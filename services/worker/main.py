"""Standalone trading worker — polls Kalshi markets, uses LLM for predictions.

Operates directly against the Kalshi API without requiring the Prophet Arena
server.  Uses an LLM (OpenAI/Anthropic) to analyze markets and produce
probability estimates, then feeds them into the existing BettingEngine.

Usage:
    python services/worker/main.py
    python services/worker/main.py --dry-run     # force dry-run regardless of env
    python services/worker/main.py --once         # run one cycle then exit
    python services/worker/main.py -v             # verbose logging

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (required)
    KALSHI_API_KEY_ID         — Kalshi API key ID
    KALSHI_PRIVATE_KEY_B64    — Base64-encoded RSA private key
    KALSHI_BASE_URL           — Kalshi API base URL
    LIVE_BETTING_ENABLED      — Master kill switch (default: false)
    LIVE_BETTING_DRY_RUN      — Dry-run mode (default: true)
    WORKER_POLL_INTERVAL_SEC  — (Deprecated) Workers now run at the top of each hour
    WORKER_MODELS             — Comma-separated model specs (default: gemini:gemini-3.1-pro-preview)
                                 Providers: openai, anthropic, gemini
                                 Examples: gemini:gemini-3.1-pro-preview, anthropic:claude-sonnet-4-5-20250929
    GOOGLE_API_KEY            — Google AI API key (for gemini provider)
    WORKER_STRATEGY           — Betting strategy: default|rebalancing (default: default)
    WORKER_MAX_MARKETS        — Max NEW markets to fetch per cycle (default: 50)
    WORKER_MAX_ACTIVE_MARKETS — Max total active markets (sticky + new) (default: 50)
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from instance_config import env_suffix, get_current_instance_name, get_instance_env
from position_replay import replay_orders_by_ticker, summarize_replayed_positions

load_dotenv()

logger = logging.getLogger("worker")
INSTANCE_NAME = get_current_instance_name()
PREDICTOR_TIMEOUT_SEC = float(os.getenv("PREDICTOR_TIMEOUT_SEC", "180"))
REMOTE_PREDICT_TIMEOUT_SEC = float(
    os.getenv("REMOTE_PREDICT_TIMEOUT_SEC", str(PREDICTOR_TIMEOUT_SEC + 10))
)


def _instance_setting(key: str, default: str = "") -> str:
    return str(get_instance_env(key, INSTANCE_NAME, default=default) or default)


def _instance_specific_setting(key: str) -> str:
    return os.getenv(f"{key}_{env_suffix(INSTANCE_NAME)}", "")


def _instance_bool_setting(key: str, default: bool) -> bool:
    return _instance_setting(key, "true" if default else "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _instance_int_setting(key: str, default: int) -> int:
    raw = _instance_setting(key, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid integer for %s on instance=%s: %r; using %d",
            key,
            INSTANCE_NAME,
            raw,
            default,
        )
        return default


def _build_instance_env() -> dict[str, str]:
    env_map = dict(os.environ)
    instance_keys = [
        "LIVE_BETTING_ENABLED",
        "LIVE_BETTING_DRY_RUN",
        "KALSHI_API_KEY_ID",
        "KALSHI_PRIVATE_KEY_B64",
        "KALSHI_BASE_URL",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "PREDICTOR_SERVICE_URL",
        "PREDICTOR_API_KEY",
        "WORKER_STRATEGY",
        "WORKER_MAX_MARKETS",
        "WORKER_MAX_ACTIVE_MARKETS",
        "WORKER_MODELS",
        "WORKER_POLL_INTERVAL_SEC",
    ]
    for key in instance_keys:
        value = get_instance_env(key, INSTANCE_NAME, env=env_map)
        if value is not None:
            env_map[key] = value
    return env_map


def _validate_instance_profile_or_raise() -> None:
    expected_profiles = {
        "Haifeng": {
            "models": ["gemini:gemini-3.1-pro-preview"],
            "market_fetcher": True,
            "peers": ["Jibang"],
            "strategy": "rebalancing",
            "max_markets": 50,
            "max_active": 50,
        },
        "Jibang": {
            "models": ["gemini:gemini-3.1-pro-preview:market"],
            "market_fetcher": False,
            "peers": ["Haifeng"],
            "strategy": "rebalancing",
            "max_markets": 50,
            "max_active": 50,
        },
    }
    expected = expected_profiles.get(INSTANCE_NAME)
    if not expected:
        return

    model_specs = [m.strip() for m in _instance_setting("WORKER_MODELS", "").split(",") if m.strip()]
    market_fetcher = _instance_bool_setting("MARKET_FETCHER", True)
    peer_instances = [p.strip() for p in _instance_setting("WORKER_PEER_INSTANCES", "").split(",") if p.strip()]
    strategy_name = _instance_setting("WORKER_STRATEGY", "rebalancing").strip().lower()
    max_markets = _instance_int_setting("WORKER_MAX_MARKETS", 50)
    max_active = _instance_int_setting("WORKER_MAX_ACTIVE_MARKETS", 50)

    issues: list[str] = []
    if model_specs != expected["models"]:
        issues.append(f"models={model_specs} expected={expected['models']}")
    if market_fetcher != expected["market_fetcher"]:
        issues.append(f"market_fetcher={market_fetcher} expected={expected['market_fetcher']}")
    if sorted(peer_instances) != sorted(expected["peers"]):
        issues.append(f"peers={peer_instances} expected={expected['peers']}")
    if strategy_name != expected["strategy"]:
        issues.append(f"strategy={strategy_name} expected={expected['strategy']}")
    if max_markets != expected["max_markets"]:
        issues.append(f"max_markets={max_markets} expected={expected['max_markets']}")
    if max_active != expected["max_active"]:
        issues.append(f"max_active={max_active} expected={expected['max_active']}")

    if not issues:
        return

    message = (
        f"Refusing to start misconfigured worker for instance={INSTANCE_NAME}: "
        + "; ".join(issues)
    )
    logger.error(message)
    try:
        from ai_prophet_core.betting.db import create_db_engine
        db_engine = create_db_engine()
        log_system_event(db_engine, "ERROR", message, instance_name=INSTANCE_NAME)
        db_engine.dispose()
    except Exception:
        pass
    raise SystemExit(2)

# ── Shutdown handling ──────────────────────────────────────────────

_shutdown_requested = False


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ── Logging setup ─────────────────────────────────────────────────

def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)


# ── DB helpers ────────────────────────────────────────────────────

def log_heartbeat(
    db_engine,
    component: str = "worker",
    message: str = "alive",
    instance_name: str = INSTANCE_NAME,
) -> None:
    """Write a heartbeat row to system_logs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                instance_name=instance_name,
                level="HEARTBEAT",
                message=message,
                component=component,
                created_at=datetime.now(UTC),
            ))
    except Exception as e:
        logger.warning("Failed to write heartbeat: %s", e)


def log_system_event(
    db_engine,
    level: str,
    message: str,
    component: str = "worker",
    instance_name: str = INSTANCE_NAME,
) -> None:
    """Write a system event to system_logs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                instance_name=instance_name,
                level=level,
                message=message[:2000],
                component=component,
                created_at=datetime.now(UTC),
            ))
    except Exception:
        pass


def save_price_snapshot(db_engine, market_id: str, ticker: str,
                        yes_ask: float, no_ask: float,
                        volume_24h: float = 0,
                        model_p_yes: float | None = None,
                        model_name: str | None = None,
                        instance_name: str = INSTANCE_NAME) -> None:
    """Record a point-in-time price snapshot for time-series analysis."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import MarketPriceSnapshot

        with get_session(db_engine) as session:
            session.add(MarketPriceSnapshot(
                instance_name=instance_name,
                market_id=market_id,
                ticker=ticker,
                yes_ask=yes_ask,
                no_ask=no_ask,
                volume_24h=volume_24h,
                model_p_yes=model_p_yes,
                model_name=model_name,
                timestamp=datetime.now(UTC),
            ))
    except Exception as e:
        logger.warning("Failed to save price snapshot: %s", e)


