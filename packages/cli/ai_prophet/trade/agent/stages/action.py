"""Action stage: Generate trade decisions from probability forecasts.

This stage takes probability forecasts and makes SEPARATE trade decisions.
This separation from the forecast stage enables independent evaluation of:
- Forecasting ability (Stage 3): How well does the model estimate probabilities?
- Risk management (Stage 4): How well does the model size trades?
"""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.llm import LLMClient, LLMMessage

from ..tool_schemas import TRADE_DECISION_TOOL
from ..utils import format_portfolio_summary, format_position_for_market
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class ActionStage(PipelineStage):
    """Convert probability forecasts into trade decisions via LLM.

    Takes forecasts and portfolio context, then:
    1. For each forecast, calls the LLM to decide on a trade
    2. LLM sees forecast probability, market price, and portfolio
    3. LLM outputs recommendation and size_usd
    4. Converts to TradeIntentRequest objects

    This is a SEPARATE LLM call from forecasting for observability:
    - Forecast stage: measures forecasting ability
    - Action stage: measures risk management / trading ability

    Input: forecast stage results (probability only)
    Output: list of TradeIntentRequest objects
    """

    def __init__(self, llm_client: LLMClient | None = None, min_size_usd: float = 1.0):
        """Initialize action stage.

        Args:
            llm_client: LLM client for trade decisions
            min_size_usd: Minimum dollar size to generate an intent (filters noise)
        """
        super().__init__(llm_client=llm_client)
        self.min_size_usd = min_size_usd
        self.validator = SchemaValidator()

    @property
    def name(self) -> str:
        return "action"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        """Execute action stage.

        Args:
            tick_ctx: Current tick context
            previous_results: Must contain "forecast" stage result

        Returns:
            StageResult with trade intents
        """
        logger.debug("Action stage starting")

        if not self.llm_client:
            logger.error("Action stage missing LLM client")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="LLM client required for action stage",
            )

        # Get forecasts
        if "forecast" not in previous_results:
            logger.error("Action stage missing forecast results")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="Forecast stage result not found",
            )

        forecast_data = previous_results["forecast"].data
        forecasts = forecast_data.get("forecasts", {})

        # Collect Bellwether enrichments if available
        bellwether_enrichments: dict[str, Any] = {}
        bellwether_result = previous_results.get("bellwether")
        if bellwether_result and bellwether_result.success:
            bellwether_enrichments = bellwether_result.data.get("enrichments", {})

        logger.info(f"Action stage processing {len(forecasts)} forecasts")

        if not forecasts:
            logger.info("No forecasts to convert to actions, returning empty result")
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"intents": [], "decisions": {}},
            )

        # Generate trade decisions for each forecast
        intents: list[dict[str, Any]] = []
        decisions: dict[str, dict[str, Any]] = {}

        for idx, (market_id, forecast) in enumerate(forecasts.items()):
            logger.debug(f"Processing forecast {idx+1}/{len(forecasts)} for {market_id}")

            try:
                # Get market info
                candidates = tick_ctx.candidates
                market_info = next((m for m in candidates if m.market_id == market_id), None)

                if not market_info:
                    logger.warning(f"Market {market_id} not found in tick context candidates")
                    continue

                # Call LLM for trade decision
                bw_data = bellwether_enrichments.get(market_id)
                decision = self._generate_trade_decision(
                    market_id, forecast, market_info, tick_ctx,
                    bellwether_data=bw_data,
                )
                decisions[market_id] = decision

                # Convert decision to intent if actionable
                intent = self._convert_to_intent(market_id, decision, market_info, tick_ctx)
                if intent:
                    logger.info(f"Generated intent for {market_id}: {intent['action']} {intent['side']} "
                               f"${decision.get('size_usd', 0):.0f}")
                    intents.append(intent)
                else:
                    logger.debug(f"No intent for {market_id} (HOLD or size below min)")
            except Exception as e:
                logger.error(f"Trade decision failed for {market_id}: {e}", exc_info=True)
                return StageResult(
                    stage_name=self.name,
                    success=False,
                    data={"intents": intents, "decisions": decisions},
                    error=f"Trade decision failed for {market_id}: {e}",
                )

        logger.info(f"Action stage complete: {len(intents)} intents from {len(forecasts)} forecasts")

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"intents": intents, "decisions": decisions},
        )

    def _generate_trade_decision(
        self,
        market_id: str,
        forecast: dict[str, Any],
        market_info: CandidateMarket,
        tick_ctx: TickContext,
        bellwether_data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate trade decision for a market using LLM with tool calling.

        This is a SEPARATE call from forecasting for observability.
        Includes position P&L context when the agent holds this market.

        Args:
            market_id: Market identifier
            forecast: Probability forecast from forecast stage
            market_info: Market data (prices, etc.)
            tick_ctx: Current tick context

        Returns:
            Trade decision matching trade_decision.schema.json
        """
        p_yes = forecast.get("p_yes", 0.5)
        forecast_rationale = forecast.get("rationale", "No rationale provided")

        question = market_info.question
        yes_bid = market_info.yes_bid
        yes_ask = market_info.yes_ask

        # Build context using shared utilities
        portfolio_summary = format_portfolio_summary(tick_ctx, include_positions=False)
        position_context = format_position_for_market(tick_ctx, market_id)
        # Build market depth context from Bellwether
        bellwether_block = ""
        if bellwether_data:
            lines = ["MARKET DEPTH (Bellwether):"]
            cost = bellwether_data.get("cost_to_move_5c")
            if cost is not None:
                lines.append(f"- Cost to move price 5c: ${cost:,.0f}")
            reportability = bellwether_data.get("reportability")
            if reportability:
                lines.append(f"- Reportability: {reportability}")
            poly = bellwether_data.get("polymarket_price")
            kalshi = bellwether_data.get("kalshi_price")
            if poly is not None or kalshi is not None:
                parts = []
                if poly is not None:
                    parts.append(f"Polymarket: {poly:.0%}")
                if kalshi is not None:
                    parts.append(f"Kalshi: {kalshi:.0%}")
                lines.append(f"- Cross-platform prices: {', '.join(parts)}")
            if len(lines) > 1:
                bellwether_block = "\n" + "\n".join(lines) + "\n"

        memory_by_market = getattr(tick_ctx, "memory_by_market", None) or {}
        market_memory = memory_by_market.get(market_id, "")
        memory_block = f"\n\nRECENT MEMORY:\n{market_memory}" if market_memory else ""
        logger.info(
            "Action prompt market_id=%s memory_in_prompt=%s memory_chars=%d bellwether=%s",
            market_id,
            bool(memory_block),
            len(market_memory),
            bool(bellwether_block),
        )

        system_prompt = """You are a trader making position sizing decisions for prediction markets.

HOW IT WORKS:
- Price = probability (0.50 = 50% chance)
- BUY YES if you think the event is more likely than the price suggests
- BUY NO if you think the event is less likely than the price suggests
- HOLD if you don't see a clear opportunity

CONSTRAINTS:
- Positions above 8% of cash are high risk
- Prices above 90% or below 10% have limited upside (near resolution)

Use the submit_trade_decision tool to provide your decision."""

        user_prompt = f"""Market: {question}

YOUR FORECAST: {p_yes:.0%} probability of YES
Rationale: {forecast_rationale}

MARKET PRICE: {yes_ask:.0%} to buy YES, {1 - yes_bid:.0%} to buy NO

{portfolio_summary}
{position_context}
{bellwether_block}
What is your trade decision?{memory_block}"""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        logger.debug(f"Calling LLM for trade decision (p_yes={p_yes:.3f}, market={yes_ask:.3f})")
        llm_client = self.llm_client
        if llm_client is None:
            raise RuntimeError("LLM client missing in action stage")
        decision_data = llm_client.generate_json(messages, tool=TRADE_DECISION_TOOL)

        self.validator.validate_trade_decision(decision_data)

        logger.debug(f"LLM trade decision: rec={decision_data.get('recommendation')}, "
                    f"size=${decision_data.get('size_usd', 0):.0f}")

        return decision_data


    def _convert_to_intent(
        self,
        market_id: str,
        decision: dict[str, Any],
        market_info: CandidateMarket,
        tick_ctx: TickContext,
    ) -> dict[str, Any] | None:
        """Convert trade decision to intent format.

        Args:
            market_id: Market identifier
            decision: Trade decision from LLM
            market_info: Market data
            tick_ctx: Current tick context

        Returns:
            Trade intent dict or None if no trade
        """
        recommendation = decision.get("recommendation", "HOLD")
        size_usd = decision.get("size_usd", 0)

        # Skip HOLD recommendations
        if recommendation == "HOLD":
            return None

        # Skip if size is below minimum
        if size_usd < self.min_size_usd:
            logger.debug(f"Size ${size_usd} below minimum ${self.min_size_usd}, skipping")
            return None

        # Determine action and side based on recommendation
        if recommendation == "BUY_YES":
            action = "BUY"
            side = "YES"
            price = market_info.yes_ask
        elif recommendation == "BUY_NO":
            action = "BUY"
            side = "NO"
            price = 1.0 - market_info.yes_bid
        else:
            # HOLD or unknown
            return None

        # Convert size_usd to shares
        if price <= 0:
            logger.warning(f"Invalid price {price} for {market_id}")
            return None

        shares = size_usd / price

        logger.debug(f"Final intent: {action} {side} {shares:.2f} shares (${size_usd} / ${price:.3f})")

        # Get market question for display
        question = getattr(market_info, "question", None) or market_id

        return {
            "run_id": tick_ctx.run_id,
            "tick_ts": tick_ctx.tick_ts,
            "market_id": market_id,
            "question": question,
            "action": action,
            "side": side,
            "shares": f"{shares:.2f}",
            "rationale": decision.get("rationale", ""),
        }
