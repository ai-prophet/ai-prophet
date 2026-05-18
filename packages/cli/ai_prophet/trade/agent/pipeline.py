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
from .utils import candidate_questions

logger = logging.getLogger(__name__)


@dataclass
class PipelineResult:
    """Output of a pipeline execution.

    ``forecasts`` contains the successful forecast-stage output so callers can
    trigger side effects, such as betting, without re-running the stage.
    """
    intents: list[dict[str, Any]]
    forecasts: dict[str, dict[str, Any]] | None = None
    reasoning: dict[str, Any] | None = None


class AgentPipeline:
    """Orchestrates the 4-stage agent pipeline.

    Pipeline flow:
    1. REVIEW:   Select markets for analysis
    2. SEARCH:   Per-market query generation, web search, summarization
    3. FORECAST: Estimate p_yes from each summary
    4. ACTION:   Convert forecasts to trade intents

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

        runtime_config = client_config or ClientConfig.get()
        search_client: SearchClient | None = self.config.get("search_client")
        self.search_client = search_client
        logger.info(
            "Initializing agent pipeline (search=%s)",
            "enabled" if search_client else "disabled",
        )

        # Use explicit runtime config as the only default source.
        max_markets = self.config.get("max_markets", runtime_config.pipeline.max_markets)
        max_queries = self.config.get("max_queries_per_market", runtime_config.search.max_queries_per_market)
        max_results = self.config.get("max_results_per_query", runtime_config.search.max_results_per_query)
        min_size = self.config.get("min_size_usd", runtime_config.pipeline.min_size_usd)

        logger.debug(f"Pipeline config: max_markets={max_markets}, min_size=${min_size}, "
                     f"search={max_queries}q×{max_results}r")

        # Initialize stages
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
            PipelineResult with intents, forecast-stage output, and optional
            reasoning.
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

                # Store result
                stage_results[stage.name] = result

                # Stop if stage failed critically
                if not result.success:
                    logger.error(f"Stage {stage.name} failed: {result.error}")
                    raise PipelineError(
                        f"Stage '{stage.name}' failed: {result.error}",
                        stage_name=stage.name,
                        forecasts=_extract_forecasts(stage_results),
                    )

            except PipelineError:
                raise
            except Exception as e:
                logger.error(f"Stage {stage.name} raised exception: {e}", exc_info=True)
                raise PipelineError(
                    f"Stage '{stage.name}' raised exception: {e}",
                    stage_name=stage.name,
                    forecasts=_extract_forecasts(stage_results),
                ) from e

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

        forecasts = _extract_forecasts(stage_results)

        reasoning = None
        if publish_reasoning:
            reasoning = _extract_reasoning(stage_results, tick_ctx)

        return PipelineResult(intents=intents, forecasts=forecasts, reasoning=reasoning)

    def close(self) -> None:
        """Release any underlying client resources (HTTP pools, etc).

        Best-effort: these may be mocks or already-closed clients in tests.
        """
        for client in (self.api_client, self.llm_client, self.search_client):
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass

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

        tick_ts = tick_ctx.tick_ts
        store = self.event_store

        if stage_name == "review":
            # `queries` no longer live in review payloads but the event
            # schema still expects a (possibly empty) list.
            for item in result.data.get("review", []):
                store.write_review_decision(
                    tick_ts=tick_ts,
                    market_id=item["market_id"],
                    priority=item["priority"],
                    queries=item.get("queries", []),
                    rationale=item["rationale"],
                )

        elif stage_name == "search":
            for market_id, queries in result.data.get("queries", {}).items():
                for idx, query in enumerate(queries):
                    store.write_search_query(
                        tick_ts=tick_ts, market_id=market_id,
                        query_idx=idx, query=query,
                    )
            for market_id, summary in result.data.get("summaries", {}).items():
                store.write_search_result(
                    tick_ts=tick_ts, market_id=market_id,
                    query_idx=0, query="combined",
                    summary=summary.get("summary", ""),
                    urls=[], error=None,
                )

        elif stage_name == "forecast":
            questions = candidate_questions(tick_ctx)
            for market_id, forecast in result.data.get("forecasts", {}).items():
                store.write_forecast(
                    tick_ts=tick_ts, market_id=market_id,
                    p_yes=forecast["p_yes"],
                    rationale=forecast["rationale"],
                    question=questions.get(market_id),
                )

        elif stage_name == "action":
            questions = candidate_questions(tick_ctx)
            for market_id, decision in result.data.get("decisions", {}).items():
                store.write_trade_decision(
                    tick_ts=tick_ts, market_id=market_id,
                    recommendation=decision.get("recommendation", "HOLD"),
                    size_usd=decision.get("size_usd", 0),
                    rationale=decision.get("rationale", ""),
                    question=questions.get(market_id),
                )


class PipelineError(Exception):
    """Pipeline execution error with any completed forecast-stage output."""

    def __init__(
        self,
        message: str,
        *,
        stage_name: str | None = None,
        forecasts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.forecasts = forecasts


def _extract_forecasts(
    stage_results: dict[str, StageResult],
) -> dict[str, dict[str, Any]] | None:
    forecast_result = stage_results.get("forecast")
    if not forecast_result or not forecast_result.success:
        return None
    forecasts = forecast_result.data.get("forecasts")
    return forecasts or None


def _extract_reasoning(
    stage_results: dict[str, StageResult],
    tick_ctx: TickContext,
) -> dict[str, Any]:
    """Compact, bounded reasoning dict for ``plan_json["reasoning"]``.

    Only includes the structured stage outputs — no raw LLM prompts.
    """
    questions = candidate_questions(tick_ctx)
    reasoning: dict[str, Any] = {
        "candidates": [
            {
                "market_id": m.market_id,
                "question": m.question,
                "yes_mark": round(m.yes_mark, 4),
                "volume_24h": m.volume_24h,
            }
            for m in tick_ctx.candidates
        ],
    }

    if (review := _stage_data(stage_results, "review")) is not None:
        reasoning["review"] = review.get("review", [])

    if (search := _stage_data(stage_results, "search")) is not None:
        reasoning["search"] = {
            mid: {"summary": s.get("summary", "")}
            for mid, s in search.get("summaries", {}).items()
        }

    if (forecast := _stage_data(stage_results, "forecast")) is not None:
        reasoning["forecasts"] = {
            mid: {
                "question": questions.get(mid),
                "p_yes": f.get("p_yes"),
                "rationale": f.get("rationale"),
            }
            for mid, f in forecast.get("forecasts", {}).items()
        }

    if (action := _stage_data(stage_results, "action")) is not None:
        reasoning["decisions"] = {
            mid: {
                "question": questions.get(mid),
                "recommendation": d.get("recommendation"),
                "size_usd": d.get("size_usd"),
                "rationale": d.get("rationale"),
            }
            for mid, d in action.get("decisions", {}).items()
        }

    return reasoning


def _stage_data(
    stage_results: dict[str, StageResult], stage_name: str,
) -> dict[str, Any] | None:
    result = stage_results.get(stage_name)
    return result.data if result and result.success else None
