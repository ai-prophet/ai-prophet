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
                decision = self._generate_trade_decision(
                    market_id, forecast, market_info, tick_ctx
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
        spread = yes_ask - yes_bid
        no_bid = 1.0 - yes_ask
        no_ask = 1.0 - yes_bid

        # Edge vs the price you'd actually pay to open (the ask side). Surfaced
        # pre-computed because the round-trip economics are easy to misread and
        # we don't want the LLM doing fragile arithmetic on every market.
        yes_edge = p_yes - yes_ask
        no_edge = (1 - p_yes) - no_ask

        # Volume is shown as a context signal (low-volume markets are usually
        # low-information / not seriously traded), not as a sizing cap.
        # The simulator fills at the quoted bid/ask regardless of order size —
        # there is no market impact. The real per-trade cost is the SPREAD.
        volume_24h = float(getattr(market_info, "volume_24h", 0) or 0)

        # Build context using shared utilities
        portfolio_summary = format_portfolio_summary(tick_ctx, include_positions=False)
        position_context = format_position_for_market(tick_ctx, market_id)

        # If we hold this market, append concrete exit price + proceeds so the
        # LLM doesn't have to derive them from the order book itself.
        held_position = tick_ctx.get_position(market_id)
        if held_position is not None:
            exit_price = yes_bid if held_position.side == "YES" else no_bid
            exit_proceeds = float(held_position.shares) * exit_price
            sell_action = "SELL_YES" if held_position.side == "YES" else "SELL_NO"
            position_context += (
                f"\nEXIT NOW: {sell_action} at {exit_price:.1%} → "
                f"proceeds ${exit_proceeds:,.2f} (vs cost ${float(held_position.avg_entry_price) * float(held_position.shares):,.2f})\n"
            )
        memory_by_market = getattr(tick_ctx, "memory_by_market", None) or {}
        market_memory = memory_by_market.get(market_id, "")
        memory_block = f"\n\nRECENT MEMORY:\n{market_memory}" if market_memory else ""
        logger.info(
            "Action prompt market_id=%s memory_in_prompt=%s memory_chars=%d",
            market_id,
            bool(memory_block),
            len(market_memory),
        )

        system_prompt = """You size trades in a prediction market.

Mechanics:
- BUY YES at YES ASK. SELL YES at YES BID. BUY NO at (1-YES bid). SELL NO at (1-YES ask).
- Order size doesn't affect fill price. The spread is the only per-trade cost.
- Round-trip = enter then exit = full spread per share. After entry you'll
  show a paper loss of half the spread (entry at ask, marked at mid). That's
  accounting, not a real loss.

Actions: BUY_YES, BUY_NO, SELL_YES, SELL_NO, HOLD.
SELL only valid if you hold that side. Use size_usd = current position value
to fully exit. Consider SELL when your forecast has materially moved away
from the thesis that justified the entry, or to free cash for a stronger trade.

Rule of thumb: trade if your forecast beats the ask by clearly more than the
spread. If the spread is wider than your edge, the round-trip will eat it —
HOLD instead.

Volume is a quality signal (low vol = poorly-informed market, usually wide
spread) but does not affect your fill price.

Cap position size around 8% of cash. Skip prices near 0 or 1 (limited upside).

Use the submit_trade_decision tool."""

        user_prompt = f"""Market: {question}
Your forecast: {p_yes:.1%} YES — {forecast_rationale}

YES: bid {yes_bid:.1%} / ask {yes_ask:.1%} / spread {spread:.1%}
Buy YES at {yes_ask:.1%}  (edge vs forecast: {yes_edge*100:+.1f}%)
Buy NO  at {no_ask:.1%}  (edge vs forecast: {no_edge*100:+.1f}%)
24h volume: ${volume_24h:,.0f}

{portfolio_summary}
{position_context}
Decide.{memory_block}"""

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

        if recommendation == "HOLD":
            return None

        # Determine action / side / fill price. BUY hits the ask, SELL hits the
        # bid — matches the execution engine's _compute_price exactly so the
        # LLM sees the same price it will get filled at.
        if recommendation == "BUY_YES":
            action, side = "BUY", "YES"
            price = market_info.yes_ask
        elif recommendation == "BUY_NO":
            action, side = "BUY", "NO"
            price = 1.0 - market_info.yes_bid
        elif recommendation == "SELL_YES":
            action, side = "SELL", "YES"
            price = market_info.yes_bid
        elif recommendation == "SELL_NO":
            action, side = "SELL", "NO"
            price = 1.0 - market_info.yes_ask
        else:
            return None

        if price <= 0:
            logger.warning(f"Invalid price {price} for {market_id}")
            return None

        is_sell = action == "SELL"

        # SELL hard gate: only emit if we actually hold the position to sell.
        # The engine would reject otherwise ("Cannot SELL without position"),
        # but pre-filtering avoids wasting submission round-trips and keeps
        # the trade log clean.
        if is_sell:
            position = tick_ctx.get_position(market_id)
            if position is None or position.side != side:
                logger.warning(
                    "Skipping %s for %s: no matching %s position to sell",
                    recommendation, market_id, side,
                )
                return None

        # min_size_usd is a noise filter for new BUY entries; it doesn't apply
        # to SELLs (closing out a small leftover position is always valid).
        if not is_sell and size_usd < self.min_size_usd:
            logger.debug(f"Size ${size_usd} below minimum ${self.min_size_usd}, skipping")
            return None

        shares = size_usd / price

        # Cap SELL shares to held position. Engine clamps too, but doing it
        # here keeps the submitted shares honest and prevents misleading log
        # output that suggests we're selling more than we own.
        if is_sell:
            held = float(position.shares)
            if shares > held:
                shares = held

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
