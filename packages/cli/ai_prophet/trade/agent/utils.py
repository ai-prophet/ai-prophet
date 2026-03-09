"""Shared utilities for agent stages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ai_prophet.trade.core import TickContext


def format_portfolio_summary(tick_ctx: TickContext, include_positions: bool = True) -> str:
    """Format portfolio summary for prompts.

    Args:
        tick_ctx: Current tick context
        include_positions: Whether to list individual positions

    Returns:
        Formatted portfolio string
    """
    cash = float(tick_ctx.cash)
    equity = float(tick_ctx.equity)
    total_pnl = float(tick_ctx.total_pnl)
    positions = tick_ctx.positions

    pnl_str = _fmt_signed_dollars(total_pnl)

    if not positions:
        return f"PORTFOLIO: ${cash:,.0f} cash, ${equity:,.0f} equity ({pnl_str} P&L), no open positions"

    total_unrealized = sum(float(p.unrealized_pnl or 0.0) for p in positions)
    total_realized = sum(float(p.realized_pnl or 0.0) for p in positions)
    header = (
        f"PORTFOLIO: ${cash:,.0f} cash, ${equity:,.0f} equity ({pnl_str} total P&L), "
        f"{len(positions)} open positions "
        f"(uPnL={_fmt_signed_dollars(total_unrealized)}, rPnL={_fmt_signed_dollars(total_realized)})"
    )

    if not include_positions:
        return header

    # Build market_id -> question lookup
    candidates = tick_ctx.candidates
    questions = {m.market_id: m.question for m in candidates}

    lines = [header + ":"]
    for pos in positions[:5]:  # Show first 5
        unrealized = float(pos.unrealized_pnl) if pos.unrealized_pnl else 0.0
        realized = float(pos.realized_pnl) if pos.realized_pnl else 0.0
        question = questions.get(pos.market_id, pos.market_id)[:55]
        lines.append(
            f"  {pos.side} {float(pos.shares):.0f} shares "
            f"(u={_fmt_signed_dollars(unrealized)}, r={_fmt_signed_dollars(realized)}): {question}"
        )

    if len(positions) > 5:
        lines.append(f"  ... and {len(positions) - 5} more")

    return "\n".join(lines)


def format_position_for_market(tick_ctx: TickContext, market_id: str) -> str:
    """Format existing position info for a specific market.

    Args:
        tick_ctx: Current tick context
        market_id: Market to check for position

    Returns:
        Formatted position string or empty string if no position
    """
    position = tick_ctx.get_position(market_id)
    if not position:
        return ""

    entry = float(position.avg_entry_price)
    current = float(position.current_price) if position.current_price else entry
    unrealized = float(position.unrealized_pnl) if position.unrealized_pnl else 0.0
    realized = float(position.realized_pnl) if position.realized_pnl else 0.0
    shares = float(position.shares)

    # Calculate values
    cost_basis = entry * shares
    current_value = current * shares
    pnl_pct = (unrealized / cost_basis * 100) if cost_basis > 0 else 0

    # Format with signs
    pnl_str = _fmt_signed_dollars(unrealized, decimals=2)
    realized_str = _fmt_signed_dollars(realized, decimals=2)
    pct_str = f"+{pnl_pct:.1f}%" if pnl_pct >= 0 else f"{pnl_pct:.1f}%"

    return f"""
YOU HOLD THIS MARKET:
- Side: {position.side}
- Shares: {shares:.2f}
- Entry: ${entry:.3f} -> Now: ${current:.3f}
- Value: ${current_value:.2f} (cost: ${cost_basis:.2f})
- Unrealized P&L: {pnl_str} ({pct_str})
- Realized P&L: {realized_str}
"""


def _fmt_signed_dollars(value: float, decimals: int = 0) -> str:
    fmt = f"{{value:+,.{decimals}f}}"
    return f"${fmt.format(value=value)}"

