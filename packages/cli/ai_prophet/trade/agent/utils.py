"""Shared formatting helpers for agent stages.

These are the single source of truth for any prompt fragment that more
than one stage renders. Stages must not hand-roll equivalents.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ai_prophet.trade.core import TickContext


def days_until_resolution(tick_ts: datetime, resolution_time: datetime) -> int:
    """Whole days from the current tick to market resolution, floored at 0."""
    return max(0, (resolution_time.date() - tick_ts.date()).days)


def render_time_context(tick_ts: datetime, resolution_time: datetime) -> str:
    """One-line time framing for forecast/action prompts.

    Example: ``Today: 2026-05-17  ·  Resolves: 2026-11-04 (in 171 days)``
    """
    days = days_until_resolution(tick_ts, resolution_time)
    if days == 0:
        when = "today"
    elif days == 1:
        when = "in 1 day"
    else:
        when = f"in {days} days"
    return (
        f"Today: {tick_ts.date().isoformat()}  ·  "
        f"Resolves: {resolution_time.date().isoformat()} ({when})"
    )


def empty_search_summary(*, question: str, reason: str) -> dict[str, Any]:
    """Placeholder summary matching search.schema.json.

    Used when no external search results are available and the forecast
    must proceed without fresh web evidence.
    """
    return {
        "schema_version": "v1",
        "summary": (
            f"No external web evidence was retrieved for '{question[:180]}'. "
            "Forecasting proceeds without fresh search data."
        ),
        "key_points": [],
        "open_questions": [reason],
    }


def candidate_questions(tick_ctx: TickContext) -> dict[str, str]:
    """Map ``market_id -> question`` for every candidate this tick."""
    return {m.market_id: m.question for m in tick_ctx.candidates}


def render_portfolio(
    tick_ctx: TickContext,
    *,
    focus_market_id: str | None = None,
    max_positions: int = 5,
) -> str:
    """Format portfolio state for an LLM prompt.

    Always renders the summary header and (when there are positions) a list
    of up to ``max_positions`` lines.

    If ``focus_market_id`` is provided and the agent holds that market, a
    focused block is appended with entry/mark/PnL, concrete exit pricing
    (SELL action + price + proceeds), and the position's *original thesis*
    (forecaster + trader rationales captured at entry) so the action stage
    can decide whether the thesis still holds.
    """
    parts = [_render_summary(tick_ctx, max_positions)]
    if focus_market_id:
        focused = _render_focused_position(tick_ctx, focus_market_id)
        if focused:
            parts.append(focused)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _render_summary(tick_ctx: TickContext, max_positions: int) -> str:
    cash = float(tick_ctx.cash)
    equity = float(tick_ctx.equity)
    total_pnl = float(tick_ctx.total_pnl)
    positions = tick_ctx.positions

    pnl_str = _fmt_signed_dollars(total_pnl)

    if not positions:
        return (
            f"PORTFOLIO: ${cash:,.0f} cash, ${equity:,.0f} equity "
            f"({pnl_str} P&L), no open positions"
        )

    total_unrealized = sum(float(p.unrealized_pnl or 0.0) for p in positions)
    total_realized = sum(float(p.realized_pnl or 0.0) for p in positions)
    header = (
        f"PORTFOLIO: ${cash:,.0f} cash, ${equity:,.0f} equity "
        f"({pnl_str} total P&L), {len(positions)} open positions "
        f"(unrealized={_fmt_signed_dollars(total_unrealized)}, "
        f"realized={_fmt_signed_dollars(total_realized)}):"
    )

    candidate_questions = {m.market_id: m.question for m in tick_ctx.candidates}
    lines = [header]
    for pos in positions[:max_positions]:
        unrealized = float(pos.unrealized_pnl) if pos.unrealized_pnl else 0.0
        realized = float(pos.realized_pnl) if pos.realized_pnl else 0.0
        question = (
            getattr(pos, "question", "")
            or candidate_questions.get(pos.market_id)
            or pos.market_id
        )[:120]
        lines.append(
            f"  {pos.side} {float(pos.shares):.0f} shares "
            f"(unrealized={_fmt_signed_dollars(unrealized)}, "
            f"realized={_fmt_signed_dollars(realized)}): {question}"
        )

    if len(positions) > max_positions:
        lines.append(f"  ... and {len(positions) - max_positions} more")

    return "\n".join(lines)


def _render_focused_position(tick_ctx: TickContext, market_id: str) -> str:
    position = tick_ctx.get_position(market_id)
    if not position:
        return ""

    entry = float(position.avg_entry_price)
    current = float(position.current_price) if position.current_price else entry
    unrealized = float(position.unrealized_pnl) if position.unrealized_pnl else 0.0
    realized = float(position.realized_pnl) if position.realized_pnl else 0.0
    shares = float(position.shares)
    cost_basis = entry * shares
    current_value = current * shares
    pnl_pct = (unrealized / cost_basis * 100) if cost_basis > 0 else 0.0

    pnl_str = _fmt_signed_dollars(unrealized, decimals=2)
    realized_str = _fmt_signed_dollars(realized, decimals=2)
    pct_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"

    lines = [
        "YOU HOLD THIS MARKET:",
        f"- Side: {position.side}",
        f"- Shares: {shares:.2f}",
        f"- Entry: ${entry:.3f} -> Now: ${current:.3f}",
        f"- Value: ${current_value:.2f} (cost: ${cost_basis:.2f})",
        f"- Unrealized P&L: {pnl_str} ({pct_str})",
        f"- Realized P&L: {realized_str}",
    ]

    # Concrete exit math, only if we still have quotes for this market.
    candidate = tick_ctx.get_candidate(market_id)
    if candidate is not None:
        exit_price = candidate.yes_bid if position.side == "YES" else candidate.no_bid
        exit_proceeds = shares * exit_price
        sell_action = "SELL_YES" if position.side == "YES" else "SELL_NO"
        lines.append(
            f"- Exit now: {sell_action} at {exit_price:.1%} -> "
            f"proceeds ${exit_proceeds:,.2f} (vs cost ${cost_basis:,.2f})"
        )

    lines.extend(_render_original_thesis(position))
    return "\n".join(lines)


def _render_original_thesis(position: Any) -> list[str]:
    """Surface the rationales captured when this position was opened.

    Falls back to a single "not captured" line when nothing is available,
    so the structure stays stable regardless of server-side support.
    """
    forecast = (getattr(position, "entry_forecast_rationale", "") or "").strip()
    trade = (getattr(position, "entry_trade_rationale", "") or "").strip()
    if not forecast and not trade:
        return [
            "ORIGINAL THESIS: not captured for this position "
            "(entered before rationale capture was available)."
        ]
    lines = ["ORIGINAL THESIS (from the trade that opened this position):"]
    if forecast:
        lines.append(f"- Forecaster: {forecast}")
    if trade:
        lines.append(f"- Trader: {trade}")
    return lines


def _fmt_signed_dollars(value: float, decimals: int = 0) -> str:
    fmt = f"{{value:+,.{decimals}f}}"
    return f"${fmt.format(value=value)}"