def save_market_snapshot(db_engine, market_id: str, title: str, category: str,
                         yes_ask: float, no_ask: float | None = None,
                         yes_bid: float | None = None, no_bid: float | None = None,
                         expiration=None, ticker: str = "",
                         event_ticker: str = "", volume_24h: float = 0,
                         instance_name: str = INSTANCE_NAME) -> None:
    """Upsert a market snapshot into trading_markets."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        # Fall back to complement math if real bid prices not provided
        _no_ask = no_ask if no_ask is not None else (1.0 - yes_ask)
        yes_bid = yes_bid if yes_bid is not None else round(1.0 - _no_ask, 6)
        no_bid = no_bid if no_bid is not None else round(1.0 - yes_ask, 6)

        now = datetime.now(UTC)
        with get_session(db_engine) as session:
            existing = session.query(TradingMarket).filter_by(
                instance_name=instance_name,
                market_id=market_id,
            ).first()
            if existing:
                existing.title = title
                existing.category = category
                existing.last_price = yes_ask
                existing.yes_bid = yes_bid
                existing.yes_ask = yes_ask
                existing.no_bid = no_bid
                existing.no_ask = _no_ask
                existing.ticker = ticker
                existing.event_ticker = event_ticker
                existing.volume_24h = volume_24h
                existing.expiration = expiration
                existing.updated_at = now
            else:
                session.add(TradingMarket(
                    instance_name=instance_name,
                    market_id=market_id,
                    ticker=ticker,
                    event_ticker=event_ticker,
                    title=title,
                    category=category or "unknown",
                    last_price=yes_ask,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    no_bid=no_bid,
                    no_ask=_no_ask,
                    volume_24h=volume_24h,
                    expiration=expiration,
                    updated_at=now,
                ))
    except Exception as e:
        logger.warning("Failed to save market snapshot: %s", e)


def save_model_run(db_engine, model_name: str, market_id: str,
                   decision: str, confidence: float | None,
                   metadata: dict | None = None,
                   instance_name: str = INSTANCE_NAME) -> None:
    """Log a model decision to model_runs."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import ModelRun

        with get_session(db_engine) as session:
            session.add(ModelRun(
                instance_name=instance_name,
                model_name=model_name,
                timestamp=datetime.now(UTC),
                decision=decision,
                confidence=confidence,
                market_id=market_id,
                metadata_json=json.dumps(metadata) if metadata else None,
            ))
    except Exception as e:
        logger.warning("Failed to save model run: %s", e)


def update_positions(db_engine, instance_name: str = INSTANCE_NAME) -> None:
    """Aggregate betting_orders into trading_positions for the dashboard.

    For each ticker with filled/dry-run orders, computes the literal held
    contract side (YES or NO), open quantity, average entry price, and
    realized P&L.
    """
    if db_engine is None:
        return
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import TradingMarket, TradingPosition

        now = datetime.now(UTC)

        with get_session(db_engine) as session:
            market_rows = (
                session.query(TradingMarket)
                .filter(TradingMarket.instance_name == instance_name)
                .all()
            )
            markets_by_ticker = {
                market.ticker: market for market in market_rows if market.ticker
            }

            # Get all filled/dry-run orders grouped by ticker
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == instance_name)
                .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
                .order_by(BettingOrder.created_at.asc())
                .all()
            )

            positions = replay_orders_by_ticker(orders)
            for ticker, pos in positions.items():
                for warning in pos.warnings:
                    logger.warning("Position replay warning for %s (%s): %s", ticker, instance_name, warning)

            active_market_ids = {
                f"kalshi:{ticker}"
                for ticker, pos in positions.items()
                if pos.current_position()[0] is not None and pos.current_position()[1] >= 0.001
            }

            # Drop orphaned snapshot rows so the dashboard mirrors the cleaned
            # order ledger exactly after repairs or manual data cleanup.
            (
                session.query(TradingPosition)
                .filter(TradingPosition.instance_name == instance_name)
                .filter(~TradingPosition.market_id.in_(active_market_ids) if active_market_ids else True)
                .delete(synchronize_session=False)
            )

            # Upsert into trading_positions
            for ticker, pos in positions.items():
                side, qty, avg_price = pos.current_position()
                if side is None or qty < 0.001:
                    continue

                market_id = f"kalshi:{ticker}"
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

                existing = session.query(TradingPosition).filter_by(
                    instance_name=instance_name,
                    market_id=market_id
                ).first()
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
                        market_id=market_id,
                        contract=side,
                        quantity=qty,
                        avg_price=round(avg_price, 4),
                        realized_pnl=round(pos.realized_pnl, 4),
                        unrealized_pnl=round(unrealized, 4),
                        max_position=max(pos.max_position, qty),
                        realized_trades=pos.realized_trades,
                        updated_at=now,
                    ))

        logger.info("Updated %d positions from order history", len(positions))
    except Exception as e:
        logger.warning("Failed to update positions: %s", e)


def sync_pending_orders(db_engine, adapter, instance_name: str) -> int:
    """Sync all PENDING orders with Kalshi to get latest fill counts and statuses.

    CRITICAL: Also re-verify CANCELLED orders that show partial fills, as these
    may have incorrect filled_shares values that cause position discrepancies.

    Returns: number of orders updated
    """
    if db_engine is None:
        return 0

    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from sqlalchemy import or_, and_

        updated_count = 0

        with get_session(db_engine) as session:
            # Get all PENDING orders AND CANCELLED orders with non-zero filled_shares
            # CANCELLED orders with partial fills need verification as they may be wrong
            orders_to_sync = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == instance_name)
                .filter(
                    or_(
                        BettingOrder.status == "PENDING",
                        and_(
                            BettingOrder.status == "CANCELLED",
                            BettingOrder.filled_shares > 0
                        )
                    )
                )
                .all()
            )

            logger.info("[SYNC] Syncing %d orders with Kalshi (PENDING + partially-filled CANCELLED)", len(orders_to_sync))

            for order in orders_to_sync:
                try:
                    # Poll Kalshi for current order status
                    result = adapter.get_order(order.order_id)

                    if result is None:
                        logger.warning("[SYNC] Could not fetch order %s from Kalshi", order.order_id)
                        continue

                    # Update order status and fill count
                    old_status = order.status
                    old_filled = order.filled_shares or 0

                    order.status = result.status.value
                    order.filled_shares = float(result.filled_shares) if result.filled_shares else 0

                    # Update fill_price if we have fills but no price
                    if order.filled_shares > 0 and (order.fill_price is None or order.fill_price == 0):
                        if hasattr(result, 'fill_price') and result.fill_price:
                            order.fill_price = float(result.fill_price)
                        elif hasattr(result, 'avg_fill_price') and result.avg_fill_price:
                            order.fill_price = float(result.avg_fill_price)

                    if old_status != order.status or old_filled != order.filled_shares:
                        # Log critical discrepancies prominently
                        if old_status == "CANCELLED" and abs(old_filled - order.filled_shares) > 0.1:
                            logger.warning(
                                "[SYNC] CRITICAL: CANCELLED order %s had wrong fill count! "
                                "DB showed %s filled but Kalshi says %s filled. Ticker: %s",
                                order.order_id[:8], old_filled, order.filled_shares, order.ticker
                            )

                        logger.info(
                            "[SYNC] Updated order %s: %s -> %s, filled: %s -> %s",
                            order.order_id[:8],
                            old_status,
                            order.status,
                            old_filled,
                            order.filled_shares,
                        )
                        updated_count += 1

                except Exception as e:
                    logger.warning("[SYNC] Failed to sync order %s: %s", order.order_id, e)
                    continue

            session.commit()

        if updated_count > 0:
            logger.info("[SYNC] Updated %d orders from Kalshi", updated_count)

        return updated_count

    except Exception as e:
        logger.error("[SYNC] Failed to sync pending orders: %s", e)
        return 0


def _load_order_ledger_state(
    db_engine,
    adapter,
    instance_name: str,
    dry_run: bool = True,
):
    """Build authoritative position state directly from the order ledger.

    This avoids relying on trading_positions rows that may lag behind the
    actual betting_orders history during the active cycle.

    For DRY_RUN: virtual cash = starting_cash - capital_deployed + realized.
    For LIVE: real balance from Kalshi already reflects all trades.
    """
    if db_engine is None:
        return None

    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder

        with get_session(db_engine) as session:
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == instance_name)
                .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
                .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
                .all()
            )

        positions = replay_orders_by_ticker(orders)
        capital_deployed, total_realized, open_position_count = summarize_replayed_positions(positions)

        if dry_run:
            starting_cash = Decimal(str(_instance_setting("WORKER_STARTING_CASH", "10000")))
            cash = starting_cash - Decimal(str(capital_deployed)) + Decimal(str(total_realized))
        else:
            try:
                cash = adapter.get_balance()
            except Exception:
                cash = Decimal("0")

        return {
            "positions": positions,
            "cash": cash,
            "total_pnl": Decimal(str(total_realized)),
            "position_count": open_position_count,
        }
    except Exception as e:
        logger.debug("Could not build order ledger state: %s", e)
        return None


# ── Sticky market tracking ────────────────────────────────────────

def get_traded_tickers(db_engine, instance_name: str = INSTANCE_NAME) -> set[str]:
    """Return tickers with orders placed in the last 30 days (still relevant)."""
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from sqlalchemy import distinct

        cutoff = datetime.now(UTC) - timedelta(days=30)
        with get_session(db_engine) as session:
            rows = (
                session.query(distinct(BettingOrder.ticker))
                .filter(BettingOrder.instance_name == instance_name)
                .filter(BettingOrder.created_at >= cutoff)
                .all()
            )
            return {r[0] for r in rows}
    except Exception as e:
        logger.warning("Failed to query traded tickers: %s", e)
        return set()


