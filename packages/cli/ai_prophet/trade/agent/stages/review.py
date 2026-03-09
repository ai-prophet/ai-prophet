"""Review stage: Select markets for detailed analysis."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.llm import LLMClient, LLMMessage

from ..tool_schemas import REVIEW_TOOL
from ..utils import format_portfolio_summary
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


class ReviewStage(PipelineStage):
    """Select markets for detailed analysis.

    Takes candidate markets and:
    1. Reviews all markets in batch
    2. Selects top N for deeper analysis
    3. Generates search queries for each
    4. Validates against review.schema.json

    Input: candidate markets from TickContext
    Output: selected markets with queries
    """

    def __init__(
        self,
        llm_client: LLMClient,
        max_markets: int = 5,
    ):
        """Initialize review stage.

        Args:
            llm_client: LLM client for market selection
            max_markets: Maximum markets to select for deeper analysis
        """
        super().__init__(llm_client)
        self.max_markets = max_markets
        self.validator = SchemaValidator()

    @property
    def name(self) -> str:
        return "review"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        """Execute review stage.

        Args:
            tick_ctx: Current tick context (contains candidates)
            previous_results: Not used (first stage)

        Returns:
            StageResult with selected markets and queries
        """
        logger.debug(f"Review stage starting with {len(tick_ctx.candidates)} candidates")

        if not self.llm_client:
            logger.error("Review stage missing LLM client")
            return StageResult(
                stage_name=self.name,
                success=False,
                data={},
                error="LLM client required for review stage",
            )

        # Get candidates from tick context (would be populated by pipeline)
        candidates = tick_ctx.candidates

        if not candidates:
            logger.info("No candidates to review, returning empty result")
            # No candidates - return empty review
            return StageResult(
                stage_name=self.name,
                success=True,
                data={"review": []},
            )

        try:
            logger.debug(f"Generating review decision for {len(candidates)} candidates (max {self.max_markets})")
            # Generate review decision
            review_data = self._generate_review(candidates, tick_ctx)

            # Sanitize: strip unexpected top-level keys (some models echo schema)
            review_data = {"review": review_data.get("review", [])}

            selected_count = len(review_data.get("review", []))
            logger.info(f"Review selected {selected_count} markets for analysis")

            # Validate schema
            logger.debug("Validating review schema")
            self.validator.validate_review(review_data)

            for item in review_data.get("review", []):
                logger.debug(f"Selected market {item['market_id']}: priority={item['priority']}, "
                            f"queries={len(item['queries'])}")

            return StageResult(
                stage_name=self.name,
                success=True,
                data=review_data,
            )

        except Exception as e:
            logger.error(f"Review generation failed: {e}", exc_info=True)
            return StageResult(
                stage_name=self.name,
                success=False,
                data={"review": []},
                error=f"Review generation failed: {e}",
            )

    def _generate_review(
        self,
        candidates: Sequence[CandidateMarket],
        tick_ctx: TickContext,
    ) -> dict:
        """Generate review decision with LLM using tool calling.

        Args:
            candidates: Candidate markets
            tick_ctx: Current tick context

        Returns:
            Review decision matching review.schema.json
        """
        # Build candidate summary with bid/ask spread
        candidates_text = "\n".join([
            f"{m.market_id} | {m.question[:80]} | {m.yes_bid:.2f}/{m.yes_ask:.2f} | ${m.volume_24h:.0f}"
            for m in candidates
        ])

        # Format portfolio context
        positions_text = format_portfolio_summary(tick_ctx, include_positions=True)
        memory_summary = getattr(tick_ctx, "memory_summary", "") or ""
        memory_block = f"\n\nRECENT MEMORY:\n{memory_summary}" if memory_summary else ""
        logger.info(
            "Review prompt memory_in_prompt=%s memory_chars=%d",
            bool(memory_block),
            len(memory_summary),
        )

        system_prompt = f"""You are a prediction market analyst selecting markets for detailed analysis.

HOW PREDICTION MARKETS WORK:
- Price = probability (0.50 = 50% chance of YES)
- BUY YES at 0.40: You profit if event happens (you think >40% likely)
- BUY NO at 0.40: You profit if event doesn't happen (you think <40% likely)
- Spread between bid/ask indicates liquidity

Review ALL {len(candidates)} markets and select up to {self.max_markets} for deeper research.

GOOD REASONS TO SELECT A MARKET:
- You have domain knowledge about the topic
- Recent news/events may not be fully priced in
- The probability seems off based on base rates or logic
- High volume indicates active trading interest

SKIP markets where:
- Price is below 0.10 or above 0.90 (near resolution, limited upside)
- You have no way to research or form a view
- Question is too vague or ambiguous

Use the submit_review tool to submit your selections."""

        user_prompt = f"""Current tick: {tick_ctx.tick_ts}
Cash available: ${float(tick_ctx.cash):,.0f}
{positions_text}
All {len(candidates)} candidate markets (ID | Question | Bid/Ask | 24h Volume):
{candidates_text}

Select up to {self.max_markets} markets worth researching.{memory_block}"""

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]

        logger.debug("Calling LLM for review decision with tool calling")
        llm_client = self.llm_client
        if llm_client is None:
            raise RuntimeError("LLM client missing in review stage")
        review_data = llm_client.generate_json(messages, tool=REVIEW_TOOL)
        logger.debug(f"LLM returned review with {len(review_data.get('review', []))} selections")

        return review_data

