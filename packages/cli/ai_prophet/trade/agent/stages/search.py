"""Search stage: per-market query generation, web search, summarization.

For each market the Review stage picked:

1. Generate up to N search queries (skipped if no search client).
2. Execute the queries via ``SearchClient``.
3. Summarize the results.

Any per-market failure falls back to ``empty_search_summary`` so one bad
market never kills the tick.
"""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient
from ai_prophet.trade.llm.base import vprint
from ai_prophet.trade.search import SearchClient

from ..tool_schemas import RESEARCH_QUERIES_TOOL, SEARCH_SUMMARY_TOOL
from ..utils import empty_search_summary
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class SearchStage(PipelineStage):
    """Research the markets picked by Review.

    Input:  review stage result (selected markets).
    Output: ``{"summaries": {mid: summary}, "queries": {mid: [str, ...]}}``.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        search_client: SearchClient | None = None,
        max_queries_per_market: int = 1,
        max_results_per_query: int = 3,
    ):
        super().__init__(llm_client)
        self.search_client = search_client
        self.max_queries_per_market = max_queries_per_market
        self.max_results_per_query = max_results_per_query
        self.validator = SchemaValidator()

    @property
    def name(self) -> str:
        return "search"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        if err := self._require_stage(previous_results, "review"):
            return err

        selected = previous_results["review"].data.get("review", [])
        logger.info("Search stage processing %d markets", len(selected))

        summaries: dict[str, dict[str, Any]] = {}
        queries_by_market: dict[str, list[str]] = {}

        for market in selected:
            market_id = market["market_id"]
            candidate = tick_ctx.get_candidate(market_id)
            question = candidate.question if candidate else f"Market {market_id}"

            queries = self._generate_queries(market_id, question)
            queries_by_market[market_id] = queries
            summaries[market_id] = self._summarize(question, self._run_searches(queries))

        return self._ok({"summaries": summaries, "queries": queries_by_market})

    # -- per-market steps ---------------------------------------------------

    def _generate_queries(self, market_id: str, question: str) -> list[str]:
        """Return 1-N tailored queries, or [] when search/LLM is unavailable."""
        if self.search_client is None or self.llm_client is None:
            return []

        messages = self._messages(
            "You are the research step in a prediction-market trading agent. "
            "Downstream, an LLM forecaster will estimate the probability that "
            "this event resolves YES and an LLM trader will size a position. "
            "Generate 1-3 web search queries that will surface the evidence "
            "the forecaster needs — recent news, key facts, polling, expert "
            "analysis, base rates, anything that materially changes the "
            "probability of YES. Use the submit_research_queries tool.",
            f"Market: {question}\nMarket ID: {market_id}",
        )
        try:
            response = self.llm_client.generate_json(messages, tool=RESEARCH_QUERIES_TOOL)
        except Exception as e:
            logger.warning("Query generation failed for %s: %s", market_id, e)
            return []

        raw = response.get("queries", [])
        queries = [q.strip() for q in raw if isinstance(q, str) and q.strip()]
        return queries[: self.max_queries_per_market]

    def _run_searches(self, queries: list[str]) -> list[dict[str, Any]]:
        if not queries or self.search_client is None:
            return []

        results: list[dict[str, Any]] = []
        for query in queries:
            try:
                results.extend(
                    self.search_client.search(query=query, limit=self.max_results_per_query)
                )
            except Exception as e:
                logger.warning("Search failed for query '%s': %s", query, e)

        vprint(f"\n  Search: {queries[0][:60]}...")
        for r in results[:3]:
            vprint(f"    - {r.get('title', '')[:50]}")
        return results

    def _summarize(self, question: str, search_results: list[dict[str, Any]]) -> dict[str, Any]:
        if not search_results or self.llm_client is None:
            return empty_search_summary(
                question=question,
                reason="No external search results were retrieved for this market.",
            )

        results_text = "\n\n".join(_format_result(i, r) for i, r in enumerate(search_results))
        messages = self._messages(
            "You are the research step in a prediction-market trading agent. "
            "The next step (forecaster) will use your summary to estimate "
            "the probability that this event resolves YES. Distill the search "
            "results into the facts that materially change that probability: "
            "key developments, supporting evidence, and remaining uncertainty. "
            "Use the submit_search_summary tool.",
            f"Market question: {question}\n\nSearch results:\n{results_text}",
        )
        try:
            summary = self.llm_client.generate_json(messages, tool=SEARCH_SUMMARY_TOOL)
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            return empty_search_summary(
                question=question,
                reason="Summarization step failed; proceeding without summary.",
            )

        try:
            self.validator.validate_search(summary)
        except Exception as e:
            # LLM summaries are often slightly off-schema; tolerate but log.
            logger.warning("Search summary validation warning: %s", e)
        return summary


def _format_result(idx: int, r: dict[str, Any]) -> str:
    part = f"[{idx}] {r['title']}\n{r['snippet']}\nURL: {r['url']}"
    if r.get("text"):
        part += f"\nContent: {r['text'][:1000]}..."
    return part
