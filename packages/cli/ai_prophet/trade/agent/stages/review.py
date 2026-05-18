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


def _liquidity_tier(spread: float, volume_24h: float) -> str:
    """Classify a market by how tradeable it is.

    Combines bid-ask spread (as a fraction of mid, approximated by spread alone
    since most markets sit in 20-80¢ range) with 24h volume. The tiers are
    deliberately coarse — the LLM uses them as a fast filter, not a precise
    cost model.
    """
    if volume_24h < 500 or spread >= 0.10:
        return "ILLIQUID"
    if spread < 0.03 and volume_24h > 5000:
        return "TIGHT"
    if spread < 0.05 and volume_24h > 1000:
        return "OK"
    return "WIDE"


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
        # Build candidate summary with derived spread + liquidity tier so the
        # LLM doesn't have to scan 256 rows and do mental arithmetic on each.
        # `tier` collapses bid-ask + volume into a single "is this tradeable
        # at all" signal — most of the LLM's mistakes here were on illiquid
        # novelty markets (e.g. $1k/day, 20¢ spread) where any edge is eaten.
        def _row(m: CandidateMarket) -> str:
            mid = (m.yes_bid + m.yes_ask) / 2
            spread = m.yes_ask - m.yes_bid
            sp_pct = (spread / mid * 100) if mid > 0 else 0
            tier = _liquidity_tier(spread, m.volume_24h)
            return (
                f"[{tier:>8}] {m.market_id} | {m.question[:70]} | "
                f"{m.yes_bid:.2f}/{m.yes_ask:.2f} sp={spread:.2f}({sp_pct:.0f}%) | "
                f"vol ${m.volume_24h:,.0f}"
            )

        candidates_text = "\n".join([_row(m) for m in candidates])

        # Format portfolio context
        positions_text = format_portfolio_summary(tick_ctx, include_positions=True)
        memory_summary = getattr(tick_ctx, "memory_summary", "") or ""
        memory_block = f"\n\nRECENT MEMORY:\n{memory_summary}" if memory_summary else ""
        logger.info(
            "Review prompt memory_in_prompt=%s memory_chars=%d",
            bool(memory_block),
            len(memory_summary),
        )

        system_prompt = f"""Pick up to {self.max_markets} markets from the candidate list to research.

Each row: [tier] market_id | question | bid/ask sp=(spread, % of mid) | volume

Tiers reflect the round-trip cost (spread) you'd pay to enter then exit:
  TIGHT     <3¢ spread, vol >$5k       cheap to trade
  OK        <5¢ spread, vol >$1k       normal
  WIDE      <10¢ spread                expensive — only if your edge beats the spread
  ILLIQUID  >=10¢ spread OR very low vol   skip — exiting will lose money

Pick markets where you have specific knowledge, recent events may not be
priced in, or the probability looks off vs base rates. Skip ILLIQUID rows,
vague questions, and prices near 0 or 1 (limited upside).

Use the submit_review tool."""

        user_prompt = f"""Tick: {tick_ctx.tick_ts}  Cash: ${float(tick_ctx.cash):,.0f}
{positions_text}

{len(candidates)} candidates:
{candidates_text}{memory_block}"""

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

