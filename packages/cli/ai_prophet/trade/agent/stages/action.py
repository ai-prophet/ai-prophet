"""Action stage: turn each probability forecast into a trade intent.

A separate LLM call from forecasting so forecasting accuracy and trade
sizing can be evaluated independently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ai_prophet_core.ruleset import (
    MAX_GROSS_EXPOSURE,
    MAX_NOTIONAL_PER_MARKET,
    MAX_OPEN_POSITIONS,
    MAX_TRADES_PER_DAY,
    MAX_TRADES_PER_TICK,
)

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.llm import LLMClient

from ..tool_schemas import TRADE_DECISION_TOOL
from ..utils import render_portfolio, render_time_context
from ..validator import SchemaValidator
from .base import PipelineStage, StageResult

logger = logging.getLogger(__name__)


def _render_research(summary: dict[str, Any] | None) -> str:
    """Compact research block for the trader.

    Renders the forecaster's underlying research so sizing can reflect
    conviction (key points) and uncertainty (open questions). Returns an
    empty string when no summary is available so the prompt collapses
    cleanly.
    """
    if not summary:
        return ""
    text = (summary.get("summary") or "").strip()
    key_points = [kp for kp in summary.get("key_points", []) if kp]
    open_questions = [q for q in summary.get("open_questions", []) if q]
    if not (text or key_points or open_questions):
        return ""

    lines = ["", "Research underlying the forecast:"]
    if text:
        lines.append(text)
    if key_points:
        lines.append("Key points:")
        lines.extend(f"- {kp}" for kp in key_points)
    if open_questions:
        lines.append("Open questions (sources of uncertainty):")
        lines.extend(f"- {q}" for q in open_questions)
    return "\n".join(lines)


# recommendation -> (action, side, price_fn(market_info)). Mirrors the
# execution engine's _compute_price so the LLM sees the same price it
# will fill at.
_DECISIONS: dict[str, tuple[str, str, Callable[[CandidateMarket], float]]] = {
    "BUY_YES":  ("BUY",  "YES", lambda m: m.yes_ask),
    "BUY_NO":   ("BUY",  "NO",  lambda m: 1.0 - m.yes_bid),
    "SELL_YES": ("SELL", "YES", lambda m: m.yes_bid),
    "SELL_NO":  ("SELL", "NO",  lambda m: 1.0 - m.yes_ask),
}


class ActionStage(PipelineStage):
    """Convert probability forecasts into trade intents via LLM.

    Input:  forecast stage result (``p_yes`` per market).
    Output: ``{"intents": [...], "decisions": {mid: decision}}``.
    """

    def __init__(self, llm_client: LLMClient | None = None, min_size_usd: float = 1.0):
        super().__init__(llm_client=llm_client)
        self.min_size_usd = min_size_usd
        self.validator = SchemaValidator()

    @property
    def name(self) -> str:
        return "action"

    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        if err := self._require_llm():
            return err
        if err := self._require_stage(previous_results, "forecast"):
            return err

        forecasts = previous_results["forecast"].data.get("forecasts", {})
        logger.info("Action stage processing %d forecasts", len(forecasts))
        if not forecasts:
            return self._ok({"intents": [], "decisions": {}})

        search_result = previous_results.get("search")
        summaries: dict[str, dict[str, Any]] = (
            search_result.data.get("summaries", {}) if search_result else {}
        )

        intents: list[dict[str, Any]] = []
        decisions: dict[str, dict[str, Any]] = {}

        for market_id, forecast in forecasts.items():
            market_info = tick_ctx.get_candidate(market_id)
            if market_info is None:
                logger.warning("Market %s not in tick candidates; skipping", market_id)
                continue

            try:
                decision = self._generate_trade_decision(
                    market_id, forecast, market_info, tick_ctx,
                    search_summary=summaries.get(market_id),
                )
            except Exception as e:
                logger.error("Trade decision failed for %s: %s", market_id, e, exc_info=True)
                return self._fail(
                    f"Trade decision failed for {market_id}: {e}",
                    {"intents": intents, "decisions": decisions},
                )

            decisions[market_id] = decision
            intent = self._convert_to_intent(market_id, decision, market_info, tick_ctx)
            if intent:
                intents.append(intent)

        logger.info("Action stage complete: %d intents from %d forecasts",
                    len(intents), len(forecasts))
        return self._ok({"intents": intents, "decisions": decisions})

    # -- internals ----------------------------------------------------------

    def _generate_trade_decision(
        self,
        market_id: str,
        forecast: dict[str, Any],
        market_info: CandidateMarket,
        tick_ctx: TickContext,
        search_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        p_yes = forecast.get("p_yes", 0.5)
        forecast_rationale = forecast.get("rationale", "No rationale provided")

        yes_bid = market_info.yes_bid
        yes_ask = market_info.yes_ask
        spread = yes_ask - yes_bid
        no_ask = 1.0 - yes_bid

        # Edge vs the price you'd actually pay to open (the ask). Precomputed
        # so the LLM doesn't do fragile arithmetic on every market.
        yes_edge = p_yes - yes_ask
        no_edge = (1 - p_yes) - no_ask

        # Volume is shown as context (low-volume = low-information), not as a
        # sizing cap. Fills are at the quoted bid/ask regardless of order
        # size; the real per-trade cost is the spread.
        volume_24h = float(getattr(market_info, "volume_24h", 0) or 0)

        portfolio = render_portfolio(tick_ctx, focus_market_id=market_id)
        time_context = render_time_context(tick_ctx.tick_ts, market_info.resolution_time)
        research_block = _render_research(search_summary)

        system_prompt = f"""You are trading in a prediction market. Your responsibility is to