def get_peer_tickers(db_engine, peer_instance_name: str) -> set[str]:
    """Return tickers currently tracked by a peer instance.

    Used by non-fetcher workers to mirror the market list of the designated
    market-fetcher instance instead of independently querying Kalshi.
    """
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            rows = (
                session.query(TradingMarket.ticker)
                .filter(TradingMarket.instance_name == peer_instance_name)
                .all()
            )
            tickers = {r[0] for r in rows if r[0]}
            logger.info("Read %d tickers from peer instance '%s'", len(tickers), peer_instance_name)
            return tickers
    except Exception as e:
        logger.warning("Failed to query peer tickers from '%s': %s", peer_instance_name, e)
        return set()


def get_tracked_tickers(db_engine, instance_name: str = INSTANCE_NAME) -> set[str]:
    """Return all tickers currently in the trading_markets table."""
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            rows = (
                session.query(TradingMarket.ticker)
                .filter(TradingMarket.instance_name == instance_name)
                .all()
            )
            return {r[0] for r in rows if r[0]}
    except Exception as e:
        logger.warning("Failed to query tracked tickers: %s", e)
        return set()


def _fetch_raw_market(adapter, ticker: str) -> dict | None:
    """Fetch raw market data from Kalshi (including resolved/closed markets)."""
    try:
        base_url = adapter._base_url
        path = f"/trade-api/v2/markets/{ticker}"
        headers = adapter._sign_request("GET", path)
        resp = adapter._session.get(base_url + path, headers=headers, timeout=10)
        resp.raise_for_status()
        return resp.json().get("market", {})
    except Exception as e:
        logger.warning("Failed to fetch raw market %s: %s", ticker, e)
        return None


def _mark_market_resolved(db_engine, adapter, ticker: str) -> None:
    """Fetch the resolution result from Kalshi and update the DB, then settle all open positions."""
    try:
        from ai_prophet_core.betting.db import get_session as _gs
        from db_models import TradingMarket as _TM, TradingPosition as _TP, BettingOrder as _BO

        mkt = _fetch_raw_market(adapter, ticker)
        if mkt is None:
            return

        result = mkt.get("result", "")  # "yes", "no", or ""
        if not result:
            logger.warning("  Market %s closed but no resolution result yet", ticker)
            return

        last_price = 1.0 if result == "yes" else 0.0

        market_id = f"kalshi:{ticker}"
        with _gs(db_engine) as session:
            # 1. Update market prices to settlement value
            row = session.query(_TM).filter_by(
                instance_name=INSTANCE_NAME, market_id=market_id,
            ).first()
            if row:
                row.last_price = last_price
                row.yes_ask = last_price
                row.yes_bid = last_price
                row.no_ask = 1.0 - last_price
                row.no_bid = 1.0 - last_price
                row.updated_at = datetime.now(UTC)

            # 2. Settle all open positions for this market (for ALL instances, not just current)
            positions = session.query(_TP).filter_by(market_id=market_id).all()
            settled_count = 0
            total_realized_pnl = 0.0

            for pos in positions:
                if pos.quantity <= 0:
                    continue  # Already closed

                # Save original quantity before we zero it
                original_qty = pos.quantity

                # Calculate settlement P&L
                # YES positions settle at $1.00, NO positions settle at $0.00
                settlement_price = last_price if pos.contract == "yes" else (1.0 - last_price)
                pnl_per_share = settlement_price - pos.avg_price
                settlement_pnl = pnl_per_share * original_qty

                # Update position: close quantity and realize P&L
                pos.realized_pnl = (pos.realized_pnl or 0.0) + settlement_pnl
                pos.unrealized_pnl = 0.0
                pos.quantity = 0.0
                pos.realized_trades += 1
                pos.updated_at = datetime.now(UTC)

                settled_count += 1
                total_realized_pnl += settlement_pnl

                # 3. Log a settlement order in betting_orders for audit trail
                settlement_order = _BO(
                    instance_name=pos.instance_name,
                    market_id=market_id,
                    ticker=ticker,
                    side="sell",  # Settlement is like selling at final price
                    contract=pos.contract,
                    price=settlement_price,
                    quantity=original_qty,
                    status="SETTLED",
                    filled_quantity=original_qty,
                    fill_price=settlement_price,
                    created_at=datetime.now(UTC),
                )
                session.add(settlement_order)

            session.commit()

            if settled_count > 0:
                logger.info(
                    "  Settled %s → %s (price=%.2f): %d positions closed, total P&L: $%.2f",
                    ticker, result, last_price, settled_count, total_realized_pnl
                )
            else:
                logger.info("  Marked %s as resolved → %s (last_price=%.1f), no open positions to settle",
                           ticker, result or "unknown", last_price)
    except Exception as e:
        logger.warning("  Failed to mark %s resolved: %s", ticker, e)


def fetch_market_by_ticker(adapter, ticker: str) -> dict | None:
    """Fetch a single market by ticker, then its parent event for clean title/category."""
    base_url = adapter._base_url

    # 1. Fetch market to get pricing + event_ticker
    path = f"/trade-api/v2/markets/{ticker}"
    headers = adapter._sign_request("GET", path)

    try:
        response = adapter._session.get(
            base_url + path,
            headers=headers,
            timeout=adapter._timeout,
        )
        response.raise_for_status()
        mkt = response.json().get("market", {})

        status = mkt.get("status", "")
        if status not in ("open", "active"):
            return None

        yes_bid = mkt.get("yes_bid_dollars")
        yes_ask = mkt.get("yes_ask_dollars")
        no_bid = mkt.get("no_bid_dollars")
        no_ask = mkt.get("no_ask_dollars")
        last_price = mkt.get("last_price_dollars")
        if yes_ask is None and last_price is None:
            return None

        # 2. Fetch parent event for clean title + category
        event_ticker = mkt.get("event_ticker", "")
        event_title = ""
        category = ""
        if event_ticker:
            try:
                ev_path = f"/trade-api/v2/events/{event_ticker}"
                ev_headers = adapter._sign_request("GET", ev_path)
                ev_resp = adapter._session.get(
                    base_url + ev_path,
                    headers=ev_headers,
                    timeout=adapter._timeout,
                )
                ev_resp.raise_for_status()
                event = ev_resp.json().get("event", {})
                event_title = event.get("title", "")
                category = event.get("category", "")
            except Exception as e:
                logger.debug("Failed to fetch event %s: %s", event_ticker, e)

        # Build clean title same way as fetch_kalshi_markets
        if not event_title:
            event_title = mkt.get("title", ticker)
        yes_sub = mkt.get("yes_sub_title", "")
        title = f"{event_title}: {yes_sub}" if yes_sub else event_title

        return {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": title,
            "subtitle": mkt.get("rules_primary", ""),
            "category": category,
            "yes_bid": yes_bid,
            "yes_ask": yes_ask,
            "no_bid": no_bid,
            "no_ask": no_ask,
            "last_price": last_price,
            "close_time": mkt.get("close_time"),
            "open_time": mkt.get("open_time"),
            "volume_24h": mkt.get("volume_24h_fp", 0),
        }
    except Exception as e:
        logger.warning("Failed to fetch market %s: %s", ticker, e)
        return None


# ── Kalshi market fetcher ─────────────────────────────────────────

