#!/usr/bin/env python3
"""
Kalshi Sync Service - Independent service to sync positions with Kalshi.

This service runs independently of the main worker on its own interval:
1. Syncs exchange-backed order statuses with Kalshi
2. Reconciles positions between DB and Kalshi
3. Updates filled/cancelled orders
4. Does NOT trigger new predictions or trades

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
    _sync_pending_order_status,
)

load_dotenv()

logger = logging.getLogger("kalshi_sync")

# Configuration
DEFAULT_SYNC_INTERVAL = 1800  # 30 minutes
STALE_ORDER_THRESHOLD_MINUTES = 120  # 2 hours

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
        "position_drifts": {},
        "errors": [],
    }

    try:
        # 1. Sync exchange-backed order statuses
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

        # 3. Reconcile positions with Kalshi (but don't sync pending orders again)
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

        # 4. Update market prices from Kalshi for active positions
        if not dry_run:
            _update_market_prices(db_engine, adapter, instance_name)
            _alert_on_position_snapshot_mismatch(db_engine, adapter, instance_name)

        logger.info(
            "[SYNC] Sync complete: %d orders updated, %d cancelled, %d drifts",
            results["pending_orders_updated"],
            results["stale_orders_cancelled"],
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
    """Emit an alert if local position views diverge from live Kalshi."""
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import TradingPosition
        from position_replay import replay_orders_by_ticker

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
            order_rows = (
                session.query(BettingOrder)
                .filter(
                    BettingOrder.instance_name == instance_name,
                    BettingOrder.status == "FILLED",
                )
                .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
                .all()
            )
            snapshot_rows = (
                session.query(TradingPosition)
                .filter(TradingPosition.instance_name == instance_name)
                .all()
            )

        ledger_positions = {}
        for ticker, pos in replay_orders_by_ticker(order_rows).items():
            side, qty, _avg = pos.current_position()
            if side is None or qty <= 1e-9:
                continue
            signed_qty = qty if side == "yes" else -qty
            ledger_positions[ticker] = int(round(signed_qty))

        snapshot_positions = {}
        for row in snapshot_rows:
            ticker = row.market_id.split("kalshi:", 1)[1] if row.market_id.startswith("kalshi:") else row.market_id
            signed_qty = row.quantity if (row.contract or "").lower() == "yes" else -row.quantity
            if abs(signed_qty) <= 1e-9:
                continue
            snapshot_positions[ticker] = int(round(signed_qty))

        mismatches = []
        all_tickers = set(live_positions.keys()) | set(snapshot_positions.keys()) | set(ledger_positions.keys())
        for ticker in sorted(all_tickers):
            live_qty = live_positions.get(ticker, 0)
            snapshot_qty = snapshot_positions.get(ticker, 0)
            ledger_qty = ledger_positions.get(ticker, 0)
            if live_qty != snapshot_qty or live_qty != ledger_qty:
                mismatches.append(
                    f"{ticker}: ledger={ledger_qty} snapshot={snapshot_qty} live={live_qty}"
                )

        if mismatches:
            message = "Kalshi position mismatch: " + ", ".join(mismatches[:10])
            logger.error("[SYNC] %s", message)
            log_sync_event(db_engine, "ERROR", message, instance_name)
        else:
            logger.info("[SYNC] local ledger and trading_positions match live Kalshi positions")

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
    from ai_prophet_core.betting.config import KalshiConfig
    from ai_prophet_core.betting.db import create_db_engine

    # Initialize database
    db_engine = create_db_engine()

    # Initialize Kalshi adapter
    kalshi_config = KalshiConfig.from_env()
    adapter = KalshiAdapter(
        api_key_id=kalshi_config.api_key_id,
        private_key_base64=kalshi_config.private_key_base64,
        base_url=kalshi_config.base_url,
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
            time.sleep(seconds_until_next_sync)

    cycle_count = 0
    while not _shutdown_requested:
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

            # Sleep in small intervals to check for shutdown
            for _ in range(seconds_until_next_sync):
                if _shutdown_requested:
                    break
                time.sleep(1)

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
