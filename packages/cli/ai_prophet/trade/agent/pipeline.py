"""Agent pipeline orchestrator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ai_prophet_core.client import ServerAPIClient

from ai_prophet.trade.core import EventStore, TickContext, TickState
from ai_prophet.trade.core.config import ClientConfig
from ai_prophet.trade.llm import LLMClient
from ai_prophet.trade.llm.base import vprint
from ai_prophet.trade.search import SearchClient

from .stages import (
    ActionStage,
    ForecastStage,
    PipelineStage,
    ReviewStage,
    SearchStage,
    StageResult,
)

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Output of a pipeline execution.

    Always contains intents. Optionally contains per-stage reasoning
    (controlled by publish_reasoning flag on execute).
    """
    intents: list[dict[str, Any]]
    reasoning: dict[str, Any] | None = None


class AgentPipeline:
    """Orchestrates the 4-stage agent pipeline.

    Pipeline flow:
    1. REVIEW: Select markets for analysis
    2. SEARCH: Execute web searches and summarize
    3. FORECAST: Generate probability estimates
    4. ACTION: Convert forecasts to trade intents

    Features:
    - Logs all stages to EventStore
    - Handles stage failures gracefully
    - Fetches data from ServerAPIClient

    Example:
        pipeline = AgentPipeline(
            llm_client=llm_client,
            event_store=event_store,
            api_client=api_client,
        )

        result = pipeline.execute(tick_ctx, run_id)
        intents = result.intents
    """

    def __init__(
        self,
        llm_client: LLMClient,
        event_store: EventStore | None,
        api_client: ServerAPIClient,
        config: dict[str, Any] | None = None,
        client_config: ClientConfig | None = None,
    ):
        """Initialize agent pipeline.

        Args:
            llm_client: LLM client for stages
            event_store: EventStore for logging
            api_client: API client for fetching data
            config: Configuration overrides for stages
            client_config: Explicit runtime config for stage defaults
        """
        self.llm_client = llm_client
        self.event_store = event_store
        self.api_client = api_client
        self.config = config or {}
        self.search_client: SearchClient | None = None

        runtime_config = client_config or ClientConfig.get()

        logger.info("Initializing agent pipeline")

        # Get search client from config (None = mock mode)
        search_client: SearchClient | None = self.config.get("search_client")
        self.search_client = search_client
        if search_client:
            logger.info("Using real search (Brave API)")
        else:
            logger.info("Search disabled (no search_client provided)")

        # Use explicit runtime config as the only default source.
        max_markets = self.config.get("max_markets", runtime_config.pipeline.max_markets)
        max_queries = self.config.get("max_queries_per_market", runtime_config.search.max_queries_per_market)
        max_results = self.config.get("max_results_per_query", runtime_config.search.max_results_per_query)
        min_size = self.config.get("min_size_usd", runtime_config.pipeline.min_size_usd)

        logger.debug(f"Pipeline config: max_markets={max_markets}, min_size=${min_size}, "
                     f"search={max_queries}q×{max_results}r")

        # Optional callback fired after forecast stage with per-market results
        self.on_forecast: Callable[..., None] | None = self.config.get("on_forecast")

        # Initialize stages
        if runtime_config.mini_prophet.enabled:
            from ai_prophet.trade.agent.mini_prophet import MiniProphetForecastStage

            logger.info("Using mini-prophet agentic forecasting (Search+Forecast replaced)")
            self.stages: list[PipelineStage] = [
                ReviewStage(
                    llm_client=llm_client,
                    max_markets=max_markets,
                ),
                MiniProphetForecastStage(
                    llm_client=llm_client,
                    config=runtime_config.mini_prophet,
                ),
                ActionStage(
                    llm_client=llm_client,
                    min_size_usd=min_size,
                ),
            ]
        else:
            self.stages: list[PipelineStage] = [
                ReviewStage(
                    llm_client=llm_client,
                    max_markets=max_markets,
                ),
                SearchStage(
                    llm_client=llm_client,
                    search_client=search_client,
                    max_queries_per_market=max_queries,
                    max_results_per_query=max_results,
                ),
                ForecastStage(
                    llm_client=llm_client,
                ),
                ActionStage(
                    llm_client=llm_client,
                    min_size_usd=min_size,
                ),
            ]
        logger.debug(f"Initialized {len(self.stages)} pipeline stages: {[s.name for s in self.stages]}")

    def execute(
        self,
        tick_ctx: TickContext,
        run_id: str,
        on_stage_start: Callable[[str, int, int], None] | None = None,
        publish_reasoning: bool = False,
    ) -> PipelineResult:
        """Execute full pipeline for a tick.

        Args:
            tick_ctx: Current tick context
            run_id: Run identifier
            on_stage_start: Optional callback
            publish_reasoning: If True, include per-stage reasoning in result

        Returns:
            PipelineResult with intents and optional reasoning
        """
        logger.info(f"Pipeline execution started for tick {tick_ctx.tick_ts}")
        logger.debug(f"Tick context: run_id={run_id}, candidates={len(tick_ctx.candidates)}, "
                     f"cash={tick_ctx.cash}, positions={len(tick_ctx.positions)}")

        if not tick_ctx.candidates:
            raise PipelineError("TickContext must be created with candidates already populated")

        # Log tick start
        if self.event_store:
            self.event_store.write_tick_start(
                tick_ts=tick_ctx.tick_ts,
                state=TickState.INITIALIZING,
            )

        # Execute stages sequentially
        stage_results: dict[str, StageResult] = {}

        for stage_idx, stage in enumerate(self.stages):
            vprint(f"\n{'#'*60}\n# STAGE {stage_idx+1}/{len(self.stages)}: {stage.name.upper()}\n{'#'*60}")

            # Call progress callback if provided
            if on_stage_start:
                on_stage_start(stage.name, stage_idx + 1, len(self.stages))

            try:
                # Execute stage
                result = stage.execute(tick_ctx, stage_results)

                vprint(f"\n[{stage.name.upper()} DONE]")

                # Log stage result
                self._log_stage_result(stage.name, result, tick_ctx)

                # Fire on_forecast callback after forecast stage
                # Wrapped in its own try/except — live betting will never break the pipeline
                if stage.name == "forecast" and callable(self.on_forecast) and result.success:
                    try:
                        self._fire_forecast_hook(result, tick_ctx)
                    except Exception as hook_err:
                        logger.warning(f"on_forecast hook failed (non-fatal): {hook_err}")

                # Store result
                stage_results[stage.name] = result

                # Stop if stage failed critically
                if not result.success:
                    logger.error(f"Stage {stage.name} failed: {result.error}")
                    raise PipelineError(f"Stage '{stage.name}' failed: {result.error}")

            except Exception as e:
                logger.error(f"Stage {stage.name} raised exception: {e}", exc_info=True)
                raise PipelineError(f"Stage '{stage.name}' raised exception: {e}") from e

        # Extract trade intents from action stage
        action_result = stage_results.get("action")
        if not action_result or not action_result.success:
            intents = []
            logger.info("No trade intents generated")
        else:
            intents = action_result.data.get("intents", [])
            logger.info(f"Generated {len(intents)} trade intents")
            for i, intent in enumerate(intents):
                logger.debug(f"Intent {i+1}: {intent['action']} {intent['side']} "
                            f"{intent['shares']} shares of {intent['market_id']}")

        # Log tick completion
        if self.event_store:
            self.event_store.write_tick_complete(tick_ts=tick_ctx.tick_ts)

        logger.info(f"Pipeline execution complete: {len(intents)} intents")

        reasoning = None
        if publish_reasoning:
            reasoning = _extract_reasoning(stage_results, tick_ctx)

        return PipelineResult(intents=intents, reasoning=reasoning)

    def close(self) -> None:
        """Release any underlying client resources (HTTP pools, etc)."""
        # Best-effort: these may be mocks in tests.
        try:
            close = getattr(self.api_client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
        try:
            close = getattr(self.llm_client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
        try:
            close = getattr(self.search_client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass

    def _fire_forecast_hook(
        self,
        result: StageResult,
        tick_ctx: TickContext,
    ) -> None:
        """Fire the on_forecast callback for each forecasted market.

        The callback receives (market_id, p_yes, yes_ask, no_ask, question)
        for each market with a forecast. Errors are logged but never
        propagate — live betting must not break the pipeline.
        """
        callback = self.on_forecast
        if callback is None:
            return
        forecasts = result.data.get("forecasts", {})
        for market_id, forecast in forecasts.items():
            market_info = tick_ctx.get_candidate(market_id)
            if not market_info:
                continue
            try:
                callback(
                    tick_ts=tick_ctx.tick_ts,
                    market_id=market_id,
                    p_yes=forecast["p_yes"],
                    yes_ask=market_info.yes_ask,
                    no_ask=market_info.no_ask,
                    question=market_info.question,
                )
            except Exception as e:
                logger.warning(f"on_forecast hook error for {market_id}: {e}")

    def _log_stage_result(
        self,
        stage_name: str,
        result: StageResult,
        tick_ctx: TickContext,
    ):
        """Log stage result to EventStore."""
        if not self.event_store:
            return
        logger.debug(f"Logging stage result for {stage_name} to EventStore")

        # Map stage names to EventStore methods
        if stage_name == "review":
            # Log each selected market individually
            review_items = result.data.get("review", [])
            logger.debug(f"Writing {len(review_items)} review decisions to EventStore")
            for item in review_items:
                self.event_store.write_review_decision(
                    tick_ts=tick_ctx.tick_ts,
                    market_id=item["market_id"],
                    priority=item["priority"],
                    queries=item["queries"],
                    rationale=item["rationale"]
                )

        elif stage_name == "search":
            # Log search results per market
            summaries = result.data.get("summaries", {})
            logger.debug(f"Writing {len(summaries)} search summaries to EventStore")
            for market_id, summary in summaries.items():
                # Log the summary as a search result
                self.event_store.write_search_result(
                    tick_ts=tick_ctx.tick_ts,
                    market_id=market_id,
                    query_idx=0,
                    query="combined",
                    summary=summary.get("summary", ""),
                    urls=[],
                    error=None
                )

        elif stage_name == "forecast":
            # Log each probability forecast individually
            # Note: Forecasts now only contain p_yes + rationale (no trade decision)
            forecasts = result.data.get("forecasts", {})
            logger.debug(f"Writing {len(forecasts)} probability forecasts to EventStore")
            # Build market_id -> question lookup
            questions = {m.market_id: m.question for m in tick_ctx.candidates}
            for market_id, forecast in forecasts.items():
                self.event_store.write_forecast(
                    tick_ts=tick_ctx.tick_ts,
                    market_id=market_id,
                    p_yes=forecast["p_yes"],
                    rationale=forecast["rationale"],
                    question=questions.get(market_id)
                )

        elif stage_name == "action":
            # Log trade decisions and intents separately
            # This enables independent analysis of forecasting vs risk management
            decisions = result.data.get("decisions", {})
            result.data.get("intents", [])

            # Build market_id -> question lookup
            questions = {m.market_id: m.question for m in tick_ctx.candidates}

            # Log LLM trade decisions
            logger.debug(f"Writing {len(decisions)} trade decisions to EventStore")
            for market_id, decision in decisions.items():
                self.event_store.write_trade_decision(
                    tick_ts=tick_ctx.tick_ts,
                    market_id=market_id,
                    recommendation=decision.get("recommendation", "HOLD"),
                    size_usd=decision.get("size_usd", 0),
                    rationale=decision.get("rationale", ""),
                    question=questions.get(market_id)
                )


class PipelineError(Exception):
    """Pipeline execution error."""
    pass


def _extract_reasoning(
    stage_results: dict[str, StageResult],
    tick_ctx: TickContext,
) -> dict[str, Any]:
    """Build a compact reasoning dict from stage results.

    Designed to be stored in plan_json["reasoning"] for experiments
    that opt in. Keeps it bounded -- no full LLM prompts, just
    the structured outputs each stage produced.
    """
    questions = {m.market_id: m.question for m in tick_ctx.candidates}
    reasoning: dict[str, Any] = {}

    # Snapshot of what the model saw: market_id, question, yes_mark, volume.
    reasoning["candidates"] = [
        {
            "market_id": m.market_id,
            "question": m.question,
            "yes_mark": round(m.yes_mark, 4),
            "volume_24h": m.volume_24h,
        }
        for m in tick_ctx.candidates
    ]

    review = stage_results.get("review")
    if review and review.success:
        reasoning["review"] = review.data.get("review", [])

    search = stage_results.get("search")
    if search and search.success:
        reasoning["search"] = {
            mid: {"summary": s.get("summary", "")}
            for mid, s in search.data.get("summaries", {}).items()
        }

    forecast = stage_results.get("forecast")
    if forecast and forecast.success:
        reasoning["forecasts"] = {
            mid: {
                "question": questions.get(mid),
                "p_yes": f.get("p_yes"),
                "rationale": f.get("rationale"),
            }
            for mid, f in forecast.data.get("forecasts", {}).items()
        }

    action = stage_results.get("action")
    if action and action.success:
        reasoning["decisions"] = {
            mid: {
                "question": questions.get(mid),
                "recommendation": d.get("recommendation"),
                "size_usd": d.get("size_usd"),
                "rationale": d.get("rationale"),
            }
            for mid, d in action.data.get("decisions", {}).items()
        }

    return reasoning