def fetch_kalshi_markets(adapter, max_markets: int = 10, max_pages: int = 10) -> list[dict]:
    """Fetch active binary markets from Kalshi via the events endpoint.

    Uses /trade-api/v2/events with nested markets.  Paginates through all
    pages, collects candidates closing within 15 days, then returns the top
    ``max_markets`` ranked by volume (with a soft bonus for prices near 50%).
    """
    base_url = adapter._base_url
    path = "/trade-api/v2/events"
    cutoff = datetime.now(UTC) + timedelta(days=15)

    candidates: list[dict] = []
    cursor = ""
    total_events = 0

    for page in range(max_pages):
        headers = adapter._sign_request("GET", path)
        params = {
            "limit": 200,
            "status": "open",
            "with_nested_markets": "true",
        }
        if cursor:
            params["cursor"] = cursor

        try:
            response = adapter._session.get(
                base_url + path,
                headers=headers,
                params=params,
                timeout=adapter._timeout,
            )
            response.raise_for_status()
            data = response.json()
            events = data.get("events", [])
            cursor = data.get("cursor", "")
            total_events += len(events)
        except Exception as e:
            logger.error("Failed to fetch Kalshi events (page %d): %s", page + 1, e)
            break

        if not events:
            break

        for event in events:
            event_title = event.get("title", "Unknown")
            category = event.get("category", "")

            for mkt in event.get("markets", []):
                status = mkt.get("status", "")
                if status not in ("open", "active"):
                    continue

                # Only trade markets closing within 15 days
                close_time_str = mkt.get("close_time")
                if close_time_str:
                    try:
                        close_dt = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                        if close_dt > cutoff:
                            continue
                    except (ValueError, AttributeError):
                        pass

                ticker = mkt.get("ticker", "")
                yes_bid = mkt.get("yes_bid_dollars")
                yes_ask = mkt.get("yes_ask_dollars")
                no_bid = mkt.get("no_bid_dollars")
                no_ask = mkt.get("no_ask_dollars")
                last_price = mkt.get("last_price_dollars")

                if yes_ask is None and last_price is None:
                    continue

                price = float(yes_ask) if yes_ask is not None else float(last_price)

                yes_sub = mkt.get("yes_sub_title", "")
                market_title = f"{event_title}: {yes_sub}" if yes_sub else event_title

                volume = float(mkt.get("volume_24h_fp", 0) or 0)

                # Apply spread filter early — skip illiquid markets
                _ya = float(yes_ask) if yes_ask is not None else price
                _na = float(no_ask) if no_ask is not None else (1.0 - price)
                if _ya + _na > 1.03:
                    continue

                # Urgency bonus: markets closing sooner rank higher
                # 1.0 if closing today, 0.0 if closing in 15 days
                urgency_bonus = 0.0
                if close_time_str:
                    try:
                        close_dt = datetime.fromisoformat(close_time_str.replace("Z", "+00:00"))
                        days_left = (close_dt - datetime.now(UTC)).total_seconds() / 86400
                        urgency_bonus = max(0.0, 1.0 - (days_left / 15)) * volume * 0.5
                    except (ValueError, AttributeError):
                        pass

                # Rank by volume with a soft bonus for prices near 50% and urgency
                proximity_bonus = 1.0 - 2.0 * abs(price - 0.5)
                candidates.append({
                    "ticker": ticker,
                    "event_ticker": mkt.get("event_ticker", ""),
                    "title": market_title,
                    "subtitle": mkt.get("rules_primary", ""),
                    "category": category,
                    "yes_bid": yes_bid,
                    "yes_ask": yes_ask,
                    "no_bid": no_bid,
                    "no_ask": no_ask,
                    "last_price": last_price,
                    "close_time": close_time_str,
                    "open_time": mkt.get("open_time"),
                    "volume_24h": volume,
                    "_score": volume + proximity_bonus + urgency_bonus,
                })

        if not cursor:
            break

        logger.debug("Page %d: %d candidates so far, fetching more...", page + 1, len(candidates))

    # Rank: prefer higher volume, soft bonus for prices near 50%
    candidates.sort(key=lambda m: m["_score"], reverse=True)
    markets = candidates[:max_markets]

    # Clean up internal scoring field
    for m in markets:
        m.pop("_score", None)

    logger.info(
        "Selected %d markets (from %d candidates, %d events, %d pages)",
        len(markets), len(candidates), total_events, page + 1,
    )
    return markets


# ── LLM prediction ───────────────────────────────────────────────

def create_llm_predictor(model_spec: str):
    """Create a function that uses an LLM to predict market probabilities.

    Args:
        model_spec: Format: "provider:model_name" or "provider:model_name:market"
            The optional ":market" suffix includes market prices in the prompt.
            e.g. "gemini:gemini-3.1-pro-preview:market" → with market data
                 "gemini:gemini-3.1-pro-preview" → without market data

    Returns:
        A callable(market_info) -> dict with keys: p_yes, confidence, reasoning
    """
    parts = model_spec.split(":")
    if len(parts) >= 3:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = parts[2].lower() in ("market", "mkt", "prices")
    elif len(parts) == 2:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = False
    else:
        raise ValueError(f"model_spec must include a provider prefix, e.g. 'gemini:{parts[0]}'")

    if provider in ("gemini", "google"):
        return _gemini_predictor(model_name, include_market)
    elif provider in ("openai", "anthropic", "claude"):
        raise NotImplementedError(
            f"Provider '{provider}' is not currently active. "
            "Uncomment _openai_predictor/_anthropic_predictor in worker/main.py to re-enable."
        )
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")


def _build_prompts(market_info: dict, include_market_prices: bool = False) -> tuple[str, str]:
    """Build system and user prompts matching the ProphetArena AgentPrompts format.

    Args:
        market_info: Market data dict with title, yes_ask, no_ask, subtitle (rules), etc.
        include_market_prices: If True, include YES/NO ask prices in the prompt.
            When False, the model is also instructed not to search for prediction
            market data so that its prediction is independent of market consensus.

    Returns:
        (system_prompt, user_prompt)
    """
    title = market_info.get("title", "")
    rules = market_info.get("subtitle", "")
    rules_block = f"\n  Resolution rules: {rules}\n" if rules else ""

    avoid_market_block = ""
    if not include_market_prices:
        avoid_market_block = """
CRITICAL RESTRICTION:
- Do NOT search for or use any prediction market data, betting odds, or market prices
- Do NOT reference Polymarket, Kalshi, PredictIt, Metaculus, or any other prediction/betting platforms
- Base your predictions ONLY on factual news, expert analysis, and primary sources
- Your prediction should be independent of any existing market consensus
"""

    system = f"""You are an AI assistant specialized in analyzing and predicting real-world events.
You are predicting whether the following binary outcome resolves YES: "{title}"
{rules_block}
This is a binary prediction market contract. Your task is to estimate the probability (0 to 1) that this specific outcome resolves YES.
Use web search to find relevant recent news, expert analysis, and primary sources to inform your prediction.
{avoid_market_block}
IMPORTANT CONSTRAINTS:
1. Output a single probability between 0 and 1 for this outcome resolving YES
2. Do not speculate about other related outcomes or contracts

Your response MUST be in JSON format with the following structure:
```json
{{
    "rationale": "<short_concise_3_sentence_rationale>",
    "probabilities": {{
        "{title}": <probability_value_from_0_to_1>
    }}
}}
```

In the rationale, provide a short, concise, 3 sentence rationale that explains:
- How you weighed different pieces of information
- Your reasoning for the probability you assigned
- Any key factors or uncertainties you considered"""

    if include_market_prices:
        yes_ask = market_info.get("yes_ask", 0.5)
        no_ask = market_info.get("no_ask", 0.5)
        market_stats = json.dumps({"YES": yes_ask, "NO": no_ask}, indent=2)
        user = f"""CURRENT ONLINE TRADING DATA:
You also have access to the predicted outcome probability from a prediction market:
{market_stats}

Note: Market data can provide insights into the current consensus influenced by traders of various beliefs and private information. However, you should not rely on market data alone.

Please analyze the event and provide your prediction following the specified format."""
    else:
        user = "Please analyze the event described in the system prompt and provide your prediction following the specified format."

    return system, user


def _parse_prediction(content: str) -> dict:
    """Extract prediction JSON from LLM response."""
    # Strip markdown code fences if present
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        result = json.loads(text[start:end])
    else:
        result = json.loads(text)

    # Extract p_yes — ProphetArena format uses "probabilities" dict
    p_yes = 0.5
    probs = result.get("probabilities", {})
    if probs:
        # Get the first (and only) value from the probabilities dict
        p_yes = float(next(iter(probs.values())))
    elif "p_yes" in result:
        p_yes = float(result["p_yes"])

    return {
        "p_yes": p_yes,
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("rationale", result.get("reasoning", "")),
        "analysis": result.get("analysis", {}),
    }


# def _openai_predictor(model_name: str, include_market: bool = False):
#     """Return a predictor function using OpenAI."""
#     import openai
#     client = openai.OpenAI(api_key=_instance_setting("OPENAI_API_KEY"))

#     def predict(market_info: dict) -> dict:
#         system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
#         try:
#             response = client.chat.completions.create(
#                 model=model_name,
#                 messages=[
#                     {"role": "system", "content": system_prompt},
#                     {"role": "user", "content": user_prompt},
#                 ],
#                 temperature=0.2,
#                 max_tokens=800,
#                 response_format={"type": "json_object"},
#             )
#             return _parse_prediction(response.choices[0].message.content)
#         except Exception as e:
#             logger.error("OpenAI prediction failed: %s", e)
#             return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}"}

#     return predict


# def _anthropic_predictor(model_name: str, include_market: bool = False):
#     """Return a predictor function using Anthropic."""
#     import anthropic
#     client = anthropic.Anthropic(api_key=_instance_setting("ANTHROPIC_API_KEY"))

#     def predict(market_info: dict) -> dict:
#         system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
#         try:
#             response = client.messages.create(
#                 model=model_name,
#                 max_tokens=800,
#                 system=system_prompt,
#                 messages=[{"role": "user", "content": user_prompt}],
#             )
#             return _parse_prediction(response.content[0].text)
#         except Exception as e:
#             logger.error("Anthropic prediction failed: %s", e)
#             return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}"}

#     return predict


