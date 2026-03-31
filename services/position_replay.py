"""Helpers for replaying order history into literal YES/NO inventory."""

from __future__ import annotations

from datetime import UTC, datetime
from dataclasses import dataclass, field
from typing import Any, Iterable


EPSILON = 1e-6


def normalize_order(order: Any) -> tuple[str, str, float, float, float]:
    """Return normalized (action, side, shares, price, fee) for a betting order row."""
    action = (getattr(order, "action", "BUY") or "BUY").upper()
    side = (getattr(order, "side", "yes") or "yes").lower()

    # CRITICAL: Only use filled_shares for position calculation
    # Never fall back to count - that's the requested amount, not what was actually filled
    # For PENDING orders with filled_shares=0, this correctly returns 0 shares
    shares = float(getattr(order, "filled_shares", 0) or 0)

    price = float(getattr(order, "fill_price", 0) or 0)
    if price <= 0:
        price = float(getattr(order, "price_cents", 0) or 0) / 100.0
    if price > 1.0:
        price = price / 100.0

    fee = float(getattr(order, "fee_paid", 0) or 0)

    return action, side, shares, price, fee


@dataclass
class InventoryPosition:
    yes_qty: float = 0.0
    yes_cost: float = 0.0
    no_qty: float = 0.0
    no_cost: float = 0.0
    realized_pnl: float = 0.0
    realized_trades: int = 0
    max_position: float = 0.0
    last_side: str = ""
    total_buy_cost: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def apply_order(self, order: Any, *, ticker: str = "") -> float:
        action, side, shares, price, fee = normalize_order(order)
        if shares <= EPSILON:
            return 0.0

        realized_delta = 0.0
        if action == "SELL":
            held_qty, held_cost = self._held_for(side)
            sell_qty = min(shares, held_qty)
            if sell_qty > EPSILON:
                avg_entry = held_cost / held_qty if held_qty > EPSILON else 0.0
                realized_delta = (price - avg_entry) * sell_qty - fee
                self.realized_pnl += realized_delta
                self.realized_trades += 1
                self._set_held(side, held_qty - sell_qty, held_cost - avg_entry * sell_qty)

            excess = shares - sell_qty
            if excess > EPSILON:
                self.warnings.append(
                    f"Oversell ignored for {ticker or '<unknown>'}: side={side} excess={excess:.4f}"
                )
        else:
            held_qty, held_cost = self._held_for(side)
            self._set_held(side, held_qty + shares, held_cost + shares * price + fee)
            self.last_side = side
            self.total_buy_cost += shares * price + fee

        self.max_position = max(self.max_position, self.yes_qty, self.no_qty)
        return realized_delta

    def get_both_positions(self) -> tuple[float, float, float, float]:
        """Return both YES and NO positions separately.

        Returns: (yes_qty, yes_avg_price, no_qty, no_avg_price)
        """
        yes_qty = 0.0 if self.yes_qty < EPSILON else self.yes_qty
        no_qty = 0.0 if self.no_qty < EPSILON else self.no_qty
        yes_avg = self.yes_cost / yes_qty if yes_qty > EPSILON else 0.0
        no_avg = self.no_cost / no_qty if no_qty > EPSILON else 0.0
        return yes_qty, yes_avg, no_qty, no_avg

    def current_position(self) -> tuple[str | None, float, float]:
        yes_qty = 0.0 if self.yes_qty < EPSILON else self.yes_qty
        no_qty = 0.0 if self.no_qty < EPSILON else self.no_qty

        # For Kalshi markets, YES and NO are separate contracts that don't net out
        # Return the larger position as the primary, but don't cancel them
        if yes_qty > 0 and no_qty == 0:
            return "yes", yes_qty, self.yes_cost / yes_qty if yes_qty > EPSILON else 0.0
        if no_qty > 0 and yes_qty == 0:
            return "no", no_qty, self.no_cost / no_qty if no_qty > EPSILON else 0.0
        if yes_qty == 0 and no_qty == 0:
            return None, 0.0, 0.0

        # IMPORTANT: YES and NO positions should NOT net out for Kalshi markets
        # They are separate contracts, not opposite sides of the same position
        # Return the larger position as primary (for backward compatibility)
        # but both positions exist independently
        if yes_qty >= no_qty:
            avg = self.yes_cost / yes_qty if yes_qty > EPSILON else 0.0
            return "yes", yes_qty, avg
        else:
            avg = self.no_cost / no_qty if no_qty > EPSILON else 0.0
            return "no", no_qty, avg

    def _held_for(self, side: str) -> tuple[float, float]:
        if side == "yes":
            return self.yes_qty, self.yes_cost
        return self.no_qty, self.no_cost

    def _set_held(self, side: str, qty: float, cost: float) -> None:
        qty = 0.0 if qty < EPSILON else qty
        cost = 0.0 if qty == 0.0 else max(0.0, cost)
        if side == "yes":
            self.yes_qty = qty
            self.yes_cost = cost
        else:
            self.no_qty = qty
            self.no_cost = cost


