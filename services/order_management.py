"""Order management utilities for handling stale orders and position reconciliation."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def cancel_stale_orders(
    db_engine: Engine,
    adapter,
    instance_name: str,
    stale_threshold_minutes: int = 60,
) -> int:
    """Cancel orders that have been PENDING for too long.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders
        stale_threshold_minutes: Age in minutes after which orders are considered stale

    Returns:
        Number of orders cancelled
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    cutoff = datetime.now(UTC) - timedelta(minutes=stale_threshold_minutes)
    cancelled_count = 0

    with get_session(db_engine) as session:
        stale_orders = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.status == "PENDING",
                BettingOrder.created_at < cutoff,
            )
            .all()
        )

        for order in stale_orders:
            try:
                # Try to cancel on exchange
                if order.exchange_order_id:
                    try:
                        adapter.cancel_order(order.exchange_order_id)
                        logger.info(
                            "[ORDER_MGMT] Cancelled stale order %s on exchange: %s %s %s",
                            order.order_id[:8],
                            order.action,
                            order.count,
                            order.ticker,
                        )
                    except Exception as e:
                        logger.warning(
                            "[ORDER_MGMT] Failed to cancel order %s on exchange (may already be filled/cancelled): %s",
                            order.order_id[:8],
                            e,
                        )

                # Mark as cancelled in database regardless
                order.status = "CANCELLED"
                cancelled_count += 1

            except Exception as e:
                logger.error("[ORDER_MGMT] Error cancelling stale order %s: %s", order.order_id[:8], e)

        if cancelled_count > 0:
            session.commit()
            logger.info("[ORDER_MGMT] Cancelled %d stale orders for %s", cancelled_count, instance_name)

    return cancelled_count


def reconcile_positions_with_kalshi(
    db_engine: Engine,
    adapter,
    instance_name: str,
    tolerance_contracts: int = 5,
) -> dict[str, tuple[int, int]]:
    """Compare database positions with Kalshi reality and report discrepancies.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders
        tolerance_contracts: Number of contracts difference to tolerate before alerting

    Returns:
        Dict of ticker -> (db_qty, kalshi_qty) for positions with drift > tolerance
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    # Get database positions by replaying orders
    from position_replay import replay_orders_by_ticker

    with get_session(db_engine) as session:
        orders = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.status.in_(["FILLED", "PENDING", "DRY_RUN"]),
            )
            .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
            .all()
        )

    db_positions = replay_orders_by_ticker(orders)

    # Get Kalshi positions
    try:
        kalshi_data = adapter._session.get(
            adapter._base_url + "/trade-api/v2/portfolio/positions",
            headers=adapter._sign_request("GET", "/trade-api/v2/portfolio/positions"),
            timeout=30,
        )
        kalshi_data.raise_for_status()
        kalshi_positions_raw = kalshi_data.json().get("market_positions", [])
    except Exception as e:
        logger.error("[ORDER_MGMT] Failed to fetch Kalshi positions: %s", e)
        return {}

    # Parse Kalshi positions
    kalshi_positions = {}
    for pos in kalshi_positions_raw:
        ticker = pos.get("ticker")
        position_fp = float(pos.get("position_fp", 0))
        if position_fp != 0:
            kalshi_positions[ticker] = int(abs(position_fp))

    # Compare
    drifts = {}
    all_tickers = set(db_positions.keys()) | set(kalshi_positions.keys())

    for ticker in all_tickers:
        # Get DB position
        db_pos = db_positions.get(ticker)
        if db_pos:
            side, qty, _ = db_pos.current_position()
            db_qty = int(qty) if qty > 0 else 0
        else:
            db_qty = 0

        # Get Kalshi position
        kalshi_qty = kalshi_positions.get(ticker, 0)

        # Check drift
        drift = abs(db_qty - kalshi_qty)
        if drift > tolerance_contracts:
            drifts[ticker] = (db_qty, kalshi_qty)
            logger.error(
                "[ORDER_MGMT] POSITION DRIFT: %s DB=%d Kalshi=%d (drift=%d)",
                ticker,
                db_qty,
                kalshi_qty,
                drift,
            )

    if not drifts:
        logger.info("[ORDER_MGMT] Position reconciliation OK - no drifts detected")

    return drifts


def check_order_idempotency(
    db_engine: Engine,
    instance_name: str,
    ticker: str,
    cycle_start_time: datetime,
) -> bool:
    """Check if an order has already been placed for this market in this cycle.

    Args:
        db_engine: Database engine
        instance_name: Instance name
        ticker: Market ticker
        cycle_start_time: When this cycle started

    Returns:
        True if order already placed this cycle, False otherwise
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    # Look for any orders placed in the last cycle interval (typically 1 hour)
    cycle_window = cycle_start_time - timedelta(minutes=5)  # Allow 5-minute overlap

    with get_session(db_engine) as session:
        existing = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.ticker == ticker,
                BettingOrder.created_at >= cycle_window,
                BettingOrder.status.in_(["FILLED", "PENDING"]),
            )
            .first()
        )

        if existing:
            logger.info(
                "[ORDER_MGMT] Skipping %s: already ordered this cycle (order=%s, status=%s)",
                ticker,
                existing.order_id[:8],
                existing.status,
            )
            return True

    return False
