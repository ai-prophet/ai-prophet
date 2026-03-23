"""Bellwether stage: Cross-platform market data enrichment.

Pure data stage (no LLM call). Queries the Bellwether API for VWAP prices,
market depth, and reportability for each market selected by REVIEW.
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher
from typing import Any

from ai_prophet_core.bellwether_client import BellwetherAPIError, BellwetherClient

from ai_prophet.trade.core import TickContext

from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class BellwetherStage(PipelineStage):
    """Enrich selected markets with cross-platform data from Bellwether.

    For each market chosen by REVIEW:
    1. Search Bellwether by question text
    2. Fuzzy-match titles to find the best candidate
    3. Fetch event metrics (VWAP, platform prices, depth)

    Always returns ``success=True`` — failures are logged, never break the
    pipeline.  Data is surfaced to downstream stages via
    ``previous_results["bellwether"].data["enrichments"]``.
    """

    def __init__(
        self,
        bellwether_client: BellwetherClient,
        min_title_similarity: float = 0.4,
    ):
        super().__init__(llm_client=None)
        self.bellwether_client = bellwether_client
        self.min_title_similarity = min_title_similarity

    @property
    def name(self) -> str:
        return "bellwether"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        """Execute Bellwether enrichment for reviewed markets."""
        logger.debug("Bellwether stage starting")

        review_result = previous_results.get("review")
        if not review_result or not review_result.success:
            logger.info("Bellwether stage: no review results, skipping")
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"enrichments": {}},
            )

        review_items = review_result.data.get("review", [])
        if not review_items:
            logger.info("Bellwether stage: no reviewed markets, skipping")
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"enrichments": {}},
            )

        # Build market_id -> question lookup from tick context
        questions: dict[str, str] = {
            m.market_id: m.question for m in tick_ctx.candidates
        }

        enrichments: dict[str, dict[str, Any]] = {}

        for item in review_items:
            market_id = item["market_id"]
            question = questions.get(market_id, "")
            if not question:
                logger.debug("No question text for %s, skipping Bellwether lookup", market_id)
                continue

            try:
                enrichment = self._enrich_market(market_id, question)
                if enrichment:
                    enrichments[market_id] = enrichment
                    logger.info(
                        "Bellwether enrichment for %s: vwap=%s",
                        market_id,
                        enrichment.get("bellwether_price"),
                    )
            except Exception as e:
                logger.warning("Bellwether enrichment failed for %s: %s", market_id, e)

        logger.info("Bellwether stage complete: %d/%d markets enriched", len(enrichments), len(review_items))

        return StageResult(
            stage_name=self.name,
            success=True,
            data={"enrichments": enrichments},
        )

    def _enrich_market(
        self, market_id: str, question: str
    ) -> dict[str, Any] | None:
        """Search and fetch metrics for a single market.

        Returns a flat dict of enrichment data, or ``None`` if no match.
        """
        try:
            search_resp = self.bellwether_client.search_markets(question, limit=5)
        except BellwetherAPIError as e:
            logger.warning("Bellwether search failed for %s: %s", market_id, e)
            return None

        if not search_resp.results:
            logger.debug("No Bellwether search results for %s", market_id)
            return None

        # Fuzzy-match: pick the best title match above threshold
        best_match = None
        best_score = 0.0
        for result in search_resp.results:
            score = SequenceMatcher(None, question.lower(), result.title.lower()).ratio()
            if score > best_score:
                best_score = score
                best_match = result

        if best_match is None or best_score < self.min_title_similarity:
            logger.debug(
                "No Bellwether match above threshold for %s (best=%.2f)",
                market_id,
                best_score,
            )
            return None

        logger.debug(
            "Bellwether matched %s -> %s (score=%.2f)",
            market_id,
            best_match.ticker,
            best_score,
        )

        # Fetch detailed metrics
        try:
            metrics = self.bellwether_client.get_event_metrics(best_match.ticker)
        except BellwetherAPIError as e:
            logger.warning("Bellwether metrics failed for %s: %s", best_match.ticker, e)
            return None

        # Flatten into a simple dict for downstream consumption
        return {
            "ticker": metrics.ticker,
            "title": metrics.title,
            "bellwether_price": metrics.bellwether_price,
            "price_tier": metrics.price_tier,
            "price_label": metrics.price_label,
            "polymarket_price": metrics.platform_prices.polymarket,
            "kalshi_price": metrics.platform_prices.kalshi,
            "cost_to_move_5c": metrics.robustness.cost_to_move_5c,
            "reportability": metrics.robustness.reportability,
            "vwap_trade_count": metrics.vwap_details.trade_count,
            "vwap_total_volume": metrics.vwap_details.total_volume,
            "match_score": round(best_score, 3),
        }