def _gemini_predictor(model_name: str, include_market: bool = False):
    """Return a predictor function using Gemini REST API.

    Usage: gemini:gemini-2.0-flash, gemini:gemini-3-flash-preview, etc.
    """
    import httpx

    api_key = _instance_specific_setting("GOOGLE_API_KEY") or _instance_specific_setting("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            f"GOOGLE_API_KEY_{env_suffix(INSTANCE_NAME)} or "
            f"GEMINI_API_KEY_{env_suffix(INSTANCE_NAME)} env var required for Gemini"
        )
    base_url = "https://generativelanguage.googleapis.com/v1beta"
    http_client = httpx.Client(timeout=PREDICTOR_TIMEOUT_SEC)

    def predict(market_info: dict) -> dict:
        system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)

        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "generationConfig": {"responseMimeType": "application/json"},
            "tools": [{"googleSearch": {}}],
        }

        # Gemini 3+ models get thinking config
        if "gemini-3" in model_name:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "high"}

        url = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

        try:
            t0 = time.time()
            response = http_client.post(url, json=body)
            elapsed = time.time() - t0
            response.raise_for_status()
            data = response.json()

            candidates = data.get("candidates", [])
            if not candidates:
                raise ValueError(f"Gemini returned no candidates: {data}")

            candidate = candidates[0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)

            # Extract grounding sources from search metadata
            sources: list[dict] = []
            try:
                grounding_meta = candidate.get("groundingMetadata", {})
                for chunk in grounding_meta.get("groundingChunks", []):
                    web = chunk.get("web", {})
                    uri = web.get("uri", "")
                    if uri:
                        sources.append({"url": uri, "title": web.get("title", uri)})
            except Exception:
                pass

            logger.info("Gemini API call took %.1fs (%d sources)", elapsed, len(sources))
            result = _parse_prediction(text)
            result["sources"] = sources
            return result
        except Exception as e:
            logger.error("Gemini prediction failed (%.1fs): %s", time.time() - t0, e)
            return {"p_yes": 0.5, "confidence": 0.0, "reasoning": f"Error: {e}", "sources": []}

    return predict


# ── Remote prediction (Cloud Run service) ─────────────────────────


