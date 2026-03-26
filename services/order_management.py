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
    """Check status of exchange-backed orders with Kalshi and update DB accordingly.

    ALSO updates positions in trading_positions table when orders change status.

    Args:
        db_engine: Database engine
        adapter: Exchange adapter (KalshiAdapter)
        instance_name: Instance name to filter orders

    Returns:
        Number of orders updated
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder
    from db_models import TradingPosition
    from position_replay import replay_orders_by_ticker
    from datetime import datetime
    updated_count = 0
    tickers_with_updates = set()

    with get_session(db_engine) as session:
        orders_to_sync = (
            session.query(BettingOrder)
            .filter(
                BettingOrder.instance_name == instance_name,
                BettingOrder.exchange_order_id.isnot(None),
            )
            .all()
        )

        for order in orders_to_sync:
            try:
                # Get current order status from Kalshi
                kalshi_order = adapter.get_order(order.exchange_order_id)
                if kalshi_order:
                    # Update DB with actual status
                    old_status = order.status
                    old_filled = float(order.filled_shares or 0)
                    new_status = kalshi_order.status.value
                    new_filled = float(kalshi_order.filled_shares or 0)
                    new_fill_price = float(kalshi_order.fill_price or 0)
                    new_fee = float(kalshi_order.fee or 0)

                    if (
                        old_status != new_status
                        or abs(old_filled - new_filled) > 0.0001
                        or float(order.fill_price or 0) != new_fill_price
                        or float(order.fee_paid or 0) != new_fee
                    ):
                        previous_status = order.status
                        order.status = new_status
                        order.filled_shares = new_filled
                        order.fill_price = new_fill_price
                        order.fee_paid = new_fee
                        updated_count += 1
                        tickers_with_updates.add(order.ticker)  # Track which tickers changed
                        logger.info(
                            "[ORDER_MGMT] Updated order %s status: %s -> %s (filled: %d -> %d shares)",
                            order.order_id[:8],
                            previous_status,
                            new_status,
                            int(old_filled),
                            int(new_filled),
                        )
                    else:
                        order.status = new_status
                        order.filled_shares = new_filled
                        order.fill_price = new_fill_price
                        order.fee_paid = new_fee
            except Exception as e:
                logger.warning(
                    "[ORDER_MGMT] Failed to check order %s status: %s",
                    order.order_id[:8],
                    e,
                )

        if updated_count > 0:
            session.commit()
            logger.info("[ORDER_MGMT] Updated %d order statuses from Kalshi", updated_count)

            # CRITICAL: Update positions for tickers that had order changes
            if tickers_with_updates:
                logger.info("[ORDER_MGMT] Updating positions for %d tickers with order changes", len(tickers_with_updates))

                # Get all FILLED orders for affected tickers
                filled_orders = (
                    session.query(BettingOrder)
                    .filter(
                        BettingOrder.instance_name == instance_name,
                        BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
                        BettingOrder.ticker.in_(tickers_with_updates)
                    )
                    .order_by(BettingOrder.created_at.asc())
                    .all()
                )

                # Replay orders to calculate positions
                positions = replay_orders_by_ticker(filled_orders)
                now = datetime.now(UTC)

                # Update trading_positions table
                for ticker in tickers_with_updates:
                    market_id = f"kalshi:{ticker}"
                    pos = positions.get(ticker)

                    # Get existing position entry
                    existing = session.query(TradingPosition).filter_by(
                        instance_name=instance_name,
                        market_id=market_id
                    ).first()

                    if pos:
                        side, qty, avg_price = pos.current_position()

                        if side and qty > 0.001:
                            # Position exists - update or create
                            if existing:
                                existing.contract = side
                                existing.quantity = int(qty)
                                existing.avg_price = round(avg_price, 4)
                                existing.realized_pnl = round(pos.realized_pnl, 4)
                                existing.realized_trades = pos.realized_trades
                                existing.updated_at = now
                                logger.info("[ORDER_MGMT] Updated position for %s: %d %s shares", ticker, int(qty), side)
                            else:
                                session.add(TradingPosition(
                                    instance_name=instance_name,
                                    market_id=market_id,
                                    contract=side,
                                    quantity=int(qty),
                                    avg_price=round(avg_price, 4),
                                    realized_pnl=round(pos.realized_pnl, 4),
                                    unrealized_pnl=0.0,
                                    max_position=pos.max_position,
                                    realized_trades=pos.realized_trades,
                                    created_at=now,
                                    updated_at=now,
                                ))
                                logger.info("[ORDER_MGMT] Created position for %s: %d %s shares", ticker, int(qty), side)
                        else:
                            # Position closed - delete if exists
                            if existing:
                                session.delete(existing)
                                logger.info("[ORDER_MGMT] Deleted closed position for %s", ticker)
                    else:
                        # No position - delete if exists
                        if existing:
                            session.delete(existing)
                            logger.info("[ORDER_MGMT] Deleted position for %s (no orders)", ticker)

                session.commit()
                logger.info("[ORDER_MGMT] Updated positions for %d tickers", len(tickers_with_updates))

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
                current_exchange_status = None

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

                    try:
                        current_exchange_status = adapter.get_order(order.exchange_order_id)
                    except Exception as e:
                        logger.warning(
                            "[ORDER_MGMT] Failed to re-check order %s after cancel attempt: %s",
                            order.order_id[:8],
                            e,
                        )

                if current_exchange_status is not None:
                    order.status = current_exchange_status.status.value
                    order.filled_shares = float(current_exchange_status.filled_shares or 0)
                    if current_exchange_status.fill_price is not None:
                        order.fill_price = float(current_exchange_status.fill_price)
                    order.fee_paid = float(current_exchange_status.fee or 0)

                    if order.status == "PENDING":
                        logger.info(
                            "[ORDER_MGMT] Order %s is still pending on Kalshi; leaving it pending in DB",
                            order.order_id[:8],
                        )
                        continue

                # If we could not confirm a different live status, fall back to local cancellation.
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

    # First, sync exchange-backed order statuses with Kalshi if requested
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

    # AUTO-CORRECT DRIFTS: Trust Kalshi as the source of truth
    if drifts:
        logger.warning("[ORDER_MGMT] Found %d position drifts - AUTO-CORRECTING by trusting Kalshi", len(drifts))

        from db_models import TradingPosition, SystemLog
        from datetime import datetime

        corrected = 0
        with get_session(db_engine) as session:
            for ticker, (db_qty, kalshi_qty) in drifts.items():
                market_id = f"kalshi:{ticker}"

                # Get existing position entry
                existing_pos = session.query(TradingPosition).filter_by(
                    instance_name=instance_name,
                    market_id=market_id
                ).first()

                if kalshi_qty == 0:
                    # Kalshi shows no position - delete local position
                    if existing_pos:
                        session.delete(existing_pos)
                        logger.info(
                            "[ORDER_MGMT] AUTO-CORRECTED: Deleted position for %s (Kalshi=0, was DB=%d)",
                            ticker, db_qty
                        )
                        corrected += 1
                else:
                    # Find the Kalshi position details
                    kalshi_pos_details = None
                    for pos in kalshi_positions_raw:
                        if pos.get("ticker") == ticker:
                            kalshi_pos_details = pos
                            break

                    if kalshi_pos_details:
                        position_fp = float(kalshi_pos_details.get("position_fp", 0))
                        position_side = "yes" if position_fp > 0 else "no"
                        position_qty = abs(int(position_fp))
                        avg_price = float(kalshi_pos_details.get("average_price_fp", 0.5))

                        if existing_pos:
                            # Update existing position
                            existing_pos.contract = position_side
                            existing_pos.quantity = position_qty
                            existing_pos.avg_price = avg_price
                            existing_pos.updated_at = datetime.now(UTC)
                            logger.info(
                                "[ORDER_MGMT] AUTO-CORRECTED: %s DB=%d → Kalshi=%d %s shares",
                                ticker, db_qty, position_qty, position_side
                            )
                        else:
                            # Create new position
                            session.add(TradingPosition(
                                instance_name=instance_name,
                                market_id=market_id,
                                contract=position_side,
                                quantity=position_qty,
                                avg_price=avg_price,
                                realized_pnl=0.0,
                                unrealized_pnl=0.0,
                                max_position=position_qty,
                                realized_trades=0,
                                created_at=datetime.now(UTC),
                                updated_at=datetime.now(UTC),
                            ))
                            logger.info(
                                "[ORDER_MGMT] AUTO-CORRECTED: Created %s position: %d %s shares",
                                ticker, position_qty, position_side
                            )
                        corrected += 1

            if corrected > 0:
                # Log the correction event
                session.add(SystemLog(
                    instance_name=instance_name,
                    level="WARNING",
                    message=f"Auto-corrected {corrected} position drifts by syncing with Kalshi",
                    component="order_mgmt",
                    created_at=datetime.now(UTC),
                ))
                session.commit()
                logger.info("[ORDER_MGMT] Successfully corrected %d/%d position drifts", corrected, len(drifts))
                # Clear drifts since they're fixed
                drifts = {}

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
