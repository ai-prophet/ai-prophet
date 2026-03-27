#!/usr/bin/env python3
"""
Kalshi Sync Service - Independent service to sync positions with Kalshi.

This service runs independently of the main worker on its own interval:
1. Syncs exchange-backed order statuses with Kalshi
2. Reconciles positions between DB and Kalshi
3. Updates filled/cancelled orders
4. May complete a previously-approved deferred flip buy after its sell leg fills
5. Does NOT trigger new predictions or fresh model-driven trades

Runs at the next UTC-aligned boundary for the configured interval.

Usage:
    python services/kalshi_sync_service.py
    python services/kalshi_sync_service.py --instance Haifeng
    python services/kalshi_sync_service.py --once  # Run once and exit

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (required)
    KALSHI_API_KEY_ID         — Kalshi API key ID
    KALSHI_PRIVATE_KEY_B64    — Base64-encoded RSA private key
    KALSHI_BASE_URL           — Kalshi API base URL
    SYNC_INTERVAL_SEC         — Sync cadence in seconds (default: 1800 = 30 min)
    WORKER_POLL_INTERVAL_SEC  — Worker cadence in seconds (default: 14400 = 4 hours)
    SYNC_WORKER_BUFFER_SEC    — Suppress standalone sync within this many seconds
                                 before/after worker boundaries (default: 900 = 15 min)
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone

UTC = timezone.utc

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from instance_config import get_current_instance_name, get_instance_env
from order_management import (
    cancel_stale_orders,
    reconcile_positions_with_kalshi,
    resume_deferred_flip_buys,
    _sync_pending_order_status,
)

load_dotenv()

logger = logging.getLogger("kalshi_sync")

# Configuration
DEFAULT_SYNC_INTERVAL = 1800  # 30 minutes
STALE_ORDER_THRESHOLD_MINUTES = 120  # 2 hours
DEFAULT_WORKER_POLL_INTERVAL = 4 * 60 * 60  # 4 hours
DEFAULT_SYNC_WORKER_BUFFER_SEC = 15 * 60  # 15 minutes

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)


def _next_sync_boundary(now: datetime, interval_sec: int) -> datetime:
    """Return the next UTC-aligned sync boundary for the configured interval."""
    interval = max(1, int(interval_sec))
    now_ts = int(now.timestamp())
    next_ts = ((now_ts // interval) + 1) * interval
    return datetime.fromtimestamp(next_ts, tz=UTC)


def _previous_boundary(now: datetime, interval_sec: int) -> datetime:
    """Return the previous UTC-aligned boundary for the configured interval."""
    interval = max(1, int(interval_sec))
    now_ts = int(now.timestamp())
    prev_ts = (now_ts // interval) * interval
    return datetime.fromtimestamp(prev_ts, tz=UTC)


def _worker_poll_interval(instance_name: str) -> int:
    raw = get_instance_env(
        "WORKER_POLL_INTERVAL_SEC",
        instance_name,
        default=str(DEFAULT_WORKER_POLL_INTERVAL),
    ) or str(DEFAULT_WORKER_POLL_INTERVAL)
    return max(1, int(raw))


def _worker_sync_buffer_sec(instance_name: str) -> int:
    raw = get_instance_env(
        "SYNC_WORKER_BUFFER_SEC",
        instance_name,
        default=str(DEFAULT_SYNC_WORKER_BUFFER_SEC),
    ) or str(DEFAULT_SYNC_WORKER_BUFFER_SEC)
    return max(0, int(raw))


def _latest_worker_cycle_state(db_engine, instance_name: str) -> tuple[datetime | None, datetime | None]:
    """Return latest worker cycle_start and cycle_end heartbeat timestamps."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            latest_start = (
                session.query(SystemLog.created_at)
                .filter(
                    SystemLog.instance_name == instance_name,
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "worker",
                    SystemLog.message == "cycle_start",
                )
                .order_by(SystemLog.created_at.desc())
                .limit(1)
                .scalar()
            )
            latest_end = (
                session.query(SystemLog.created_at)
                .filter(
                    SystemLog.instance_name == instance_name,
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "worker",
                    SystemLog.message == "cycle_end",
                )
                .order_by(SystemLog.created_at.desc())
                .limit(1)
                .scalar()
            )
            return latest_start, latest_end
    except Exception as e:
        logger.debug("[SYNC] Failed to inspect worker cycle state for %s: %s", instance_name, e)
        return None, None


