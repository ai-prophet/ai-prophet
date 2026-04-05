"""Search stage: Execute web searches and summarize results."""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient, LLMMessage
from ai_prophet.trade.llm.base import vprint
from ai_prophet.trade.search import SearchClient

from ..tool_schemas import SEARCH_SUMMARY_TOOL
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class SearchStage(PipelineStage):
    """Execute web searches for selected markets.

    Takes queries from review stage and:
    1. Executes web searches (via search tool)
    2. Formats results for LLM
    3. Generates cited summaries
    4. Validates against search.schema.json

    Input: review stage result (selected markets + queries)
    Output: search summaries per market
    """

    def __init__(
        self,
        llm_client: LLMClient,
        search_client: SearchClient | None = None,
        max_queries_per_market: int = 1,
        max_results_per_query: int = 3,
    ):
        """Initialize search stage.

        Args:
            llm_client: LLM client for summarization
            search_client: SearchClient instance (optional; None means search disabled)
            max_queries_per_market: Max queries per market
            max_results_per_query: Max search results per query
        """
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
        """Execute search stage.

        Args:
            tick_ctx: Current tick context
            previous_results: Must contain "review" stage result

        Returns:
            StageResult with search summaries per market
        """
        logger.debug("Search stage starting")

        # Get review results
        if "review" not in previous_results:
            logger.error("Search stage missing review results")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="Review stage result not found",
            )

        review_data = previous_results["review"].data
        selected_markets = review_data.get("review", [])

        logger.info(f"Search stage processing {len(selected_markets)} markets")

        if not selected_markets:
            logger.info("No markets selected for search, returning empty result")
            # No markets selected - return empty result
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"summaries": {}},
            )

        # Execute searches for each market
        summaries: dict[str, dict[str, Any]] = {}

        for idx, market in enumerate(selected_markets):
            market_id = market["market_id"]
            queries = market["queries"]

            # Look up the full market info from tick_ctx to get the question
            candidates = tick_ctx.candidates
            market_info = next((m for m in candidates if m.market_id == market_id), None)
            question = market_info.question if market_info else f"Market {market_id}"

            logger.debug(f"Processing market {idx+1}/{len(selected_markets)}: {market_id} "
                        f"with {len(queries)} queries")

            if self.search_client is None:
                summaries[market_id] = self._empty_search_summary(
                    question=question,
                    reason="Search disabled: no search client configured for this run.",
                )
                logger.info("Search disabled for %s, using explicit no-search summary", market_id)
                continue

            try:
                # Execute searches
                search_results = self._execute_searches(queries)

                # Verbose: show search results
                vprint(f"\n  Search: {queries[0][:60] if queries else 'no query'}...")
                for r in search_results[:3]:
                    title = r.get("title", "")[:50]
                    vprint(f"    - {title}")

                # Summarize with LLM - pass the question
                logger.debug("Summarizing search results with LLM")
                summary = self._summarize_results(market_id, question, search_results)

                # Validate schema (with flexible parsing for sources)
                try:
                    logger.debug("Validating search summary schema")
                    self.validator.validate_search(summary)
                except Exception as e:
                    # Log warning but don't fail - LLM output can be fuzzy
                    logger.warning(f"Search validation warning for {market_id}: {e}")

                summaries[market_id] = summary
                logger.info(f"Completed search for {market_id}")
            except Exception as e:
                logger.error(f"Search failed for {market_id}: {e}", exc_info=True)
                return StageResult(
                    stage_name=self.name,
                    success=False,
                    data={"summaries": summaries},
                    error=f"Search failed for {market_id}: {e}",
                )

        logger.info(f"Search stage complete: {len(summaries)} summaries generated")

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"summaries": summaries},
        )
    
    def _clean_queries(self, queries: list[str]) -> list[str]:
        """Strip, drop empty queries, and deduplicate while preserving order."""
        cleaned: list[str] = []
        seen: set[str] = set()
        for query in queries:
            if not isinstance(query, str):
                continue
            normalized = " ".join(query.split()).strip()
            if not normalized:
                continue
            dedup_key = normalized.casefold()
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            cleaned.append(normalized)
        return cleaned

    def _deduplicate_results(
    self,
    results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Deduplicate search results by URL while preserving order."""
        deduped: list[dict[str, Any]] = []
        seen_urls: set[str] = set()

        for result in results:
            if not isinstance(result, dict):
                continue

            url = result.get("url")
            if not isinstance(url, str):
                deduped.append(result)
                continue

            normalized_url = url.strip()
            if not normalized_url:
                deduped.append(result)
                continue

            dedup_key = normalized_url.casefold()
            if dedup_key in seen_urls:
                continue

            seen_urls.add(dedup_key)
            deduped.append(result)

        return deduped

    def _execute_searches(self, queries: list[str]) -> list[dict[str, Any]]:
        """Execute web searches for queries.

        Args:
            queries: List of search queries

        Returns:
            List of search results with url, title, snippet, text
        """
        # Limit queries per market (configurable via config.yaml)
        
        cleaned_queries = self._clean_queries(queries)
        queries_to_run = cleaned_queries[:self.max_queries_per_market]

        if not queries_to_run:
            logger.debug("No queries to execute")
            return []

        if self.search_client is None:
            logger.info("Search disabled: no search client configured")
            return []

        # Real search mode: Use Brave Search API
        logger.debug(f"Executing {len(queries_to_run)} search queries")
        all_results = []
        for query in queries_to_run:
            try:
                logger.debug(f"Executing search query: {query[:100]}")
                results = self.search_client.search(
                    query=query,
                    limit=self.max_results_per_query
                )
                logger.debug(f"Got {len(results)} results for query")
                all_results.extend(results)
            except Exception as e:
                logger.warning(f"Search failed for query '{query}': {e}")
                # Continue with other queries
        deduped_results = self._deduplicate_results(all_results)
        logger.debug( f"Search results deduplicated from {len(all_results)} to {len(deduped_results)}")
        return deduped_results

    def _summarize_results(
        self,
        market_id: str,
        question: str,
        search_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Summarize search results with LLM using tool calling.

        Args:
            market_id: Market identifier
            question: Market question text
            search_results: Raw search results

        Returns:
            Validated search summary (matching search.schema.json)
        """
        if not search_results:
            return self._empty_search_summary(
                question=question,
                reason="No external search results were retrieved for this market.",
            )

        # Format results with full text if available
        results_parts = []
        for i, r in enumerate(search_results):
            part = f"[{i}] {r['title']}\n{r['snippet']}\nURL: {r['url']}"
            if "text" in r and r["text"]:
                text = r["text"][:1000]
                part += f"\nContent: {text}..."
            results_parts.append(part)

        results_text = "\n\n".join(results_parts)

        system_prompt = """You are a research analyst summarizing web search results for a prediction market.

Synthesize the most relevant findings into a concise summary. Use the submit_search_summary tool to provide your analysis."""

        user_prompt = f"""Market question: {question}

Search results:
{results_text}

Summarize the key findings relevant to forecasting this market."""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        if self.llm_client:
            logger.debug(f"Calling LLM to summarize {len(search_results)} search results")
            summary = self.llm_client.generate_json(messages, tool=SEARCH_SUMMARY_TOOL)
            logger.debug(f"LLM returned summary with {len(summary.get('key_points', []))} key points")
            return summary

        return self._empty_search_summary(
            question=question,
            reason="LLM unavailable for summarization.",
        )

    def _empty_search_summary(self, question: str, reason: str) -> dict[str, Any]:
        question_snippet = question[:180]
        return {
            "schema_version": "v1",
            "summary": (
                f"No external web evidence was retrieved for '{question_snippet}'. "
                "Forecasting proceeds without fresh search data."
            ),
            "key_points": [],
            "open_questions": [reason],
        }

