#!/usr/bin/env python3
"""
Kalshi Sync Service - Independent service to sync positions with Kalshi.

This service runs independently of the main worker and periodically:
1. Syncs pending order statuses with Kalshi
2. Reconciles positions between DB and Kalshi
3. Updates filled/cancelled orders
4. Does NOT trigger new predictions or trades

Usage:
    python services/kalshi_sync_service.py
    python services/kalshi_sync_service.py --instance Haifeng --interval 1800
    python services/kalshi_sync_service.py --once  # Run once and exit

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (required)
    KALSHI_API_KEY_ID         — Kalshi API key ID
    KALSHI_PRIVATE_KEY_B64    — Base64-encoded RSA private key
    KALSHI_BASE_URL           — Kalshi API base URL
    SYNC_INTERVAL_SEC         — Seconds between sync cycles (default: 1800 = 30 minutes)
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
        # 1. Sync pending order statuses
        logger.info("[SYNC] Checking pending order statuses with Kalshi...")
        updated = _sync_pending_order_status(db_engine, adapter, instance_name)
        results["pending_orders_updated"] = updated
        if updated > 0:
            log_sync_event(
                db_engine,
                "INFO",
                f"Updated {updated} pending order statuses from Kalshi",
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
            tolerance_contracts=5,
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
                "WARNING",
                f"Position drifts detected: {drift_msg}",
                instance_name,
            )

        # 4. Update market prices from Kalshi for active positions
        if not dry_run:
            _update_market_prices(db_engine, adapter, instance_name)

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
        from position_replay import replay_orders_by_ticker

        # Get active positions
        with get_session(db_engine) as session:
            orders = (
                session.query(BettingOrder)
                .filter(
                    BettingOrder.instance_name == instance_name,
                    BettingOrder.status == "FILLED",
                )
                .order_by(BettingOrder.created_at.asc())
                .all()
            )

        positions = replay_orders_by_ticker(orders)
        active_tickers = [
            ticker
            for ticker, pos in positions.items()
            if pos.current_position()[0] is not None and pos.current_position()[1] > 0
        ]

        if not active_tickers:
            return

        logger.info("[SYNC] Updating prices for %d active positions", len(active_tickers))

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

    cycle_count = 0
    while not _shutdown_requested:
        cycle_count += 1
        cycle_start = datetime.now(UTC)

        try:
            logger.info("[SYNC] Starting sync cycle #%d", cycle_count)
            results = sync_with_kalshi(db_engine, adapter, instance_name, dry_run)

            # Log heartbeat
            log_sync_event(
                db_engine,
                "HEARTBEAT",
                f"Cycle #{cycle_count} complete",
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

        # Wait for next cycle
        cycle_duration = (datetime.now(UTC) - cycle_start).total_seconds()
        sleep_time = max(0, interval_sec - cycle_duration)

        if sleep_time > 0 and not _shutdown_requested:
            logger.info(
                "[SYNC] Cycle took %.1fs, sleeping %.1fs until next cycle",
                cycle_duration,
                sleep_time,
            )
            # Sleep in small intervals to check for shutdown
            for _ in range(int(sleep_time)):
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