def _sync_defer_until_for_worker(
    db_engine,
    instance_name: str,
    now: datetime,
) -> tuple[datetime | None, str | None]:
    """Return a defer-until timestamp when sync should yield to the worker."""
    worker_interval = _worker_poll_interval(instance_name)
    buffer_sec = _worker_sync_buffer_sec(instance_name)

    latest_start, latest_end = _latest_worker_cycle_state(db_engine, instance_name)
    if latest_start and (latest_end is None or latest_start > latest_end):
        return now + timedelta(seconds=60), "worker cycle is currently running"

    if buffer_sec <= 0:
        return None, None

    previous_boundary = _previous_boundary(now, worker_interval)
    next_boundary = _next_sync_boundary(now, worker_interval)
    post_boundary_deadline = previous_boundary + timedelta(seconds=buffer_sec)
    pre_boundary_start = next_boundary - timedelta(seconds=buffer_sec)

    if now < post_boundary_deadline:
        return post_boundary_deadline, "inside post-worker buffer window"
    if now >= pre_boundary_start:
        return next_boundary + timedelta(seconds=buffer_sec), "inside pre-worker buffer window"

    return None, None


def _sleep_until(target: datetime) -> None:
    """Sleep until *target*, checking for shutdown every second."""
    while not _shutdown_requested:
        remaining = int((target - datetime.now(UTC)).total_seconds())
        if remaining <= 0:
            break
        time.sleep(min(1, remaining))