size trades to be profitable, given the forecaster's probability estimate,
the research it was based on, and the current market quote.

Mechanics:
- BUY YES fills at YES ASK. SELL YES fills at YES BID. BUY NO fills at (1-YES bid). SELL NO fills at (1-YES ask).
- Order size doesn't affect fill price; the spread is the per-trade cost.
- A round-trip (enter then exit) costs the full spread per share, so after
  entry you'll show a paper loss of half the spread (entry at ask, marked at mid).

Actions: BUY_YES, BUY_NO, SELL_YES, SELL_NO, HOLD.
SELL only valid if you hold that side. Use size_usd = current position value
to fully exit. Consider SELL when your forecast has materially moved away
from the thesis that justified the entry, or to free cash for a stronger trade.

Volume is the dollar amount traded in this market over the last 24 hours.
It does not affect your fill price.

Server-enforced limits (intents over these are rejected):
- Max notional per market: ${MAX_NOTIONAL_PER_MARKET:,.0f}
- Max gross exposure across all positions: ${MAX_GROSS_EXPOSURE:,.0f}
- Max open positions: {MAX_OPEN_POSITIONS}
- Max trades per tick: {MAX_TRADES_PER_TICK}
- Max trades per day: {MAX_TRADES_PER_DAY}

Prices near 0 or 1 typically reflect near-resolved markets and rarely move much.

Use the submit_trade_decision tool."""

        user_prompt = f"""Market: {market_info.question}
{time_context}

Forecaster output (from the previous step):
- p_yes = {p_yes:.1%}
- Reasoning: {forecast_rationale}
{research_block}
YES: bid {yes_bid:.1%} / ask {yes_ask:.1%} / spread {spread:.1%}
Buy YES at {yes_ask:.1%}  (edge vs forecast: {yes_edge*100:+.1f}pp)
Buy NO  at {no_ask:.1%}  (edge vs forecast: {no_edge*100:+.1f}pp)
24h volume: ${volume_24h:,.0f}

{portfolio}

Decide."""

        assert self.llm_client is not None  # _require_llm enforces this
        decision = self.llm_client.generate_json(
            self._messages(system_prompt, user_prompt), tool=TRADE_DECISION_TOOL,
        )
        self.validator.validate_trade_decision(decision)
        return decision

    def _convert_to_intent(
        self,
        market_id: str,
        decision: dict[str, Any],
        market_info: CandidateMarket,
        tick_ctx: TickContext,
    ) -> dict[str, Any] | None:
        recommendation = decision.get("recommendation", "HOLD")
        size_usd = decision.get("size_usd", 0)

        if recommendation not in _DECISIONS:
            return None  # HOLD or unknown
        action, side, price_fn = _DECISIONS[recommendation]
        price = price_fn(market_info)
        if price <= 0:
            logger.warning("Invalid price %s for %s", price, market_id)
            return None

        is_sell = action == "SELL"

        # SELL hard gate: pre-filter to avoid wasted submissions the engine
        # would reject ("Cannot SELL without position").
        if is_sell:
            position = tick_ctx.get_position(market_id)
            if position is None or position.side != side:
                logger.warning(
                    "Skipping %s for %s: no matching %s position", recommendation, market_id, side,
                )
                return None

        # min_size_usd is a noise filter for new entries only; SELLs always
        # go through so we can close out leftovers.
        if not is_sell and size_usd < self.min_size_usd:
            return None

        shares = size_usd / price

        # Cap SELL shares to held position. Engine clamps too, but doing it
        # here keeps logs honest.
        if is_sell:
            held = float(position.shares)  # type: ignore[union-attr]
            if shares > held:
                shares = held

        return {
            "run_id": tick_ctx.run_id,
            "tick_ts": tick_ctx.tick_ts,
            "market_id": market_id,
            "question": market_info.question or market_id,
            "action": action,
            "side": side,
            "shares": f"{shares:.2f}",
            "rationale": decision.get("rationale", ""),
        }
