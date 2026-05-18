"""Review stage: pick the top-N markets to research this tick."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import datetime

from ai_prophet_core.ruleset import TICK_INTERVAL_SECONDS

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.llm import LLMClient

from ..tool_schemas import REVIEW_TOOL
from ..utils import days_until_resolution, render_portfolio
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

_TICK_INTERVAL_MIN = TICK_INTERVAL_SECONDS // 60

logger = logging.getLogger(__name__)


class ReviewStage(PipelineStage):
    """Score candidate markets and return the top ``max_markets``.

    Input:  ``tick_ctx.candidates``.
    Output: ``{"review": [{market_id, priority, rationale}, ...]}``.
    """

    def __init__(self, llm_client: LLMClient, max_markets: int = 5):
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
        if err := self._require_llm():
            return err

        candidates = tick_ctx.candidates
        if not candidates:
            return self._ok({"review": []})

        try:
            review_data = self._generate_review(candidates, tick_ctx)
        except Exception as e:
            logger.error("Review generation failed: %s", e, exc_info=True)
            return self._fail(f"Review generation failed: {e}", {"review": []})

        # Some models echo extra top-level keys; keep only what we expect.
        review_data = {"review": review_data.get("review", [])}
        self.validator.validate_review(review_data)
        logger.info("Review selected %d markets", len(review_data["review"]))
        return self._ok(review_data)

    def _generate_review(
        self,
        candidates: Sequence[CandidateMarket],
        tick_ctx: TickContext,
    ) -> dict:
        candidates_text = "\n".join(_row(m, tick_ctx.tick_ts) for m in candidates)
        portfolio = render_portfolio(tick_ctx)

        system_prompt = f"""You are trading on a prediction market platform. Your goal is to
select the most promising markets for further evaluation so you can place
profitable trades.

Pick up to {self.max_markets} markets from the candidate list. Only the
markets you pick will be researched, forecasted, and traded this tick.
Ticks fire every {_TICK_INTERVAL_MIN} minutes; you will re-evaluate every market
on the next tick unless it has resolved.

Prediction market basics:
- Each market resolves YES (=$1) or NO (=$0).
- A YES share's mid is the market's implied probability of YES.
- BUY hits the ask, SELL hits the bid; the spread is the per-trade cost.
- Wider spread = more cost to round-trip a position.
- Prices near 0 or 1 are usually near-resolved and rarely move much.

Selection guidance:
- A wider spread means you need a bigger edge to overcome the round-trip cost.
- Prices near 0 or 1 are usually settled; only pick one if something has
  materially changed.
- Markets you already hold are valid picks when you want to reassess the
  position (add, trim, or exit).

Each candidate row: market_id | question | yes_bid/yes_ask sp=(spread, % of mid) | 24h vol | days to resolution

Use the submit_review tool."""

        user_prompt = f"""Tick: {tick_ctx.tick_ts}  Cash: ${float(tick_ctx.cash):,.0f}
{portfolio}

{len(candidates)} candidates:
{candidates_text}"""

        assert self.llm_client is not None  # _require_llm enforces this
        return self.llm_client.generate_json(
            self._messages(system_prompt, user_prompt), tool=REVIEW_TOOL,
        )


def _row(m: CandidateMarket, tick_ts: datetime) -> str:
    mid = (m.yes_bid + m.yes_ask) / 2
    spread = m.yes_ask - m.yes_bid
    sp_pct = (spread / mid * 100) if mid > 0 else 0
    days = days_until_resolution(tick_ts, m.resolution_time)
    return (
        f"{m.market_id} | {m.question} | "
        f"{m.yes_bid:.2f}/{m.yes_ask:.2f} sp={spread:.2f}({sp_pct:.0f}%) | "
        f"vol ${m.volume_24h:,.0f} | {days}d to resolve"
    )