def log_sync_event(
    db_engine,
    level: str,
    message: str,
    instance_name: str,
) -> None:
    """Log sync events to system_logs table."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                instance_name=instance_name,
                level=level,
                message=message[:2000],
                component="kalshi_sync",
                created_at=datetime.now(UTC),
            ))
    except Exception:
        pass


def sync_with_kalshi(
    db_engine,
    adapter,
    instance_name: str,
    dry_run: bool = False,
) -> dict:
    """Perform a full sync with Kalshi.

    Returns:
        Dict with sync results
    """
    results = {
        "pending_orders_updated": 0,
        "stale_orders_cancelled": 0,
        "deferred_flips_resumed": 0,
        "position_drifts": {},
        "errors": [],
        "realtime_polls": 0,  # Track realtime polling
    }

    try:
        # 1. Enhanced: Poll all pending orders immediately for real-time updates
        logger.info("[SYNC] Polling all pending orders for real-time status updates...")
        realtime_updated = _poll_pending_orders_realtime(db_engine, adapter, instance_name)
        results["realtime_polls"] = realtime_updated
        if realtime_updated > 0:
            log_sync_event(
                db_engine,
                "INFO",
                f"Real-time polling updated {realtime_updated} pending orders",
                instance_name,
            )

        # 2. Sync exchange-backed order statuses (catches any missed by realtime polling)
        logger.info("[SYNC] Checking exchange-backed order statuses with Kalshi...")
        updated = _sync_pending_order_status(db_engine, adapter, instance_name)
        results["pending_orders_updated"] = updated
        if updated > 0:
            log_sync_event(
                db_engine,
                "INFO",
                f"Updated {updated} exchange-backed order statuses from Kalshi",
                instance_name,
            )

        # 2. Cancel stale pending orders
        logger.info("[SYNC] Checking for stale orders...")
        cancelled = cancel_stale_orders(
            db_engine,
            adapter,
            instance_name,
            stale_threshold_minutes=STALE_ORDER_THRESHOLD_MINUTES,
        )
        results["stale_orders_cancelled"] = cancelled
        if cancelled > 0:
            log_sync_event(
                db_engine,
                "INFO",
                f"Cancelled {cancelled} stale pending orders",
                instance_name,
            )

        # 3. Resume any deferred flip buys whose sell leg is now fully resolved.
        logger.info("[SYNC] Checking for deferred flip buys to resume...")
        resumed = resume_deferred_flip_buys(db_engine, adapter, instance_name)
        results["deferred_flips_resumed"] = resumed
        if resumed > 0:
            log_sync_event(
                db_engine,
                "INFO",
                f"Submitted {resumed} deferred flip buy order(s)",
                instance_name,
            )

        # 4. Reconcile positions with Kalshi (but don't sync pending orders again)
        logger.info("[SYNC] Reconciling positions with Kalshi...")
        drifts = reconcile_positions_with_kalshi(
            db_engine,
            adapter,
            instance_name,
            tolerance_contracts=0,
            sync_pending_orders=False,  # Already done above
        )
        results["position_drifts"] = drifts
        if drifts:
            drift_msg = ", ".join(
                f"{ticker}: DB={db_qty} Kalshi={k_qty}"
                for ticker, (db_qty, k_qty) in drifts.items()
            )
            log_sync_event(
                db_engine,
                "ERROR",
                f"Position drifts detected: {drift_msg}",
                instance_name,
            )

        # 5. Update market prices from Kalshi for active positions
        if not dry_run:
            _update_market_prices(db_engine, adapter, instance_name)
            _alert_on_position_snapshot_mismatch(db_engine, adapter, instance_name)

        logger.info(
            "[SYNC] Sync complete: %d orders updated, %d cancelled, %d deferred flips resumed, %d drifts",
            results["pending_orders_updated"],
            results["stale_orders_cancelled"],
            results["deferred_flips_resumed"],
            len(results["position_drifts"]),
        )

    except Exception as e:
        logger.error("[SYNC] Error during sync: %s", e, exc_info=True)
        results["errors"].append(str(e))
        log_sync_event(
            db_engine,
            "ERROR",
            f"Sync failed: {e}",
            instance_name,
        )

    return results


def _poll_pending_orders_realtime(
    db_engine,
    adapter,
    instance_name: str,
) -> int:
    """Poll all pending orders for immediate status updates.

    This provides real-time order status updates independent of the snapshot sync.
    Critical for catching fills/cancellations as they happen.

    Returns:
        Number of orders updated
    """
    from ai_prophet_core.betting.db import get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    updated_count = 0

    try:
        with get_session(db_engine) as session:
            # Get all pending orders
            pending_orders = (
                session.query(BettingOrder)
                .filter(
                    BettingOrder.instance_name == instance_name,
                    BettingOrder.status == "PENDING"
                )
                .all()
            )

            if not pending_orders:
                logger.debug("[REALTIME] No pending orders to poll")
                return 0

            logger.info("[REALTIME] Polling %d pending orders for status updates", len(pending_orders))

            for order in pending_orders:
                if not order.exchange_order_id:
                    logger.debug("[REALTIME] Skipping order %s - no exchange ID", order.order_id[:8])
                    continue

                try:
                    # Poll Kalshi directly for current status
                    order_status = adapter.get_order(order.exchange_order_id)

                    if order_status is None:
                        logger.debug("[REALTIME] No status returned for order %s", order.order_id[:8])
                        continue

                    # Check if status changed from PENDING
                    new_status = order_status.status.value
                    if new_status != "PENDING":
                        # Update order with latest information
                        order.status = new_status
                        order.filled_shares = float(order_status.filled_shares or 0)
                        order.fill_price = float(order_status.fill_price or 0)
                        order.fee_paid = float(order_status.fee or 0)
                        updated_count += 1

                        logger.info(
                            "[REALTIME] Order %s updated: %s → %s (filled=%d shares @ $%.2f)",
                            order.order_id[:8],
                            "PENDING",
                            new_status,
                            order.filled_shares,
                            order.fill_price
                        )
                    elif order_status.filled_shares and float(order_status.filled_shares) > float(order.filled_shares or 0):
                        # Partial fill update
                        order.filled_shares = float(order_status.filled_shares)
                        order.fill_price = float(order_status.fill_price or 0)
                        order.fee_paid = float(order_status.fee or 0)
                        updated_count += 1

                        logger.info(
                            "[REALTIME] Order %s partial fill: %d shares @ $%.2f",
                            order.order_id[:8],
                            order.filled_shares,
                            order.fill_price
                        )

                except Exception as e:
                    logger.warning(
                        "[REALTIME] Failed to poll order %s: %s",
                        order.order_id[:8], e
                    )

            if updated_count > 0:
                session.commit()
                logger.info("[REALTIME] Updated %d orders from real-time polling", updated_count)

    except Exception as e:
        logger.error("[REALTIME] Error during real-time polling: %s", e, exc_info=True)

    return updated_count


def _update_market_prices(db_engine, adapter, instance_name: str) -> None:
    """Update market prices from Kalshi for active positions."""
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import TradingMarket

        # Use both live Kalshi positions and pending orders as the source of truth
        # for markets whose live prices must stay fresh in the dashboard.
        live_position_tickers = [
            pos.get("ticker")
            for pos in adapter.get_positions()
            if pos.get("ticker") and abs(float(pos.get("position_fp", 0) or 0)) > 0
        ]

        with get_session(db_engine) as session:
            pending_tickers = [
                ticker for (ticker,) in (
                    session.query(BettingOrder.ticker)
                    .filter(
                        BettingOrder.instance_name == instance_name,
                        BettingOrder.status == "PENDING",
                    )
                    .distinct()
                    .all()
                )
                if ticker
            ]

        active_tickers = sorted(set(live_position_tickers) | set(pending_tickers))

        if not active_tickers:
            return

        logger.info("[SYNC] Updating live prices for %d active/pending markets", len(active_tickers))

        # Fetch current prices from Kalshi
        for ticker in active_tickers:
            try:
                market_data = adapter.get_market(ticker)
                if market_data and "last_price" in market_data:
                    # Update in DB
                    with get_session(db_engine) as session:
                        market = session.query(TradingMarket).filter_by(
                            instance_name=instance_name,
                            ticker=ticker,
                        ).first()
                        if market:
                            market.last_price = market_data.get("last_price")
                            market.yes_ask = market_data.get("yes_ask")
                            market.no_ask = market_data.get("no_ask")
                            market.yes_bid = market_data.get("yes_bid")
                            market.no_bid = market_data.get("no_bid")
                            market.volume_24h = market_data.get("volume", 0)
                            market.updated_at = datetime.now(UTC)
                            logger.debug(
                                "[SYNC] Updated prices for %s: yes_ask=%.2f, no_ask=%.2f",
                                ticker,
                                market_data.get("yes_ask", 0),
                                market_data.get("no_ask", 0),
                            )
            except Exception as e:
                logger.warning("[SYNC] Failed to update prices for %s: %s", ticker, e)

    except Exception as e:
        logger.error("[SYNC] Failed to update market prices: %s", e)


def _alert_on_position_snapshot_mismatch(db_engine, adapter, instance_name: str) -> None:
    """Emit an alert only when the cached snapshot itself diverges from live Kalshi."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingPosition
        from kalshi_state import get_latest_position_snapshots

        live_positions = {}
        for pos in adapter.get_positions():
            ticker = pos.get("ticker")
            if not ticker:
                continue
            signed_qty = float(pos.get("position_fp", 0) or 0)
            if abs(signed_qty) <= 1e-9:
                continue
            live_positions[ticker] = int(round(signed_qty))

        with get_session(db_engine) as session:
            kalshi_snapshots = get_latest_position_snapshots(session, instance_name)
            snapshot_positions = {}
            for row in (
                session.query(TradingPosition)
                .filter(TradingPosition.instance_name == instance_name)
                .all()
            ):
                ticker = row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id
                signed_qty = row.quantity if (row.contract or "").lower() == "yes" else -row.quantity
                if abs(signed_qty) <= 1e-9:
                    continue
                snapshot_positions[ticker] = int(round(signed_qty))

        mismatches = []
        all_tickers = set(live_positions.keys()) | set(snapshot_positions.keys()) | set(kalshi_snapshots.keys())
        for ticker in sorted(all_tickers):
            live_qty = live_positions.get(ticker, 0)
            snapshot_qty = snapshot_positions.get(ticker, 0)
            # Fix: Safely get snapshot and handle None case
            kalshi_snapshot = kalshi_snapshots.get(ticker)
            kalshi_snapshot_qty = int(round(kalshi_snapshot.signed_quantity)) if kalshi_snapshot else 0
            if live_qty != snapshot_qty or live_qty != kalshi_snapshot_qty:
                mismatches.append(f"{ticker}: snapshot={snapshot_qty} recorded={kalshi_snapshot_qty} live={live_qty}")

        if mismatches:
            message = "Kalshi position mismatch: " + ", ".join(mismatches[:10])
            logger.critical("[SYNC] CRITICAL: %s - AUTO-CORRECTING NOW", message)

            # IMMEDIATELY fix the mismatches - Kalshi is ALWAYS right
            from kalshi_state import sync_trading_positions_from_snapshots
            with get_session(db_engine) as session:
                corrected = sync_trading_positions_from_snapshots(session, instance_name)
                if corrected > 0:
                    logger.info("[SYNC] AUTO-CORRECTED %d positions to match Kalshi truth", corrected)
                    session.add(SystemLog(
                        instance_name=instance_name,
                        level="CRITICAL",
                        message=f"AUTO-CORRECTED {corrected} position mismatches: {message}",
                        component="position_auto_correct",
                        created_at=datetime.now(UTC),
                    ))
                    session.commit()
        else:
            logger.info("[SYNC] ✓ All positions match between DB and live Kalshi")

    except Exception as e:
        logger.error("[SYNC] Failed to compare trading_positions against live Kalshi: %s", e)
        log_sync_event(
            db_engine,
            "ERROR",
            f"Failed to compare trading_positions against live Kalshi: {e}",
            instance_name,
        )


