"""Forecast stage: Generate probability estimates only.

This stage focuses ONLY on forecasting ability - estimating the probability
of outcomes. Trade decisions are handled separately in the action stage.
This separation enables independent evaluation of forecasting vs risk management.
"""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient, LLMMessage

from ..tool_schemas import FORECAST_TOOL
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class ForecastStage(PipelineStage):
    """Generate probability forecasts for selected markets.

    Takes search summaries and:
    1. Analyzes each market individually
    2. Generates probability estimate (p_yes) ONLY
    3. Provides rationale for the forecast
    4. Validates against forecast.schema.json

    Note: This stage does NOT make trade recommendations or sizing decisions.
    Those are handled in the action stage for observability/separation.

    Input: search stage summaries
    Output: probability forecasts per market
    """

    def __init__(self, llm_client: LLMClient):
        """Initialize forecast stage.

        Args:
            llm_client: LLM client for forecasting
        """
        super().__init__(llm_client)
        self.validator = SchemaValidator()

    @property
    def name(self) -> str:
        return "forecast"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        """Execute forecast stage.

        Args:
            tick_ctx: Current tick context
            previous_results: Must contain "search" stage result

        Returns:
            StageResult with probability forecasts per market
        """
        logger.debug("Forecast stage starting")

        if not self.llm_client:
            logger.error("Forecast stage missing LLM client")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="LLM client required for forecast stage",
            )

        # Get search summaries
        if "search" not in previous_results:
            logger.error("Forecast stage missing search results")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="Search stage result not found",
            )

        search_data = previous_results["search"].data
        summaries = search_data.get("summaries", {})

        logger.info(f"Forecast stage processing {len(summaries)} markets")

        if not summaries:
            logger.info("No summaries to forecast, returning empty result")
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"forecasts": {}},
            )

        # Generate forecasts for each market
        forecasts: dict[str, dict[str, Any]] = {}

        for idx, (market_id, summary) in enumerate(summaries.items()):
            logger.debug(f"Generating forecast {idx+1}/{len(summaries)} for {market_id}")

            try:
                # Generate probability forecast
                forecast = self._generate_forecast(market_id, summary, tick_ctx)

                # Normalize obvious LLM misformats before validation.
                if "p_yes" in forecast:
                    p = forecast["p_yes"]
                    if isinstance(p, (int, float)) and 1.0 < p <= 100.0:
                        logger.warning(f"Normalizing p_yes {p} -> {p / 100} for {market_id}")
                        forecast["p_yes"] = p / 100

                logger.debug(f"Forecast for {market_id}: p_yes={forecast['p_yes']:.3f}")

                self.validator.validate_forecast(forecast)

                forecasts[market_id] = forecast
                logger.info(f"Completed forecast for {market_id}: p_yes={forecast['p_yes']:.3f}")
            except Exception as e:
                logger.error(f"Forecast failed for {market_id}: {e}", exc_info=True)
                return StageResult(
                    stage_name=self.name,
                    success=False,
                    data={"forecasts": forecasts},
                    error=f"Forecast failed for {market_id}: {e}",
                )

        logger.info(f"Forecast stage complete: {len(forecasts)} probability forecasts generated")

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"forecasts": forecasts},
        )

    def _generate_forecast(
        self,
        market_id: str,
        summary: dict[str, Any],
        tick_ctx: TickContext,
    ) -> dict:
        """Generate probability forecast for a market using tool calling.

        This focuses purely on calibrated probability estimation.
        Trade decisions are made in the action stage.

        Args:
            market_id: Market identifier
            summary: Search summary for this market
            tick_ctx: Current tick context

        Returns:
            Forecast matching forecast.schema.json (p_yes + rationale only)
        """
        candidates = tick_ctx.candidates
        market_info = next((m for m in candidates if m.market_id == market_id), None)
        question = market_info.question if market_info else "Unknown market"

        # Get current market price for context
        market_price = ""
        if market_info:
            mid = (market_info.yes_bid + market_info.yes_ask) / 2
            market_price = f"\nCurrent market price: {mid:.1%} (the market's implied probability)"

        summary_text = summary.get("summary", "No summary available")
        key_points = "\n".join([f"- {kp}" for kp in summary.get("key_points", [])])
        open_questions = summary.get("open_questions", [])
        open_questions_text = "\n".join([f"- {q}" for q in open_questions]) if open_questions else "None identified"
        memory_by_market = getattr(tick_ctx, "memory_by_market", None) or {}
        market_memory = memory_by_market.get(market_id, "")
        memory_block = f"\n\nRECENT MEMORY:\n{market_memory}" if market_memory else ""
        logger.info(
            "Forecast prompt market_id=%s memory_in_prompt=%s memory_chars=%d",
            market_id,
            bool(memory_block),
            len(market_memory),
        )

        system_prompt = """You are an expert forecaster specialized in calibrated probability estimation.

Your ONLY task is to estimate the probability that this event resolves YES.
Do NOT make trading recommendations - just provide your honest probability estimate.

CALIBRATION GUIDELINES:
- Consider base rates: What's the typical outcome for similar events?
- Weight evidence by reliability and recency
- Account for uncertainty: don't be overconfident
- Extremes (p < 0.10 or p > 0.90) require very strong evidence
- When uncertain, probabilities closer to market price are safer

CRITICAL: RESPECT THE MARKET
- The market price reflects the consensus of many traders
- If your estimate differs by >15% from market, you need SPECIFIC facts to justify it
- Generic research (e.g. "X is a good player") does NOT justify large deviations
- Ask yourself: "What do I know that the market doesn't?"
- If you can't answer that clearly, stay close to the market price

COMMON PITFALLS TO AVOID:
- Claiming large edge without specific insider-level knowledge
- Ignoring that prediction markets are usually well-calibrated
- Being overconfident despite limited/generic information

Use the submit_forecast tool to provide your probability estimate."""

        user_prompt = f"""Event to forecast: {question}
{market_price}

RESEARCH FINDINGS:
{summary_text}

KEY POINTS:
{key_points}

OPEN QUESTIONS/UNCERTAINTIES:
{open_questions_text}

Based on this research, what is your probability estimate that this event resolves YES?
Think carefully about base rates and calibration.{memory_block}"""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        logger.debug("Calling LLM for probability forecast with tool calling")
        llm_client = self.llm_client
        if llm_client is None:
            raise RuntimeError("LLM client missing in forecast stage")
        forecast_data = llm_client.generate_json(messages, tool=FORECAST_TOOL)
        logger.debug(f"LLM returned p_yes={forecast_data.get('p_yes', 0):.3f}")

        return forecast_data