def replay_orders_by_ticker(orders: Iterable[Any]) -> dict[str, InventoryPosition]:
    positions: dict[str, InventoryPosition] = {}
    for order in orders:
        ticker = getattr(order, "ticker", "")
        pos = positions.setdefault(ticker, InventoryPosition())
        pos.apply_order(order, ticker=ticker)
    return positions


def load_replayable_orders(
    session: Any,
    betting_order_model: Any,
    instance_name: str,
    *,
    tickers: Iterable[str] | None = None,
) -> list[Any]:
    """Load all orders whose executed quantity should affect replayed inventory."""
    from sqlalchemy import or_

    query = (
        session.query(betting_order_model)
        .filter(betting_order_model.instance_name == instance_name)
        .filter(
            or_(
                betting_order_model.status.in_(["FILLED", "DRY_RUN"]),
                betting_order_model.filled_shares > 0,
            )
        )
    )

    ticker_list = sorted({ticker for ticker in (tickers or []) if ticker})
    if tickers is not None:
        if not ticker_list:
            return []
        query = query.filter(betting_order_model.ticker.in_(ticker_list))

    return query.order_by(betting_order_model.created_at.asc(), betting_order_model.id.asc()).all()


def sync_replayed_positions(
    session: Any,
    instance_name: str,
    positions: dict[str, InventoryPosition],
    *,
    markets_by_ticker: dict[str, Any] | None = None,
    tickers: Iterable[str] | None = None,
    log: Any | None = None,
) -> None:
    """Persist replayed positions into trading_positions using one shared policy."""
    from db_models import TradingPosition

    markets_by_ticker = markets_by_ticker or {}
    now = datetime.now(UTC)

    if tickers is None:
        target_tickers = set(positions.keys())
        existing_rows = (
            session.query(TradingPosition)
            .filter(TradingPosition.instance_name == instance_name)
            .all()
        )
        for row in existing_rows:
            ticker = row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id
            target_tickers.add(ticker)
    else:
        target_tickers = {ticker for ticker in tickers if ticker}
        if not target_tickers:
            return
        market_ids = [f"kalshi:{ticker}" for ticker in sorted(target_tickers)]
        existing_rows = (
            session.query(TradingPosition)
            .filter(TradingPosition.instance_name == instance_name)
            .filter(TradingPosition.market_id.in_(market_ids))
            .all()
        )

    existing_by_ticker = {
        (row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id): row
        for row in existing_rows
    }

    for ticker in sorted(target_tickers):
        pos = positions.get(ticker)
        if pos and log:
            for warning in pos.warnings:
                log.warning("Position replay warning for %s (%s): %s", ticker, instance_name, warning)

        existing = existing_by_ticker.get(ticker)
        side, qty, avg_price = pos.current_position() if pos else (None, 0.0, 0.0)
        if side is None or qty < 0.001:
            if existing:
                session.delete(existing)
            continue

        market = markets_by_ticker.get(ticker)
        current_bid = None
        if market is not None:
            if side == "yes":
                current_bid = market.yes_bid
                if current_bid is None and market.no_ask is not None:
                    current_bid = 1.0 - market.no_ask
            else:
                current_bid = market.no_bid
                if current_bid is None and market.yes_ask is not None:
                    current_bid = 1.0 - market.yes_ask

        unrealized = 0.0 if current_bid is None else (current_bid - avg_price) * qty
        if existing:
            existing.contract = side
            existing.quantity = qty
            existing.avg_price = round(avg_price, 4)
            existing.realized_pnl = round(pos.realized_pnl, 4)
            existing.unrealized_pnl = round(unrealized, 4)
            existing.max_position = max(existing.max_position or 0.0, pos.max_position, qty)
            existing.realized_trades = pos.realized_trades
            existing.updated_at = now
        else:
            session.add(TradingPosition(
                instance_name=instance_name,
                market_id=f"kalshi:{ticker}",
                contract=side,
                quantity=qty,
                avg_price=round(avg_price, 4),
                realized_pnl=round(pos.realized_pnl, 4),
                unrealized_pnl=round(unrealized, 4),
                max_position=max(pos.max_position, qty),
                realized_trades=pos.realized_trades,
                updated_at=now,
            ))


def summarize_replayed_positions(
    positions: dict[str, InventoryPosition],
) -> tuple[float, float, int]:
    """Return (capital_deployed, total_realized, open_position_count)."""
    capital_deployed = 0.0
    total_realized = 0.0
    open_position_count = 0

    for pos in positions.values():
        side, qty, avg_price = pos.current_position()
        if side and qty > EPSILON:
            capital_deployed += qty * avg_price
            open_position_count += 1
        total_realized += pos.realized_pnl

    return capital_deployed, total_realized, open_position_count