def _remote_predict(
    model_spec: str,
    market_info: dict,
    *,
    service_url: str,
    api_key: str,
) -> dict:
    """Call the remote predictor service for a single (model, market) pair."""
    import requests

    resp = requests.post(
        f"{service_url}/predict",
        json={
            "model_spec": model_spec,
            "market_info": market_info,
            "instance_name": INSTANCE_NAME,
        },
        headers={"X-API-Key": api_key} if api_key else {},
        timeout=REMOTE_PREDICT_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def _remote_predict_with_retry(
    model_spec: str,
    market_info: dict,
    *,
    service_url: str,
    api_key: str,
    max_retries: int = 2,
) -> dict:
    """Call remote predictor with retries on failure."""
    for attempt in range(max_retries + 1):
        try:
            return _remote_predict(
                model_spec,
                market_info,
                service_url=service_url,
                api_key=api_key,
            )
        except Exception as e:
            if attempt < max_retries:
                logger.warning(
                    "  [%s] remote attempt %d failed, retrying in 5s: %s",
                    model_spec, attempt + 1, e,
                )
                time.sleep(5)
            else:
                raise


# ── Betting engine factory ────────────────────────────────────────

def build_betting_engine(strategy_name: str = "default", dry_run_override: bool | None = None):
    """Create BettingEngine reusing the existing core module."""
    from ai_prophet_core.betting import BettingEngine, LiveBettingSettings
    from ai_prophet_core.betting.db import create_db_engine

    settings = LiveBettingSettings.from_env(_build_instance_env())

    if not settings.enabled:
        logger.warning("Betting engine DISABLED (LIVE_BETTING_ENABLED != true)")
        return None, None

    dry_run = dry_run_override if dry_run_override is not None else settings.dry_run

    db_engine = create_db_engine()

    if strategy_name == "rebalancing":
        from ai_prophet_core.betting import RebalancingStrategy
        strategy = RebalancingStrategy()
    else:
        from ai_prophet_core.betting import DefaultBettingStrategy
        strategy = DefaultBettingStrategy()

    starting_cash = float(_instance_setting("WORKER_STARTING_CASH", "10000"))

    engine = BettingEngine(
        strategy=strategy,
        db_engine=db_engine,
        dry_run=dry_run,
        kalshi_config=settings.kalshi,
        enabled=settings.enabled,
        instance_name=INSTANCE_NAME,
        starting_cash=starting_cash,
    )
    logger.info(
        "BettingEngine ready: instance=%s, strategy=%s, dry_run=%s",
        INSTANCE_NAME, engine.strategy.name, dry_run,
    )
    return engine, db_engine


# ── Main trading cycle ────────────────────────────────────────────

def run_cycle(args) -> None:
    """Run one trading cycle: fetch markets → LLM predict → BettingEngine.

    When MARKET_FETCHER=true (default), this instance discovers new markets
    from Kalshi.  When MARKET_FETCHER=false, it mirrors the market list from
    the peer instance (WORKER_PEER_INSTANCES) instead of querying Kalshi for
    new markets — ensuring both workers always predict on the same events.
    """
    strategy_name = _instance_setting("WORKER_STRATEGY", "rebalancing")
    dry_run_override = True if args.dry_run else None
    max_markets = _instance_int_setting("WORKER_MAX_MARKETS", 50)
    max_active = _instance_int_setting("WORKER_MAX_ACTIVE_MARKETS", 50)
    models_str = _instance_setting("WORKER_MODELS", "gemini:gemini-3.1-pro-preview")
    model_specs = [m.strip() for m in models_str.split(",") if m.strip()]
    predictor_service_url = _instance_setting("PREDICTOR_SERVICE_URL", "").rstrip("/")
    predictor_api_key = _instance_setting("PREDICTOR_API_KEY", "")
    is_market_fetcher = _instance_setting("MARKET_FETCHER", "true").lower() in ("true", "1", "yes")
    peer_instances = [p.strip() for p in _instance_setting("WORKER_PEER_INSTANCES", "").split(",") if p.strip()]

    # Build engine
    betting_engine, db_engine = build_betting_engine(
        strategy_name=strategy_name,
        dry_run_override=dry_run_override,
    )

    if db_engine is not None:
        log_heartbeat(db_engine, message="cycle_start", instance_name=INSTANCE_NAME)
        from ai_prophet_core.betting.db_schema import Base as CoreBase
        CoreBase.metadata.create_all(db_engine, checkfirst=True)

    if betting_engine is None:
        logger.error("Betting engine not available, skipping cycle")
        return

    # Get the Kalshi adapter from the engine to reuse auth
    adapter = betting_engine._get_adapter()

    logger.info(
        "Starting cycle: instance=%s, models=%s, strategy=%s, max_markets=%d, max_active=%d",
        INSTANCE_NAME, model_specs, strategy_name, max_markets, max_active,
    )
    if db_engine is not None:
        log_system_event(
            db_engine,
            "INFO",
            f"Cycle start: fetcher={is_market_fetcher}, peers={peer_instances or []}, "
            f"models={model_specs}, strategy={strategy_name}, max_active={max_active}",
            instance_name=INSTANCE_NAME,
        )

    # Order management: Sync pending orders FIRST, then cancel stale orders
    if db_engine is not None and not dry_run_override:
        try:
            # CRITICAL: Sync all pending orders with Kalshi to get latest fills/status
            updated = sync_pending_orders(db_engine, adapter, INSTANCE_NAME)
            if updated > 0:
                logger.info("[CYCLE] Synced %d pending orders from Kalshi", updated)

        except Exception as e:
            logger.error("[CYCLE] Order sync failed: %s", e)

        # Optional: Cancel stale orders and reconcile positions (not yet implemented)
        # try:
        #     from order_management import cancel_stale_orders, reconcile_positions_with_kalshi
        #     cancelled = cancel_stale_orders(db_engine, adapter, INSTANCE_NAME, stale_threshold_minutes=60)
        #     if cancelled > 0:
        #         logger.info("[CYCLE] Cancelled %d stale orders", cancelled)
        #     drifts = reconcile_positions_with_kalshi(db_engine, adapter, INSTANCE_NAME, tolerance_contracts=5)
        #     if drifts:
        #         logger.error("[CYCLE] Position drifts detected: %s", drifts)
        # except Exception as e:
        #     logger.error("[CYCLE] Order management failed: %s", e)

    # 1. Gather sticky markets (already tracked in DB)
    tracked_tickers = (
        get_tracked_tickers(db_engine, INSTANCE_NAME)
        | get_traded_tickers(db_engine, INSTANCE_NAME)
    )
    sticky_markets: list[dict] = []

    if tracked_tickers:
        logger.info("Re-fetching %d sticky markets: %s", len(tracked_tickers), tracked_tickers)
        for ticker in tracked_tickers:
            mkt = fetch_market_by_ticker(adapter, ticker)
            if mkt:
                sticky_markets.append(mkt)
            else:
                logger.info("  Sticky market %s resolved/closed, marking in DB", ticker)
                if db_engine is not None:
                    _mark_market_resolved(db_engine, adapter, ticker)

    # 2. Discover NEW markets — either from Kalshi (fetcher) or from peer instance (mirror)
    new_slots = max(0, max_active - len(sticky_markets))
    if new_slots > 0:
        if is_market_fetcher:
            # This instance owns market discovery — pull ranked candidates from Kalshi
            all_new = fetch_kalshi_markets(adapter, max_markets=max_markets + len(tracked_tickers))
            new_markets = [m for m in all_new if m["ticker"] not in tracked_tickers]
            new_markets = new_markets[:new_slots]
            logger.info(
                "Fetched %d new markets from Kalshi (%d candidates, %d excluded as already tracked)",
                len(new_markets), len(all_new), len(all_new) - len(new_markets),
            )
        else:
            # Mirror the peer instance's market list — fetch live prices for tickers
            # the fetcher has already discovered, skipping ones we already track.
            peer = peer_instances[0] if peer_instances else None
            if not peer:
                logger.warning("MARKET_FETCHER=false but no WORKER_PEER_INSTANCES set — no new markets")
                new_markets = []
            else:
                peer_tickers = get_peer_tickers(db_engine, peer) - tracked_tickers
                logger.info("Mirroring %d new tickers from peer '%s'", len(peer_tickers), peer)
                new_markets = []
                for ticker in list(peer_tickers)[:new_slots]:
                    mkt = fetch_market_by_ticker(adapter, ticker)
                    if mkt:
                        new_markets.append(mkt)
                logger.info("Fetched live prices for %d mirrored markets", len(new_markets))
    else:
        new_markets = []
        logger.info("At max active markets (%d), not fetching new ones", max_active)

    # 3. Combine: sticky first, then new
    raw_markets = sticky_markets + new_markets
    logger.info("Total markets this cycle: %d sticky + %d new = %d",
                len(sticky_markets), len(new_markets), len(raw_markets))
    if db_engine is not None:
        log_system_event(
            db_engine,
            "INFO",
            f"Market discovery: sticky={len(sticky_markets)}, "
            f"new={len(new_markets)}, total={len(raw_markets)}, "
            f"tracked={len(tracked_tickers)}, mode={'fetcher' if is_market_fetcher else 'mirror'}",
            instance_name=INSTANCE_NAME,
        )

    if not raw_markets:
        logger.warning("No markets fetched, skipping cycle")
        if db_engine:
            log_system_event(db_engine, "WARNING", "No markets fetched from Kalshi", instance_name=INSTANCE_NAME)
            log_heartbeat(db_engine, message="cycle_end", instance_name=INSTANCE_NAME)
        if betting_engine:
            betting_engine.close()
        return

    # Collect all market prices across models for position updates
    all_market_prices: dict[str, tuple[float, float]] = {}
    # Track (market_id, model, edge) for alert checking
    all_edges: list[tuple[str, str, float]] = []

    # ── Phase A: Pre-filter markets (sequential) ────────────────────
    # Validate prices, apply spread filter, save snapshots, skip unchanged.
    # Build a list of markets that need LLM analysis.
    tick_ts = datetime.now(UTC)
    total_results = []

    from ai_prophet_core.betting.config import MAX_SPREAD

    markets_to_analyze: list[dict] = []  # enriched market dicts

    for market in raw_markets:
        if _shutdown_requested:
            logger.info("Shutdown requested, stopping analysis")
            break

        ticker = market.get("ticker", "")
        title = market.get("title", "Unknown")
        subtitle = market.get("subtitle", "")
        category = market.get("category", "")

        yes_ask = market.get("yes_ask")
        no_ask = market.get("no_ask")

        if yes_ask is None or no_ask is None:
            last_price = market.get("last_price")
            if last_price is not None:
                yes_ask = float(last_price)
                no_ask = 1.0 - yes_ask
            else:
                logger.warning("Skipping %s: no pricing data", ticker)
                continue

        yes_ask = float(yes_ask)
        no_ask = float(no_ask)

        if yes_ask + no_ask > MAX_SPREAD:
            logger.debug("Skipping %s: spread %.3f > %.3f", ticker, yes_ask + no_ask, MAX_SPREAD)
            continue

        # Skip markets where the outcome is nearly certain (≥97% or ≤3%) — no profit opportunity.
        if yes_ask >= 0.97 or yes_ask <= 0.03:
            logger.debug("Skipping %s: near-certain price (yes_ask=%.3f)", ticker, yes_ask)
            continue

        # Never bet on MENTIONS markets — they track social media activity,
        # not real-world events, and are not suitable for model prediction.
        if market.get("category", "").upper() == "MENTIONS":
            logger.debug("Skipping %s: MENTIONS category excluded from betting", ticker)
            continue

        market_id = f"kalshi:{ticker}"

        # Save market snapshot for dashboard
        if db_engine:
            expiration = None
            exp_str = market.get("close_time")
            if exp_str:
                try:
                    expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                except (ValueError, AttributeError):
                    pass
            save_market_snapshot(
                db_engine, market_id, title, category,
                yes_bid=market.get("yes_bid"), yes_ask=yes_ask,
                no_bid=market.get("no_bid"), no_ask=no_ask,
                expiration=expiration, ticker=ticker,
                event_ticker=market.get("event_ticker", ""),
                volume_24h=float(market.get("volume_24h", 0) or 0),
                instance_name=INSTANCE_NAME,
            )

            # Skip if position held and prices unchanged
            try:
                from db_models import TradingPosition as TP, MarketPriceSnapshot as MPS
                from ai_prophet_core.betting.db import get_session as _gs
                with _gs(db_engine) as _sess:
                    _pos = _sess.query(TP).filter_by(
                        instance_name=INSTANCE_NAME,
                        market_id=market_id,
                    ).first()
                    if _pos and _pos.quantity > 0:
                        _last = (
                            _sess.query(MPS)
                            .filter(
                                MPS.instance_name == INSTANCE_NAME,
                                MPS.market_id == market_id,
                            )
                            .order_by(MPS.timestamp.desc())
                            .first()
                        )
                        if (_last
                            and abs(_last.yes_ask - yes_ask) < 1e-6
                            and abs(_last.no_ask - no_ask) < 1e-6):
                            logger.info(
                                "  Skipping %s — market prices unchanged "
                                "(yes=%.2f, no=%.2f)",
                                ticker, yes_ask, no_ask,
                            )
                            all_market_prices[market_id] = (yes_ask, no_ask)
                            continue
            except Exception:
                pass

            save_price_snapshot(
                db_engine, market_id, ticker,
                yes_ask=yes_ask, no_ask=no_ask,
                volume_24h=float(market.get("volume_24h", 0) or 0),
                instance_name=INSTANCE_NAME,
            )

        # Market passed all filters — queue for LLM analysis
        markets_to_analyze.append({
            **market,
            "yes_ask": yes_ask,
            "no_ask": no_ask,
            "market_id": market_id,
            "market_info": {
                "title": title,
                "subtitle": subtitle,
                "category": category,
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "open_time": market.get("open_time"),
            },
        })

    logger.info("Phase A complete: %d markets to analyze (from %d raw)",
                len(markets_to_analyze), len(raw_markets))
    if db_engine is not None:
        log_system_event(
            db_engine,
            "INFO",
            f"Phase A complete: {len(markets_to_analyze)} analyzable markets from {len(raw_markets)} raw",
            instance_name=INSTANCE_NAME,
        )

    if not markets_to_analyze:
        logger.info("No markets to analyze, skipping prediction phase")
        if db_engine is not None:
            log_system_event(
                db_engine,
                "WARNING",
                f"No markets to analyze after filtering ({len(raw_markets)} raw markets)",
                instance_name=INSTANCE_NAME,
            )
            log_heartbeat(db_engine, message="cycle_end", instance_name=INSTANCE_NAME)
        if betting_engine:
            betting_engine.close()
        return

    # ── Phase B: Collect predictions (parallel or sequential) ─────
    # predictions[(ticker, model_spec)] = {p_yes, confidence, reasoning, analysis}
    predictions: dict[tuple[str, str], dict] = {}

    if predictor_service_url:
        # ── Remote parallel prediction via Cloud Run service ──────
        logger.info("Using remote predictor: %s (parallel fanout)", predictor_service_url)

        prediction_tasks = [
            (mkt, ms)
            for mkt in markets_to_analyze
            for ms in model_specs
        ]

        logger.info("Fanning out %d prediction tasks (%d markets × %d models) with max_workers=20",
                     len(prediction_tasks), len(markets_to_analyze), len(model_specs))

        t_fan = time.time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            future_to_key = {}
            for mkt, ms in prediction_tasks:
                future = executor.submit(
                    _remote_predict_with_retry,
                    ms,
                    mkt["market_info"],
                    service_url=predictor_service_url,
                    api_key=predictor_api_key,
                )
                future_to_key[future] = (mkt["ticker"], ms)

            for future in concurrent.futures.as_completed(future_to_key):
                key = future_to_key[future]
                ticker, ms = key
                try:
                    result = future.result()
                    predictions[key] = result
                    logger.info(
                        "  [%s] %s → p_yes=%.3f",
                        ms.split(":")[-1], ticker, result["p_yes"],
                    )
                except Exception as e:
                    logger.error("  [%s] %s prediction failed: %s", ms, ticker, e)
                    if db_engine is not None:
                        log_system_event(
                            db_engine,
                            "ERROR",
                            f"Prediction failed for {ticker} [{ms}]: {e}",
                            instance_name=INSTANCE_NAME,
                        )

        logger.info("Phase B complete: %d/%d predictions in %.1fs",
                     len(predictions), len(prediction_tasks), time.time() - t_fan)
        if db_engine is not None:
            log_system_event(
                db_engine,
                "INFO",
                f"Phase B complete: {len(predictions)}/{len(prediction_tasks)} predictions succeeded",
                instance_name=INSTANCE_NAME,
            )
    else:
        # ── Local sequential prediction (development fallback) ────
        logger.info("Using local predictors (sequential, no PREDICTOR_SERVICE_URL set)")

        predictors: dict[str, Any] = {}
        for model_spec in model_specs:
            try:
                predictors[model_spec] = create_llm_predictor(model_spec)
            except Exception as e:
                logger.error("Failed to create predictor for %s: %s", model_spec, e)
                if db_engine:
                    log_system_event(
                        db_engine,
                        "ERROR",
                        f"Predictor init failed for {model_spec}: {e}",
                        instance_name=INSTANCE_NAME,
                    )

        if not predictors:
            logger.error("No predictors available, skipping cycle")
            if betting_engine:
                betting_engine.close()
            return

        for mkt in markets_to_analyze:
            ticker = mkt["ticker"]
            market_info = mkt["market_info"]

            logger.info("Analyzing: %s (yes=%.2f, no=%.2f)",
                        mkt.get("title", "")[:60], mkt["yes_ask"], mkt["no_ask"])

            for mi, (model_spec, predictor) in enumerate(predictors.items()):
                if mi > 0:
                    time.sleep(2)

                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        prediction = predictor(market_info)
                        predictions[(ticker, model_spec)] = prediction
                        logger.info(
                            "  [%s] p_yes=%.3f (confidence=%.2f) | %s",
                            model_spec.split(":")[-1],
                            prediction["p_yes"],
                            prediction.get("confidence", 0.5),
                            prediction.get("reasoning", "")[:60],
                        )
                        break
                    except Exception as e:
                        if attempt < max_retries:
                            logger.warning("  [%s] attempt %d failed, retrying in 5s: %s",
                                           model_spec, attempt + 1, e)
                            time.sleep(5)
                        else:
                            logger.error("  [%s] prediction failed after %d attempts: %s",
                                         model_spec, max_retries + 1, e)
                            if db_engine is not None:
                                log_system_event(
                                    db_engine,
                                    "ERROR",
                                    f"Prediction failed for {ticker} [{model_spec}] after {max_retries + 1} attempts: {e}",
                                    instance_name=INSTANCE_NAME,
                                )

    # ── Phase C: Aggregate & bet (sequential) ─────────────────────
    # BettingEngine is NOT thread-safe — process all markets sequentially.
    dry_run = _instance_bool_setting("LIVE_BETTING_DRY_RUN", True)
    order_ledger_state = _load_order_ledger_state(db_engine, adapter, INSTANCE_NAME, dry_run=dry_run)
    for mkt in markets_to_analyze:
        if _shutdown_requested:
            logger.info("Shutdown requested, stopping betting")
            break

        ticker = mkt["ticker"]
        market_id = mkt["market_id"]
        yes_ask = mkt["yes_ask"]
        no_ask = mkt["no_ask"]
        title = mkt.get("title", "Unknown")

        # Gather this market's predictions across all models
        model_predictions: dict[str, dict] = {}
        for ms in model_specs:
            pred = predictions.get((ticker, ms))
            if pred:
                p_yes = pred["p_yes"]
                confidence = pred.get("confidence", 0.5)
                reasoning = pred.get("reasoning", "")

                model_predictions[ms] = {
                    "p_yes": p_yes,
                    "confidence": confidence,
                    "reasoning": reasoning,
                    "analysis": pred.get("analysis", {}),
                    "sources": pred.get("sources", []),
                }

                # Track edge for alert checking
                all_edges.append((market_id, ms, abs(p_yes - yes_ask)))

        if not model_predictions:
            logger.warning("  No model predictions for %s, skipping", ticker)
            if db_engine is not None:
                log_system_event(
                    db_engine,
                    "WARNING",
                    f"No model predictions available for {ticker}; skipping market",
                    instance_name=INSTANCE_NAME,
                )
            all_market_prices[market_id] = (yes_ask, no_ask)
            continue

        # Use the single model's prediction directly (first available)
        model_spec = next(iter(model_predictions))
        mp = model_predictions[model_spec]
        p_yes = mp["p_yes"]
        edge = p_yes - yes_ask

        logger.info(
            "  [%s] edge=%.3f (p_yes=%.3f vs yes_ask=%.3f)",
            model_spec.split(":")[-1], edge, p_yes, yes_ask,
        )

        if db_engine:
            save_price_snapshot(
                db_engine, market_id, ticker,
                yes_ask=yes_ask, no_ask=no_ask,
                volume_24h=float(mkt.get("volume_24h", 0) or 0),
                model_p_yes=round(p_yes, 6),
                model_name=model_spec,
                instance_name=INSTANCE_NAME,
            )

        # Build per-market portfolio snapshot directly from the order ledger so
        # rebalancing uses authoritative holdings instead of potentially stale
        # trading_positions snapshots.
        portfolio = None
        if order_ledger_state is not None:
            try:
                from ai_prophet_core.betting.strategy import PortfolioSnapshot

                ticker_key = market_id[len("kalshi:"):] if market_id.startswith("kalshi:") else market_id
                market_position = order_ledger_state["positions"].get(ticker_key)
                market_side = None
                market_qty = 0.0
                if market_position is not None:
                    market_side, market_qty, _avg_price = market_position.current_position()

                portfolio = PortfolioSnapshot(
                    cash=order_ledger_state["cash"],
                    total_pnl=order_ledger_state["total_pnl"],
                    position_count=order_ledger_state["position_count"],
                    market_position_shares=Decimal(str(market_qty)),
                    market_position_side=market_side,
                )
            except Exception as e:
                logger.debug("Could not materialize ledger-based portfolio snapshot: %s", e)

        # If the market drifted to near-certain territory since it was pulled, skip betting.
        if yes_ask >= 0.97 or yes_ask <= 0.03:
            logger.info(
                "  HOLD_NOPROFIT %s — near-certain price (yes_ask=%.3f), skipping",
                ticker, yes_ask,
            )
            if db_engine and betting_engine is not None:
                for ms, pred in model_predictions.items():
                    save_model_run(
                        db_engine, ms, market_id, "HOLD_NOPROFIT", pred.get("confidence"),
                        metadata={"p_yes": pred["p_yes"], "reasoning": pred.get("reasoning", ""),
                                  "analysis": pred.get("analysis", {}),
                                  "sources": pred.get("sources", []),
                                  "yes_ask": yes_ask, "no_ask": no_ask},
                        instance_name=INSTANCE_NAME,
                    )
            all_market_prices[market_id] = (yes_ask, no_ask)
            continue

        if db_engine and betting_engine is not None:
            for ms, pred in model_predictions.items():
                try:
                    betting_engine.strategy._portfolio = portfolio
                    strategy_signal = betting_engine.strategy.evaluate(
                        market_id=market_id,
                        p_yes=pred["p_yes"],
                        yes_ask=yes_ask,
                        no_ask=no_ask,
                    )
                    decision = (
                        f"BUY_{strategy_signal.side.upper()}"
                        if strategy_signal is not None
                        else "HOLD"
                    )
                except Exception:
                    decision = "HOLD"

                save_model_run(
                    db_engine, ms, market_id, decision, pred.get("confidence"),
                    metadata={"p_yes": pred["p_yes"], "reasoning": pred.get("reasoning", ""),
                              "analysis": pred.get("analysis", {}),
                              "sources": pred.get("sources", []),
                              "yes_ask": yes_ask, "no_ask": no_ask},
                    instance_name=INSTANCE_NAME,
                )

        # Feed prediction directly into BettingEngine (strategy decides edge threshold)
        result = betting_engine.on_forecast(
            tick_ts=tick_ts,
            market_id=market_id,
            p_yes=p_yes,
            yes_ask=yes_ask,
            no_ask=no_ask,
            source=model_spec,
            portfolio=portfolio,
        )
        if result is not None:
            total_results.append(result)

        all_market_prices[market_id] = (yes_ask, no_ask)

    # Summarize cycle results
    if total_results:
        placed = sum(1 for r in total_results if r.order_placed)
        skipped = sum(1 for r in total_results if r.signal is None)
        logger.info(
            "Cycle results: %d orders placed, %d skipped, %d total across %d markets",
            placed, skipped, len(total_results), len(raw_markets),
        )
        if db_engine:
            log_system_event(
                db_engine, "INFO",
                f"Cycle complete: models={model_specs}, placed={placed}, "
                f"skipped={skipped}, total={len(total_results)}",
                instance_name=INSTANCE_NAME,
            )
    elif db_engine is not None:
        log_system_event(
            db_engine,
            "WARNING",
            f"Cycle produced no betting results across {len(markets_to_analyze)} analyzed markets",
            instance_name=INSTANCE_NAME,
        )

    # 3b. Check alert conditions and log to SystemLog
    if db_engine:
        try:
            from ai_prophet_core.betting.db import get_session
            from db_models import TradingPosition

            # Alert if any model showed a large edge (|p_yes - yes_ask| > 0.20)
            for mid, mname, edge in all_edges:
                if edge > 0.20:
                    log_system_event(
                        db_engine, "ALERT",
                        f"Large edge detected on {mid} (model={mname}): "
                        f"edge={edge:.3f}",
                        instance_name=INSTANCE_NAME,
                    )

            # Alert if total capital deployed is high
            with get_session(db_engine) as session:
                all_positions = (
                    session.query(TradingPosition)
                    .filter(TradingPosition.instance_name == INSTANCE_NAME)
                    .all()
                )
                total_capital = sum(p.quantity * p.avg_price for p in all_positions)
                if total_capital > 50.0:  # threshold: $50 deployed
                    log_system_event(
                        db_engine, "ALERT",
                        f"High capital deployment: ${total_capital:.2f} across "
                        f"{len(all_positions)} positions",
                        instance_name=INSTANCE_NAME,
                    )
        except Exception as e:
            logger.debug("Alert check failed: %s", e)

    # 4. Update positions from order history
    #    Re-fetch current prices for ALL traded tickers so unrealized PnL
    #    reflects actual market movement, not just this cycle's markets.
    if db_engine:
        traded = get_traded_tickers(db_engine, INSTANCE_NAME)
        for ticker in traded:
            market_id = f"kalshi:{ticker}"
            if market_id not in all_market_prices:
                mkt = fetch_market_by_ticker(adapter, ticker)
                if mkt and mkt.get("yes_ask") is not None and mkt.get("no_ask") is not None:
                    all_market_prices[market_id] = (float(mkt["yes_ask"]), float(mkt["no_ask"]))

        # Fall back to cached prices in trading_markets table
        if not all_market_prices:
            try:
                from ai_prophet_core.betting.db import get_session
                from db_models import TradingMarket
                with get_session(db_engine) as session:
                    for tm in session.query(TradingMarket).all():
                        if tm.instance_name != INSTANCE_NAME:
                            continue
                        if tm.yes_ask is not None and tm.no_ask is not None:
                            all_market_prices[tm.market_id] = (tm.yes_ask, tm.no_ask)
            except Exception as e:
                logger.debug("Failed to load cached market prices: %s", e)

        # Always update positions (even without prices — deployed capital still tracked)
        update_positions(db_engine, INSTANCE_NAME)

    # Cleanup
    if betting_engine is not None:
        betting_engine.close()
    if db_engine is not None:
        log_heartbeat(db_engine, message="cycle_end", instance_name=INSTANCE_NAME)

    total_placed = sum(1 for r in total_results if r.order_placed)
    logger.info(
        "Cycle complete: %d total results, %d orders placed across %d models",
        len(total_results), total_placed, len(model_specs),
    )


# ── Health server (Cloud Run requires HTTP) ───────────────────────

def _start_health_server() -> None:
    """Serve a minimal HTTP health endpoint so Cloud Run keeps the container alive."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args):  # suppress access logs
            pass

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    logger.info("Health server listening on port %d", port)


# ── Entry point ───────────────────────────────────────────────────

def _get_max_peer_cycle_end(db_engine, all_instances: list[str]) -> datetime | None:
    """Return the most recent cycle_end timestamp across all specified instances."""
    if db_engine is None or not all_instances:
        return None
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            rows = (
                session.query(SystemLog)
                .filter(
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "worker",
                    SystemLog.message == "cycle_end",
                    SystemLog.instance_name.in_(all_instances),
                )
                .order_by(SystemLog.created_at.desc())
                .limit(len(all_instances) + 5)
                .all()
            )
            # Most recent cycle_end per instance
            seen: set[str] = set()
            latest_per: dict[str, datetime] = {}
            for row in rows:
                if row.instance_name not in seen:
                    seen.add(row.instance_name)
                    latest_per[row.instance_name] = row.created_at
            if not latest_per:
                return None
            return max(latest_per.values())
    except Exception as e:
        logger.warning("Failed to get peer cycle ends: %s", e)
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Kalshi trading worker (standalone)")
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run mode")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)
    _start_health_server()
    _validate_instance_profile_or_raise()

    poll_interval = _instance_int_setting("WORKER_POLL_INTERVAL_SEC", 3600)
    peer_instances_str = _instance_setting("WORKER_PEER_INSTANCES", "")
    peer_instances = [p.strip() for p in peer_instances_str.split(",") if p.strip()] if peer_instances_str else []
    all_sync_instances = list({INSTANCE_NAME} | set(peer_instances))

    logger.info(
        "Worker starting (instance=%s, poll_interval=%ds, dry_run=%s, peers=%s)",
        INSTANCE_NAME,
        poll_interval,
        args.dry_run,
        peer_instances or "none",
    )
    logger.info("Mode: STANDALONE (direct Kalshi API + LLM predictions)")

    # Wait for the next hour boundary before starting (unless --once flag is set)
    if not args.once:
        now = datetime.now(UTC)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seconds_until_next_hour = (next_hour - now).total_seconds()

        logger.info(
            "Waiting until next hour boundary: %s UTC (%.0f seconds)",
            next_hour.strftime("%H:%M"), seconds_until_next_hour
        )

        # Show local time too
        try:
            import zoneinfo
            local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
            next_hour_local = next_hour.astimezone(local_tz)
            logger.info(
                "First cycle will run at: %s UTC / %s PST",
                next_hour.strftime("%H:%M"),
                next_hour_local.strftime("%H:%M")
            )
        except:
            pass

        if seconds_until_next_hour > 0 and not _shutdown_requested:
            time.sleep(seconds_until_next_hour)

    while not _shutdown_requested:
        try:
            run_cycle(args)
        except SystemExit:
            break
        except Exception as e:
            traceback.print_exc()
            try:
                from ai_prophet_core.betting.db import create_db_engine
                db_engine = create_db_engine()
                log_system_event(
                    db_engine,
                    "ERROR",
                    f"Worker loop crashed: {type(e).__name__}: {e}",
                    instance_name=INSTANCE_NAME,
                )
                log_heartbeat(db_engine, message="cycle_error", instance_name=INSTANCE_NAME)
            except Exception:
                pass

        if args.once:
            logger.info("--once flag set, exiting after single cycle.")
            break

        # Calculate time until the next top of the hour
        now = datetime.now(UTC)

        # Find the next hour boundary
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seconds_until_next_hour = (next_hour - now).total_seconds()

        # For peer synchronization, check if we need to wait for other instances
        db_engine_for_sync = None
        try:
            from ai_prophet_core.betting.db import create_db_engine
            db_engine_for_sync = create_db_engine()
        except Exception:
            pass

        max_cycle_end = _get_max_peer_cycle_end(db_engine_for_sync, all_sync_instances)
        if db_engine_for_sync is not None:
            try:
                db_engine_for_sync.dispose()
            except Exception:
                pass

        # If peers are still running and would finish after the next hour, wait longer
        if max_cycle_end is not None:
            max_cycle_end_aware = max_cycle_end.replace(tzinfo=UTC) if max_cycle_end.tzinfo is None else max_cycle_end
            if max_cycle_end_aware > next_hour:
                # Wait until the hour after peers finish
                hours_to_wait = ((max_cycle_end_aware - next_hour).total_seconds() / 3600) + 1
                next_hour = next_hour + timedelta(hours=int(hours_to_wait))
                seconds_until_next_hour = (next_hour - now).total_seconds()
                # Show local time too
                try:
                    import zoneinfo
                    local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                    next_hour_local = next_hour.astimezone(local_tz)
                    logger.info(
                        "Sync: peers still running, waiting until %s UTC / %s PST (%.0f seconds)",
                        next_hour.strftime("%H:%M"),
                        next_hour_local.strftime("%H:%M"),
                        seconds_until_next_hour
                    )
                except:
                    logger.info(
                        "Sync: peers still running, waiting until %s UTC (%.0f seconds)",
                        next_hour.strftime("%H:%M"), seconds_until_next_hour
                    )
            else:
                # Show local time too
                try:
                    import zoneinfo
                    local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                    next_hour_local = next_hour.astimezone(local_tz)
                    logger.info(
                        "Next cycle will run at the top of the hour: %s UTC / %s PST (%.0f seconds)",
                        next_hour.strftime("%H:%M"),
                        next_hour_local.strftime("%H:%M"),
                        seconds_until_next_hour
                    )
                except:
                    logger.info(
                        "Next cycle will run at the top of the hour: %s UTC (%.0f seconds)",
                        next_hour.strftime("%H:%M"), seconds_until_next_hour
                    )
        else:
            # Show local time too
            try:
                import zoneinfo
                local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                next_hour_local = next_hour.astimezone(local_tz)
                logger.info(
                    "Next cycle will run at the top of the hour: %s UTC / %s PST (%.0f seconds)",
                    next_hour.strftime("%H:%M"),
                    next_hour_local.strftime("%H:%M"),
                    seconds_until_next_hour
                )
            except:
                logger.info(
                    "Next cycle will run at the top of the hour: %s UTC (%.0f seconds)",
                    next_hour.strftime("%H:%M"), seconds_until_next_hour
                )

        # Sleep until the next hour, checking for shutdown every second
        for _ in range(int(seconds_until_next_hour)):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
