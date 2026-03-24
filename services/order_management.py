"""Order management utilities for handling stale orders and position reconciliation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

UTC = timezone.utc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _sync_pending_order_status(
    db_engine: Engine,
    adapter,
    instance_name: str,
) -> int:
    """Check status of pending orders with Kalshi and update DB accordingly.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders

    Returns:
        Number of orders updated
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    updated_count = 0

    with get_session(db_engine) as session:
        pending_orders = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.status == "PENDING",
            )
            .all()
        )

        for order in pending_orders:
            if not order.exchange_order_id:
                continue

            try:
                # Get current order status from Kalshi
                kalshi_order = adapter.get_order(order.exchange_order_id)
                if kalshi_order:
                    # Update DB with actual status
                    if kalshi_order.status.value != "PENDING":
                        order.status = kalshi_order.status.value
                        order.filled_shares = float(kalshi_order.filled_shares)
                        order.fill_price = float(kalshi_order.fill_price)
                        updated_count += 1
                        logger.info(
                            "[ORDER_MGMT] Updated order %s status: PENDING -> %s (filled: %d shares)",
                            order.order_id[:8],
                            kalshi_order.status.value,
                            int(order.filled_shares),
                        )
            except Exception as e:
                logger.warning(
                    "[ORDER_MGMT] Failed to check order %s status: %s",
                    order.order_id[:8],
                    e,
                )

        if updated_count > 0:
            session.commit()
            logger.info("[ORDER_MGMT] Updated %d pending order statuses from Kalshi", updated_count)

    return updated_count


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


def cancel_partially_filled_orders(
    db_engine: Engine,
    adapter,
    instance_name: str,
    ticker: str,
) -> int:
    """Cancel unfilled portions of partially filled PENDING orders for a specific ticker.

    This prevents double-ordering when rebalancing. If an order for 50 shares is only
    partially filled with 20 shares, we cancel the remaining 30 before placing a new order.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders
        ticker: Specific ticker to cancel orders for

    Returns:
        Number of orders cancelled
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    cancelled_count = 0

    with get_session(db_engine) as session:
        pending_orders = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.status == "PENDING",
                BettingOrder.ticker == ticker,
            )
            .all()
        )

        for order in pending_orders:
            filled = order.filled_shares or 0
            requested = order.count
            unfilled = requested - filled

            # Log whether this is fully unfilled or partially filled
            if filled > 0 and unfilled > 0:
                logger.info(
                    "[ORDER_MGMT] Cancelling partially filled order %s: %d/%d filled, cancelling %d unfilled for %s",
                    order.order_id[:8],
                    int(filled),
                    requested,
                    int(unfilled),
                    ticker,
                )
            elif unfilled > 0:
                logger.info(
                    "[ORDER_MGMT] Cancelling unfilled order %s: 0/%d filled for %s",
                    order.order_id[:8],
                    requested,
                    ticker,
                )

            try:
                # Try to cancel on exchange
                if order.exchange_order_id and unfilled > 0:
                    try:
                        adapter.cancel_order(order.exchange_order_id)
                    except Exception as e:
                        logger.warning(
                            "[ORDER_MGMT] Failed to cancel order %s on exchange (may already be filled/cancelled): %s",
                            order.order_id[:8],
                            e,
                        )

                # Mark as cancelled in database
                order.status = "CANCELLED"
                cancelled_count += 1

            except Exception as e:
                logger.error("[ORDER_MGMT] Error cancelling order %s: %s", order.order_id[:8], e)

        if cancelled_count > 0:
            session.commit()
            logger.info(
                "[ORDER_MGMT] Cancelled %d pending order(s) for %s before rebalancing",
                cancelled_count,
                ticker,
            )

    return cancelled_count


def reconcile_positions_with_kalshi(
    db_engine: Engine,
    adapter,
    instance_name: str,
    tolerance_contracts: int = 5,
    sync_pending_orders: bool = True,
) -> dict[str, tuple[int, int]]:
    """Compare database positions with Kalshi reality and report discrepancies.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders
        tolerance_contracts: Number of contracts difference to tolerate before alerting
        sync_pending_orders: If True, check and update status of pending orders from Kalshi

    Returns:
        Dict of ticker -> (db_qty, kalshi_qty) for positions with drift > tolerance
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    # First, sync pending order statuses with Kalshi if requested
    if sync_pending_orders:
        _sync_pending_order_status(db_engine, adapter, instance_name)

    # Get database positions by replaying orders
    from position_replay import replay_orders_by_ticker

    with get_session(db_engine) as session:
        # Only count FILLED orders for position calculation
        orders = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.status == "FILLED",
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
