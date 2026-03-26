"""Order management utilities for handling stale orders and position reconciliation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal

UTC = timezone.utc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)


def _fallback_request_from_db_order(order):
    """Build a best-effort OrderRequest for polling when Kalshi omits fill fields."""
    from ai_prophet_core.betting.adapters.base import OrderRequest

    limit_price = Decimal(str(float(order.price_cents or 0) / 100.0))
    shares = Decimal(str(float(order.count or 0)))
    return OrderRequest(
        order_id=order.order_id or "poll",
        intent_id=order.order_id or "poll",
        market_id=f"kalshi:{order.ticker}" if getattr(order, "ticker", None) else "",
        exchange_ticker=order.ticker or "",
        action=(getattr(order, "action", None) or "BUY").upper(),
        side=(getattr(order, "side", None) or "yes").upper(),
        shares=shares if shares > 0 else Decimal("1"),
        limit_price=limit_price if limit_price > 0 else Decimal("0.50"),
    )


def _sync_pending_order_status(
    db_engine: Engine,
    adapter,
    instance_name: str,
) -> int:
    """Refresh local betting_orders from the latest recorded Kalshi snapshots."""
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder
    from kalshi_state import (
        record_kalshi_state,
        sync_betting_orders_from_snapshots,
        sync_trading_positions_from_snapshots,
    )

    with get_session(db_engine) as session:
        record_kalshi_state(session, adapter, instance_name)
        updated_count = sync_betting_orders_from_snapshots(session, BettingOrder, instance_name)
        position_updates = sync_trading_positions_from_snapshots(session, instance_name)

    if updated_count > 0:
        logger.info("[ORDER_MGMT] Updated %d order statuses from Kalshi snapshots", updated_count)
    if position_updates > 0:
        logger.info("[ORDER_MGMT] Updated %d trading_positions rows from Kalshi snapshots", position_updates)

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
                        current_exchange_status = adapter.get_order(
                            order.exchange_order_id,
                            fallback_request=_fallback_request_from_db_order(order),
                        )
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
                current_exchange_status = None
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

                    try:
                        current_exchange_status = adapter.get_order(
                            order.exchange_order_id,
                            fallback_request=_fallback_request_from_db_order(order),
                        )
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

                if order.status != "CANCELLED":
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
    """Compare cached trading_positions with the latest recorded Kalshi state.

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
    from db_models import SystemLog, TradingPosition
    from kalshi_state import (
        build_position_views,
        record_kalshi_state,
        sync_trading_positions_from_snapshots,
    )

    # First, sync exchange-backed order statuses with Kalshi if requested
    if sync_pending_orders:
        _sync_pending_order_status(db_engine, adapter, instance_name)

    with get_session(db_engine) as session:
        record_kalshi_state(session, adapter, instance_name)
        kalshi_views = build_position_views(session, instance_name)
        snapshot_rows = (
            session.query(TradingPosition)
            .filter(TradingPosition.instance_name == instance_name)
            .all()
        )
        kalshi_positions = {
            view.ticker: int(round(view.quantity if view.contract == "yes" else -view.quantity))
            for view in kalshi_views
        }

    snapshot_positions = {}
    for row in snapshot_rows:
        ticker = row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id
        signed_qty = row.quantity if (row.contract or "").lower() == "yes" else -row.quantity
        snapshot_positions[ticker] = int(round(signed_qty))

    # Compare
    drifts = {}
    all_tickers = set(snapshot_positions.keys()) | set(kalshi_positions.keys())

    for ticker in all_tickers:
        db_qty = snapshot_positions.get(ticker, 0)

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

    if drifts:
        logger.critical(
            "[ORDER_MGMT] CRITICAL: Found %d position drifts - This should NOT happen! AUTO-CORRECTING immediately",
            len(drifts)
        )
    with get_session(db_engine) as session:
        corrected = sync_trading_positions_from_snapshots(session, instance_name)

        if corrected > 0:
            drift_details = ", ".join([f"{t}: DB={d[0]} Kalshi={d[1]}" for t, d in list(drifts.items())[:5]])
            session.add(SystemLog(
                instance_name=instance_name,
                level="CRITICAL",
                message=f"AUTO-CORRECTED {corrected} CRITICAL position drifts: {drift_details}",
                component="order_mgmt",
                created_at=datetime.now(UTC),
            ))
            session.commit()
            logger.info("[ORDER_MGMT] ✓ Successfully AUTO-CORRECTED %d position drifts", corrected)

            # If we had to correct positions, force a full state snapshot
            logger.info("[ORDER_MGMT] Recording full Kalshi state after position corrections...")
            record_kalshi_state(session, adapter, instance_name)

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
