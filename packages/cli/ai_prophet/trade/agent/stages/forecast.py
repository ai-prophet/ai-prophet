"""Forecast stage: estimate p_yes per market from search summaries."""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient

from ..tool_schemas import FORECAST_TOOL
from ..utils import render_time_context
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class ForecastStage(PipelineStage):
    """Pure forecasting: turn each search summary into a probability.

    Input:  search stage result (summaries per market).
    Output: ``{"forecasts": {mid: {p_yes, rationale}}}``.
    """

    def __init__(self, llm_client: LLMClient):
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
        if err := self._require_llm():
            return err
        if err := self._require_stage(previous_results, "search"):
            return err

        summaries = previous_results["search"].data.get("summaries", {})
        logger.info("Forecast stage processing %d markets", len(summaries))

        forecasts: dict[str, dict[str, Any]] = {}
        for market_id, summary in summaries.items():
            try:
                forecasts[market_id] = self._forecast(market_id, summary, tick_ctx)
            except Exception as e:
                logger.error("Forecast failed for %s: %s", market_id, e, exc_info=True)
                return self._fail(
                    f"Forecast failed for {market_id}: {e}",
                    {"forecasts": forecasts},
                )

        return self._ok({"forecasts": forecasts})

    def _forecast(
        self,
        market_id: str,
        summary: dict[str, Any],
        tick_ctx: TickContext,
    ) -> dict[str, Any]:
        candidate = tick_ctx.get_candidate(market_id)
        question = candidate.question if candidate else "Unknown market"

        time_context = ""
        market_price = ""
        resolution_criteria = ""
        if candidate:
            time_context = render_time_context(tick_ctx.tick_ts, candidate.resolution_time)
            mid = (candidate.yes_bid + candidate.yes_ask) / 2
            market_price = (
                f"Market quote (YES): bid {candidate.yes_bid:.1%}  "
                f"ask {candidate.yes_ask:.1%}  mid {mid:.1%}"
            )
            if candidate.description:
                resolution_criteria = f"Resolution criteria: {candidate.description}"

        summary_text = summary.get("summary", "No summary available")
        key_points = "\n".join(f"- {kp}" for kp in summary.get("key_points", []))
        open_questions = summary.get("open_questions", [])
        open_questions_text = (
            "\n".join(f"- {q}" for q in open_questions) if open_questions else "None identified"
        )

        system_prompt = """You are a forecaster trying to determine the probability of an event
occurring in a prediction market. Estimate the probability that this event
resolves YES. Additional research is provided below.

The market mid is one data point, not a target — match it if you genuinely
have no edge, but do not anchor on it when the evidence supports a different
view.

Justify your estimate concretely from the research; a downstream trader
will read your rationale when deciding whether and how much to trade.

Use the submit_forecast tool."""

        header = "\n".join(
            line for line in (f"Event: {question}", resolution_criteria, time_context, market_price)
            if line
        )

        user_prompt = f"""{header}

Research:
{summary_text}

Key points:
{key_points}

Open questions:
{open_questions_text}"""

        assert self.llm_client is not None  # _require_llm enforces this
        forecast = self.llm_client.generate_json(
            self._messages(system_prompt, user_prompt), tool=FORECAST_TOOL,
        )

        # Tolerate LLMs that emit percentages instead of probabilities.
        p = forecast.get("p_yes")
        if isinstance(p, (int, float)) and 1.0 < p <= 100.0:
            logger.warning("Normalizing p_yes %s -> %s for %s", p, p / 100, market_id)
            forecast["p_yes"] = p / 100

        self.validator.validate_forecast(forecast)
        logger.info("Forecast %s: p_yes=%.3f", market_id, forecast["p_yes"])
        return forecast
