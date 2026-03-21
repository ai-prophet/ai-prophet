"""Position replay helpers — compute net positions from order history.

Replays a sequence of BettingOrder rows (already filtered by instance) to
derive the current net position and cash flows for each ticker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class ReplayedPosition:
    """Net position for a single ticker built by replaying orders chronologically."""

    ticker: str
    yes_qty: Decimal = field(default_factory=Decimal)
    no_qty: Decimal = field(default_factory=Decimal)
    total_cost: Decimal = field(default_factory=Decimal)      # sum of BUY (filled_shares * fill_price)
    total_proceeds: Decimal = field(default_factory=Decimal)  # sum of SELL (filled_shares * fill_price)

    def apply(self, order: Any) -> None:
        """Incorporate one BettingOrder row into the running position."""
        qty = Decimal(str(order.filled_shares))
        price = Decimal(str(order.fill_price))
        value = qty * price
        action = (order.action or "BUY").upper()
        side = order.side.lower()

        if action == "BUY":
            self.total_cost += value
            if side == "yes":
                self.yes_qty += qty
            else:
                self.no_qty += qty
        elif action == "SELL":
            self.total_proceeds += value
            if side == "yes":
                self.yes_qty -= qty
            else:
                self.no_qty -= qty

    def current_position(self) -> tuple[str | None, int, Decimal]:
        """Return (side, qty, net_cost) for the dominant side.

        Returns ``(None, 0, 0)`` when flat.
        """
        net_yes = self.yes_qty - self.no_qty
        net_cost = self.total_cost - self.total_proceeds
        if net_yes > 0:
            return "yes", max(0, round(float(net_yes))), net_cost
        if net_yes < 0:
            return "no", max(0, round(float(-net_yes))), net_cost
        return None, 0, Decimal("0")


def replay_orders_by_ticker(orders: list[Any]) -> dict[str, ReplayedPosition]:
    """Group and replay orders into per-ticker positions.

    Args:
        orders: ``BettingOrder`` rows for a single instance, sorted ascending
                by ``created_at`` / ``id``.

    Returns:
        ``{ticker: ReplayedPosition}`` mapping.
    """
    positions: dict[str, ReplayedPosition] = {}
    for order in orders:
        ticker = order.ticker
        if ticker not in positions:
            positions[ticker] = ReplayedPosition(ticker=ticker)
        positions[ticker].apply(order)
    return positions


def summarize_replayed_positions(
    positions: dict[str, ReplayedPosition],
) -> tuple[float, float, float]:
    """Aggregate capital flows across all tickers.

    Returns:
        ``(capital_deployed, total_realized, unrealized_placeholder)`` where

        * ``capital_deployed`` — total cash spent on BUY orders
        * ``total_realized``   — total cash received from SELL orders
        * third value          — reserved / unused by callers
    """
    capital_deployed = sum(float(p.total_cost) for p in positions.values())
    total_realized = sum(float(p.total_proceeds) for p in positions.values())
    return capital_deployed, total_realized, 0.0