def run_sync_loop(
    instance_name: str,
    interval_sec: int,
    run_once: bool = False,
    dry_run: bool = False,
) -> None:
    """Main sync loop."""
    from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
    from ai_prophet_core.betting.config import DEFAULT_KALSHI_BASE_URL, KALSHI_BASE_URL_ENV
    from ai_prophet_core.betting.db import create_db_engine
    from ai_prophet_core.betting.db_schema import Base as CoreBase
    from db_models import (  # noqa: F401
        KalshiBalanceSnapshot,
        KalshiOrderSnapshot,
        KalshiPositionSnapshot,
        MarketPriceSnapshot,
        ModelRun,
        SystemLog,
        TradingMarket,
        TradingPosition,
    )

    # Initialize database
    db_engine = create_db_engine()
    CoreBase.metadata.create_all(db_engine, checkfirst=True)

    # Initialize Kalshi adapter with instance-aware credentials so each sync
    # service talks to the correct Kalshi account.
    adapter = KalshiAdapter(
        api_key_id=get_instance_env("KALSHI_API_KEY_ID", instance_name, default="") or "",
        private_key_base64=get_instance_env("KALSHI_PRIVATE_KEY_B64", instance_name, default="") or "",
        base_url=get_instance_env(
            KALSHI_BASE_URL_ENV,
            instance_name,
            default=DEFAULT_KALSHI_BASE_URL,
        ) or DEFAULT_KALSHI_BASE_URL,
        dry_run=dry_run,
    )

    logger.info(
        "[SYNC] Starting Kalshi sync service for %s (interval=%ds, mode=%s)",
        instance_name,
        interval_sec,
        "DRY_RUN" if dry_run else "LIVE",
    )

    log_sync_event(
        db_engine,
        "INFO",
        f"Sync service started (interval={interval_sec}s)",
        instance_name,
    )

    # Wait for the next interval boundary before starting (unless --once flag)
    if not run_once:
        now = datetime.now(UTC)
        next_sync = _next_sync_boundary(now, interval_sec)
        seconds_until_next_sync = int((next_sync - now).total_seconds())

        logger.info(
            "[SYNC] Waiting until next %d-second boundary: %s UTC (%.0f seconds)",
            interval_sec,
            next_sync.strftime("%H:%M"), seconds_until_next_sync
        )

        if seconds_until_next_sync > 0 and not _shutdown_requested:
            _sleep_until(next_sync)

    cycle_count = 0
    while not _shutdown_requested:
        now = datetime.now(UTC)
        defer_until, defer_reason = _sync_defer_until_for_worker(db_engine, instance_name, now)
        if defer_until is not None:
            delay_sec = max(0, int((defer_until - now).total_seconds()))
            logger.info(
                "[SYNC] Deferring sync because %s. Next sync attempt at %s UTC (%d seconds)",
                defer_reason,
                defer_until.strftime("%H:%M"),
                delay_sec,
            )
            if run_once:
                break
            _sleep_until(defer_until)
            continue

        cycle_count += 1
        cycle_start = datetime.now(UTC)

        try:
            logger.info("[SYNC] Starting sync cycle #%d", cycle_count)
            log_sync_event(
                db_engine,
                "HEARTBEAT",
                "sync_start",
                instance_name,
            )
            results = sync_with_kalshi(db_engine, adapter, instance_name, dry_run)

            # Log heartbeat
            log_sync_event(
                db_engine,
                "HEARTBEAT",
                "sync_end",
                instance_name,
            )

        except Exception as e:
            logger.error("[SYNC] Sync cycle failed: %s", e, exc_info=True)
            log_sync_event(
                db_engine,
                "ERROR",
                f"Sync cycle #{cycle_count} failed: {e}",
                instance_name,
            )

        if run_once:
            logger.info("[SYNC] Single sync complete, exiting")
            break

        # Calculate time until the next configured sync boundary
        now = datetime.now(UTC)
        next_sync = _next_sync_boundary(now, interval_sec)
        seconds_until_next_sync = int((next_sync - now).total_seconds())

        if seconds_until_next_sync > 0 and not _shutdown_requested:
            # Show both UTC and PST times
            try:
                import zoneinfo
                local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                next_sync_local = next_sync.astimezone(local_tz)
                logger.info(
                    "[SYNC] Next sync will run at %s UTC / %s PST (%d seconds)",
                    next_sync.strftime("%H:%M"),
                    next_sync_local.strftime("%H:%M"),
                    seconds_until_next_sync,
                )
            except:
                logger.info(
                    "[SYNC] Next sync will run at %s UTC (%d seconds)",
                    next_sync.strftime("%H:%M"),
                    seconds_until_next_sync,
                )

            _sleep_until(next_sync)

    logger.info("[SYNC] Sync service shutting down")
    adapter.close()
    db_engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Kalshi position sync service")
    parser.add_argument(
        "--instance",
        default=None,
        help="Instance name (defaults to TRADING_INSTANCE_NAME env var)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("SYNC_INTERVAL_SEC", str(DEFAULT_SYNC_INTERVAL))),
        help="Sync interval in seconds (default: 1800 = 30 minutes)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no real changes)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()
    setup_logging(args.verbose)

    instance_name = args.instance or get_current_instance_name()

    try:
        run_sync_loop(
            instance_name=instance_name,
            interval_sec=args.interval,
            run_once=args.once,
            dry_run=args.dry_run,
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error("Fatal error: %s", e, exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
