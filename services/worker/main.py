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
    WORKER_POLL_INTERVAL_SEC  — Poll interval in seconds (default: 7200 = every 2 hours)
    WORKER_POLL_OFFSET_SEC    — Optional phase offset in seconds for cycle boundaries (default: 0)
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
from uuid import uuid4

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from instance_config import env_suffix, get_current_instance_name, get_instance_env
from position_replay import (
    load_replayable_orders,
    replay_orders_by_ticker,
    summarize_replayed_positions,
    sync_replayed_positions,
)
from schedule_utils import next_interval_boundary

load_dotenv()

logger = logging.getLogger("worker")
INSTANCE_NAME = get_current_instance_name()
PREDICTOR_TIMEOUT_SEC = float(os.getenv("PREDICTOR_TIMEOUT_SEC", "180"))
REMOTE_PREDICT_TIMEOUT_SEC = float(
    os.getenv("REMOTE_PREDICT_TIMEOUT_SEC", str(PREDICTOR_TIMEOUT_SEC + 10))
)
EXCLUDED_MARKET_CATEGORIES = {"MENTIONS"}


def _normalized_market_category(category: str | None) -> str:
    return (category or "").strip().upper()


def _contains_excluded_market_marker(value: str | None) -> bool:
    return "MENTION" in _normalized_market_category(value)


def _is_excluded_market_category(category: str | None) -> bool:
    return _normalized_market_category(category) in EXCLUDED_MARKET_CATEGORIES


def _is_excluded_market(
    *,
    category: str | None = None,
    ticker: str | None = None,
    event_ticker: str | None = None,
    title: str | None = None,
) -> bool:
    if _is_excluded_market_category(category):
        return True
    return any(
        _contains_excluded_market_marker(value)
        for value in (ticker, event_ticker, title, category)
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
        "XAI_API_KEY",
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
DEFAULT_WORKER_POLL_INTERVAL_SEC = 2 * 60 * 60  # 2 hours


def _handle_signal(signum, frame):
    global _shutdown_requested
    logger.info("Received signal %s, shutting down gracefully...", signum)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


def _next_cycle_boundary(now: datetime, poll_interval_sec: int) -> datetime:
    """Return the next UTC-aligned cycle boundary for the configured interval."""
    return next_interval_boundary(now, poll_interval_sec, _instance_int_setting("WORKER_POLL_OFFSET_SEC", 0))


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


def save_market_lifecycle_snapshot(
    db_engine,
    market_id: str,
    *,
    ticker: str = "",
    status: str | None = None,
    result: str | None = None,
    instance_name: str = INSTANCE_NAME,
) -> None:
    """Upsert the latest fetched lifecycle status for a tracked market."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarketLifecycle

        normalized_status = (status or "unknown").strip().lower() or "unknown"
        normalized_result = (result or "").strip().lower() or None
        now = datetime.now(UTC)

        with get_session(db_engine) as session:
            existing = session.query(TradingMarketLifecycle).filter_by(
                instance_name=instance_name,
                market_id=market_id,
            ).first()
            if existing:
                existing.ticker = ticker
                existing.status = normalized_status
                existing.result = normalized_result
                existing.updated_at = now
            else:
                session.add(TradingMarketLifecycle(
                    instance_name=instance_name,
                    market_id=market_id,
                    ticker=ticker,
                    status=normalized_status,
                    result=normalized_result,
                    updated_at=now,
                ))
    except Exception as e:
        logger.warning("Failed to save market lifecycle snapshot: %s", e)


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


def log_cycle_skip_for_models(
    db_engine,
    model_names: list[str],
    market_id: str,
    *,
    yes_ask: float | None,
    no_ask: float | None,
    reason: str,
    instance_name: str = INSTANCE_NAME,
) -> None:
    """Persist an explicit per-cycle skip row for every configured model."""
    if db_engine is None:
        return

    metadata = {
        "p_yes": None,
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "skip_reason": reason,
    }
    for model_name in model_names:
        save_model_run(
            db_engine,
            model_name,
            market_id,
            "CYCLE_SKIPPED",
            None,
            metadata=metadata,
            instance_name=instance_name,
        )


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
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            market_rows = (
                session.query(TradingMarket)
                .filter(TradingMarket.instance_name == instance_name)
                .all()
            )
            markets_by_ticker = {
                market.ticker: market for market in market_rows if market.ticker
            }

            orders = load_replayable_orders(session, BettingOrder, instance_name)

            positions = replay_orders_by_ticker(orders)
            sync_replayed_positions(
                session,
                instance_name,
                positions,
                markets_by_ticker=markets_by_ticker,
                log=logger,
            )

        logger.info("Updated %d positions from order history", len(positions))
    except Exception as e:
        logger.warning("Failed to update positions: %s", e)


def sync_pending_orders(db_engine, adapter, instance_name: str) -> int:
    """Compatibility wrapper around the shared Kalshi order sync helper."""
    if db_engine is None:
        return 0

    try:
        from order_management import _sync_pending_order_status

        return _sync_pending_order_status(db_engine, adapter, instance_name)
    except Exception as e:
        logger.error("[SYNC] Failed to sync orders: %s", e)
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
        from sqlalchemy import or_

        with get_session(db_engine) as session:
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == instance_name)
                .filter(
                    or_(
                        BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
                        BettingOrder.filled_shares > 0,
                    )
                )
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


def get_peer_tickers(db_engine, peer_instance_name: str) -> list[str]:
    """Return tickers currently tracked by a peer instance.

    Used by non-fetcher workers to mirror the market list of the designated
    market-fetcher instance instead of independently querying Kalshi.
    """
    if db_engine is None:
        return []
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            rows = (
                session.query(
                    TradingMarket.ticker,
                    TradingMarket.category,
                    TradingMarket.event_ticker,
                    TradingMarket.title,
                )
                .filter(TradingMarket.instance_name == peer_instance_name)
                .order_by(TradingMarket.updated_at.desc())
                .all()
            )
            tickers: list[str] = []
            seen: set[str] = set()
            for ticker, category, event_ticker, title in rows:
                if not ticker or ticker in seen or _is_excluded_market(
                    category=category,
                    ticker=ticker,
                    event_ticker=event_ticker,
                    title=title,
                ):
                    continue
                tickers.append(ticker)
                seen.add(ticker)
            logger.info("Read %d tickers from peer instance '%s'", len(tickers), peer_instance_name)
            return tickers
    except Exception as e:
        logger.warning("Failed to query peer tickers from '%s': %s", peer_instance_name, e)
        return []


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


def purge_excluded_tracked_markets(db_engine, instance_name: str = INSTANCE_NAME) -> int:
    """Delete tracked markets that are excluded from trading and rediscovery."""
    if db_engine is None:
        return 0
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import KalshiOrderSnapshot, TradingMarket, TradingPosition
        from kalshi_state import build_position_views

        with get_session(db_engine) as session:
            live_position_views = build_position_views(session, instance_name)
            protected_tickers = {
                view.ticker
                for view in live_position_views
                if view.ticker
            }
            protected_market_ids = {
                view.market_id
                for view in live_position_views
                if view.market_id
            }
            protected_market_ids.update(
                market_id
                for (market_id,) in (
                    session.query(TradingPosition.market_id)
                    .filter(
                        TradingPosition.instance_name == instance_name,
                        TradingPosition.quantity > 1e-9,
                    )
                    .all()
                )
                if market_id
            )
            protected_tickers.update(
                ticker
                for (ticker,) in (
                    session.query(BettingOrder.ticker)
                    .filter(BettingOrder.instance_name == instance_name)
                    .all()
                )
                if ticker
            )
            protected_tickers.update(
                ticker
                for (ticker,) in (
                    session.query(KalshiOrderSnapshot.ticker)
                    .filter(KalshiOrderSnapshot.instance_name == instance_name)
                    .all()
                )
                if ticker
            )
            rows = (
                session.query(TradingMarket)
                .filter(TradingMarket.instance_name == instance_name)
                .all()
            )
            excluded_rows = [
                row for row in rows
                if _is_excluded_market(
                    category=row.category,
                    ticker=row.ticker,
                    event_ticker=row.event_ticker,
                    title=row.title,
                )
            ]
            removable_rows = [
                row
                for row in excluded_rows
                if row.ticker not in protected_tickers
                and row.market_id not in protected_market_ids
            ]
            for row in removable_rows:
                session.delete(row)
        removed = len(removable_rows)
        preserved = len(excluded_rows) - removed
        if removed:
            logger.info(
                "Removed %d excluded tracked markets for %s before discovery",
                removed,
                instance_name,
            )
        if preserved:
            logger.info(
                "Preserved %d excluded tracked markets for %s because they still have positions or order history",
                preserved,
                instance_name,
            )
        return removed
    except Exception as e:
        logger.warning("Failed to purge excluded tracked markets for %s: %s", instance_name, e)
        return 0


def get_live_position_tickers(db_engine, instance_name: str = INSTANCE_NAME) -> set[str]:
    """Return tickers that still have a live synced Kalshi position."""
    if db_engine is None:
        return set()
    try:
        from ai_prophet_core.betting.db import get_session
        from kalshi_state import build_position_views

        with get_session(db_engine) as session:
            return {
                view.ticker
                for view in build_position_views(session, instance_name)
                if view.ticker
            }
    except Exception as e:
        logger.warning("Failed to query live position tickers: %s", e)
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
        from ai_prophet_core.betting.db_schema import BettingOrder as _BO
        from db_models import TradingMarket as _TM, TradingPosition as _TP

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
            for row in session.query(_TM).filter_by(market_id=market_id).all():
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
                dry_run = str(
                    get_instance_env("LIVE_BETTING_DRY_RUN", pos.instance_name, default="true") or "true"
                ).strip().lower() in ("1", "true", "yes", "on")
                settlement_order = _BO(
                    instance_name=pos.instance_name,
                    signal_id=None,
                    order_id=f"settlement-{ticker}-{uuid4()}",
                    ticker=ticker,
                    action="SELL",
                    side=pos.contract.upper(),
                    count=max(1, int(round(original_qty))),
                    price_cents=int(round(settlement_price * 100)),
                    status="SETTLED",
                    filled_shares=original_qty,
                    fill_price=settlement_price,
                    fee_paid=0.0,
                    exchange_order_id=None,
                    dry_run=dry_run,
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


def fetch_market_lifecycle_by_ticker(adapter, ticker: str) -> dict | None:
    """Fetch raw market lifecycle state even when the market is no longer tradable."""
    base_url = adapter._base_url
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
        if not mkt:
            return None
        return {
            "ticker": ticker,
            "status": str(mkt.get("status", "") or "").lower(),
            "result": str(mkt.get("result", "") or "").lower(),
            "yes_bid": mkt.get("yes_bid_dollars"),
            "yes_ask": mkt.get("yes_ask_dollars"),
            "no_bid": mkt.get("no_bid_dollars"),
            "no_ask": mkt.get("no_ask_dollars"),
            "last_price": mkt.get("last_price_dollars"),
            "close_time": mkt.get("close_time"),
            "open_time": mkt.get("open_time"),
            "volume_24h": mkt.get("volume_24h_fp", 0),
        }
    except Exception as e:
        logger.debug("Failed to fetch market lifecycle for %s: %s", ticker, e)
        return None


def fetch_market_by_ticker(
    adapter,
    ticker: str,
    *,
    allow_excluded: bool = False,
    allow_inactive: bool = False,
) -> dict | None:
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

        status = str(mkt.get("status", "") or "").lower()
        if status not in ("open", "active") and not allow_inactive:
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

        if _is_excluded_market(
            category=category,
            ticker=ticker,
            event_ticker=event_ticker,
            title=title,
        ) and not allow_excluded:
            return None

        return {
            "ticker": ticker,
            "event_ticker": event_ticker,
            "title": title,
            "subtitle": mkt.get("rules_primary", ""),
            "category": category,
            "status": status,
            "result": str(mkt.get("result", "") or "").lower(),
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

def fetch_kalshi_markets(adapter, max_markets: int = 10, max_pages: int | None = None) -> list[dict]:
    """Fetch active binary markets from Kalshi via the events endpoint.

    Uses /trade-api/v2/events with nested markets.  Paginates through all
    pages, collects candidates closing within 3 days, then returns the first
    ``max_markets`` that pass the discovery filters.

    Markets with prices outside the 90/10 band are excluded from discovery.
    """
    base_url = adapter._base_url
    path = "/trade-api/v2/events"
    cutoff = datetime.now(UTC) + timedelta(days=3)

    candidates: list[dict] = []
    seen_tickers: set[str] = set()
    cursor = ""
    total_events = 0
    pages_scanned = 0

    while True:
        if max_pages is not None and pages_scanned >= max_pages:
            break

        pages_scanned += 1
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
            logger.error("Failed to fetch Kalshi events (page %d): %s", pages_scanned, e)
            break

        if not events:
            break

        for event in events:
            event_title = event.get("title", "Unknown")
            category = event.get("category", "")
            event_ticker = event.get("ticker", "")
            if _is_excluded_market(
                category=category,
                event_ticker=event_ticker,
                title=event_title,
            ):
                continue

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
                if not ticker or ticker in seen_tickers:
                    continue
                market_event_ticker = mkt.get("event_ticker", "") or event_ticker
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
                if _is_excluded_market(
                    category=category,
                    ticker=ticker,
                    event_ticker=market_event_ticker,
                    title=market_title,
                ):
                    continue

                _ya = float(yes_ask) if yes_ask is not None else price
                _na = float(no_ask) if no_ask is not None else (1.0 - price)
                # Require both sides to stay inside the 10c-90c range.
                # Example: 95c/8c gets excluded because it is more extreme than 90-10.
                if _ya < 0.10 or _ya > 0.90 or _na < 0.10 or _na > 0.90:
                    continue

                candidates.append({
                    "ticker": ticker,
                    "event_ticker": market_event_ticker,
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
                    "volume_24h": float(mkt.get("volume_24h_fp", 0) or 0),
                })
                seen_tickers.add(ticker)

        if not cursor:
            break

        logger.debug("Page %d: %d candidates so far, fetching more...", pages_scanned, len(candidates))

    markets = candidates[:max_markets]

    logger.info(
        "Selected %d markets (from %d candidates, %d events, %d pages)",
        len(markets), len(candidates), total_events, pages_scanned,
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

    # Use GOOGLE_API_KEY as the primary key (Gemini is a Google service)
    api_key = _instance_specific_setting("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError(
            f"GOOGLE_API_KEY_{env_suffix(INSTANCE_NAME)} env var required for Gemini"
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

    # Gather API keys from environment to pass in request
    api_keys = {}

    # OpenAI
    openai_key = _instance_setting("OPENAI_API_KEY", "")
    if openai_key:
        api_keys["openai"] = openai_key

    # Anthropic
    anthropic_key = _instance_setting("ANTHROPIC_API_KEY", "")
    if anthropic_key:
        api_keys["anthropic"] = anthropic_key

    # Google/Gemini - use instance-specific keys (GOOGLE_API_KEY is the primary)
    google_key = _instance_specific_setting("GOOGLE_API_KEY")
    if google_key:
        api_keys["google"] = google_key
        api_keys["gemini"] = google_key  # For backwards compatibility

    # xAI/Grok
    xai_key = _instance_setting("XAI_API_KEY", "")
    if xai_key:
        api_keys["xai"] = xai_key

    resp = requests.post(
        f"{service_url}/predict",
        json={
            "model_spec": model_spec,
            "market_info": market_info,
            "instance_name": INSTANCE_NAME,
            "api_keys": api_keys,  # Pass API keys in request body
        },
        headers={"X-API-Key": api_key} if api_key else {},  # Optional auth header
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

    # Check if another instance is already running a cycle
    if db_engine is not None:
        try:
            from sqlalchemy import text
            with db_engine.connect() as conn:
                # Check for recent cycle_start without cycle_end in last 10 minutes
                result = conn.execute(text("""
                    SELECT COUNT(*) FROM system_logs
                    WHERE instance_name = :instance
                    AND message = 'cycle_start'
                    AND created_at > NOW() - INTERVAL '10 minutes'
                    AND NOT EXISTS (
                        SELECT 1 FROM system_logs sl2
                        WHERE sl2.instance_name = :instance
                        AND sl2.message = 'cycle_end'
                        AND sl2.created_at > system_logs.created_at
                    )
                """), {"instance": INSTANCE_NAME}).scalar()

                if result > 0:
                    logger.warning(
                        "[CYCLE] Another %s worker appears to be running. Skipping this cycle to prevent duplicates.",
                        INSTANCE_NAME
                    )
                    db_engine.dispose()
                    return
        except Exception as e:
            logger.warning("[CYCLE] Could not check for duplicate workers: %s", e)

    if db_engine is not None:
        log_heartbeat(db_engine, message="cycle_start", instance_name=INSTANCE_NAME)
        from ai_prophet_core.betting.db_schema import Base as CoreBase
        CoreBase.metadata.create_all(db_engine, checkfirst=True)

    if betting_engine is None:
        logger.error("Betting engine not available, skipping cycle")
        if db_engine is not None:
            db_engine.dispose()
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
            f"models={model_specs}, strategy={strategy_name}, max_active={max_active}, "
            f"dry_run={betting_engine.dry_run}",
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
    purged_markets = purge_excluded_tracked_markets(db_engine, INSTANCE_NAME)
    if purged_markets and db_engine is not None:
        log_system_event(
            db_engine,
            "INFO",
            f"Removed {purged_markets} excluded tracked markets before discovery",
            instance_name=INSTANCE_NAME,
        )
    live_position_tickers = get_live_position_tickers(db_engine, INSTANCE_NAME)
    tracked_tickers = (
        get_tracked_tickers(db_engine, INSTANCE_NAME)
        | get_traded_tickers(db_engine, INSTANCE_NAME)
        | live_position_tickers
    )
    sticky_markets: list[dict] = []

    if tracked_tickers:
        logger.info("Re-fetching %d sticky markets: %s", len(tracked_tickers), tracked_tickers)
        for ticker in tracked_tickers:
            keep_for_display = ticker in live_position_tickers
            mkt = fetch_market_by_ticker(
                adapter,
                ticker,
                allow_excluded=keep_for_display,
                allow_inactive=keep_for_display,
            )
            if mkt:
                market_status = str(mkt.get("status", "") or "").lower()
                market_result = str(mkt.get("result", "") or "").lower()
                if db_engine is not None:
                    save_market_lifecycle_snapshot(
                        db_engine,
                        f"kalshi:{ticker}",
                        ticker=ticker,
                        status=market_status,
                        result=market_result,
                        instance_name=INSTANCE_NAME,
                    )
                if market_result in ("yes", "no") and market_status not in ("open", "active"):
                    logger.info("  Sticky market %s resolved/closed, marking in DB", ticker)
                    if db_engine is not None:
                        _mark_market_resolved(db_engine, adapter, ticker)
                    continue
                sticky_markets.append(mkt)
            else:
                lifecycle_market = fetch_market_lifecycle_by_ticker(adapter, ticker)
                if lifecycle_market and db_engine is not None:
                    save_market_lifecycle_snapshot(
                        db_engine,
                        f"kalshi:{ticker}",
                        ticker=ticker,
                        status=str(lifecycle_market.get("status", "") or "").lower(),
                        result=str(lifecycle_market.get("result", "") or "").lower(),
                        instance_name=INSTANCE_NAME,
                    )
                    if lifecycle_market.get("result") in ("yes", "no") and lifecycle_market.get("status") not in ("open", "active"):
                        logger.info("  Sticky market %s resolved/closed, marking in DB", ticker)
                        _mark_market_resolved(db_engine, adapter, ticker)
                logger.info("  Sticky market %s no longer has active market data", ticker)
                if db_engine is not None:
                    log_cycle_skip_for_models(
                        db_engine,
                        model_specs,
                        f"kalshi:{ticker}",
                        yes_ask=None,
                        no_ask=None,
                        reason="Skipped because live market data could not be fetched for this cycle.",
                        instance_name=INSTANCE_NAME,
                    )

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
                peer_tickers = [
                    ticker
                    for ticker in get_peer_tickers(db_engine, peer)
                    if ticker not in tracked_tickers
                ]
                logger.info("Mirroring %d new tickers from peer '%s'", len(peer_tickers), peer)
                new_markets = []
                for ticker in peer_tickers[:new_slots]:
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
            db_engine.dispose()
        if betting_engine:
            betting_engine.close()
        return

    # Collect all market prices across models for position updates
    all_market_prices: dict[str, tuple[float, float]] = {}
    # Track (market_id, model, edge) for alert checking
    all_edges: list[tuple[str, str, float]] = []

    # ── Phase A: Pre-filter markets (sequential) ────────────────────
    # Validate prices, save snapshots, skip unchanged.
    # Build a list of markets that need LLM analysis.
    tick_ts = datetime.now(UTC)
    total_results = []

    markets_to_analyze: list[dict] = []  # enriched market dicts

    # Pre-load latest prediction prices per market for price-movement check
    # (single query instead of per-market to avoid DB pool exhaustion)
    last_pred_prices: dict[str, tuple[float, float]] = {}
    if db_engine is not None:
        try:
            from sqlalchemy import text as sa_text
            with db_engine.connect() as conn:
                result = conn.execute(sa_text("""
                    SELECT DISTINCT ON (market_id) market_id, yes_ask, no_ask
                    FROM betting_predictions
                    WHERE instance_name = :instance
                    ORDER BY market_id, created_at DESC
                """), {"instance": INSTANCE_NAME})
                for row in result:
                    last_pred_prices[row[0]] = (float(row[1]), float(row[2]))
            logger.info("Loaded %d latest prediction prices for price-movement filter", len(last_pred_prices))
        except Exception as e:
            logger.warning("Failed to pre-load prediction prices: %s", e)

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

        market_id = f"kalshi:{ticker}"

        if db_engine:
            save_market_lifecycle_snapshot(
                db_engine,
                market_id,
                ticker=ticker,
                status=str(market.get("status", "") or "").lower(),
                result=str(market.get("result", "") or "").lower(),
                instance_name=INSTANCE_NAME,
            )

        # Save market snapshot for dashboard even when the market is skipped for this cycle.
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

        if _is_excluded_market(
            category=category,
            ticker=ticker,
            event_ticker=market.get("event_ticker", ""),
            title=title,
        ):
            logger.debug("Skipping %s: mentions markets are excluded from tracking and betting", ticker)
            log_cycle_skip_for_models(
                db_engine,
                model_specs,
                market_id,
                yes_ask=yes_ask,
                no_ask=no_ask,
                reason="Skipped because mentions markets are excluded from new trading, but tracked holdings stay visible.",
                instance_name=INSTANCE_NAME,
            )
            all_market_prices[market_id] = (yes_ask, no_ask)
            continue

        # Spread filter removed - no longer checking MAX_SPREAD

        if db_engine:
            save_price_snapshot(
                db_engine, market_id, ticker,
                yes_ask=yes_ask, no_ask=no_ask,
                volume_24h=float(market.get("volume_24h", 0) or 0),
                instance_name=INSTANCE_NAME,
            )

        # Skip markets where the price hasn't moved 10+ cents since last forecast
        MIN_PRICE_MOVEMENT = 0.10
        prev_prices = last_pred_prices.get(market_id)
        if prev_prices is not None:
            last_yes, last_no = prev_prices
            max_dev = max(abs(yes_ask - last_yes), abs(no_ask - last_no))
            if max_dev < MIN_PRICE_MOVEMENT:
                logger.info(
                    "Skipping LLM for %s: price unchanged (%.1f¢ < %.0f¢ threshold). "
                    "Last forecast: YES %.3f, NO %.3f → Current: YES %.3f, NO %.3f",
                    ticker, max_dev * 100, MIN_PRICE_MOVEMENT * 100,
                    last_yes, last_no, yes_ask, no_ask,
                )
                log_cycle_skip_for_models(
                    db_engine,
                    model_specs,
                    market_id,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    reason=f"Skipped because the market price moved only {max_dev*100:.1f}c since last forecast (need {MIN_PRICE_MOVEMENT*100:.0f}c).",
                    instance_name=INSTANCE_NAME,
                )
                all_market_prices[market_id] = (yes_ask, no_ask)
                continue

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
            if db_engine is not None:
                db_engine.dispose()
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
                log_cycle_skip_for_models(
                    db_engine,
                    model_specs,
                    market_id,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    reason="Skipped because no model predictions were available for this cycle.",
                    instance_name=INSTANCE_NAME,
                )
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

        if db_engine and betting_engine is not None:
            for ms, pred in model_predictions.items():
                try:
                    betting_engine.strategy._portfolio = portfolio
                    skip_reason = None
                    strategy_signal = betting_engine.strategy.evaluate(
                        market_id=market_id,
                        p_yes=pred["p_yes"],
                        yes_ask=yes_ask,
                        no_ask=no_ask,
                    )
                    strategy_metadata = strategy_signal.metadata if strategy_signal is not None else None

                    # Log detailed strategy evaluation results for debugging
                    edge = pred["p_yes"] - yes_ask
                    current_pos_info = ""
                    if portfolio and portfolio.market_position_shares > 0:
                        current_pos_info = f", pos={portfolio.market_position_side}/{float(portfolio.market_position_shares):.2f}"
                    logger.debug(
                        "%s: p_yes=%.2f, yes_ask=%.2f, no_ask=%.2f, edge=%.2f%s, signal=%s",
                        ticker, pred["p_yes"], yes_ask, no_ask, edge, current_pos_info,
                        f"{strategy_signal.side}/{strategy_signal.shares:.3f}/${strategy_signal.cost:.2f}" if strategy_signal else "None"
                    )

                    if strategy_signal is None:
                        decision = "HOLD"
                    elif strategy_metadata and strategy_metadata.get("flatten_reason") == "WITHIN_SPREAD":
                        decision = "HOLD"
                    elif strategy_signal.cost > 50.0:
                        decision = "SKIP"
                        skip_reason = "Skipped because the order would consume more than $50 of capital."
                    elif strategy_signal.side in ["yes", "no"]:
                        decision = f"BUY_{strategy_signal.side.upper()}"
                    else:
                        decision = "HOLD"
                except Exception as e:
                    logger.warning("Strategy evaluation failed for %s model %s: %s", ticker, ms, e)
                    decision = "HOLD"
                    strategy_metadata = None
                    skip_reason = None

                save_model_run(
                    db_engine, ms, market_id, decision, pred.get("confidence"),
                    metadata={"p_yes": pred["p_yes"], "reasoning": pred.get("reasoning", ""),
                              "analysis": pred.get("analysis", {}),
                              "sources": pred.get("sources", []),
                              "yes_ask": yes_ask, "no_ask": no_ask,
                              "strategy": strategy_metadata,
                              "skip_reason": skip_reason},
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
        traded = get_traded_tickers(db_engine, INSTANCE_NAME) | live_position_tickers
        for ticker in traded:
            market_id = f"kalshi:{ticker}"
            if market_id not in all_market_prices:
                keep_for_display = ticker in live_position_tickers
                mkt = fetch_market_by_ticker(
                    adapter,
                    ticker,
                    allow_excluded=keep_for_display,
                    allow_inactive=keep_for_display,
                )
                if not mkt:
                    continue

                yes_ask = mkt.get("yes_ask")
                no_ask = mkt.get("no_ask")
                if yes_ask is None or no_ask is None:
                    last_price = mkt.get("last_price")
                    if last_price is not None:
                        yes_ask = float(last_price)
                        no_ask = 1.0 - yes_ask

                if yes_ask is not None and no_ask is not None:
                    all_market_prices[market_id] = (float(yes_ask), float(no_ask))

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
        db_engine.dispose()

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

    poll_interval = _instance_int_setting("WORKER_POLL_INTERVAL_SEC", DEFAULT_WORKER_POLL_INTERVAL_SEC)
    poll_offset_sec = _instance_int_setting("WORKER_POLL_OFFSET_SEC", 0)
    peer_instances_str = _instance_setting("WORKER_PEER_INSTANCES", "")
    peer_instances = [p.strip() for p in peer_instances_str.split(",") if p.strip()] if peer_instances_str else []
    all_sync_instances = list({INSTANCE_NAME} | set(peer_instances))

    logger.info(
        "Worker starting (instance=%s, poll_interval=%ds, poll_offset=%ds, cli_dry_run=%s, env_dry_run=%s, peers=%s)",
        INSTANCE_NAME,
        poll_interval,
        poll_offset_sec,
        args.dry_run,
        _instance_bool_setting("LIVE_BETTING_DRY_RUN", True),
        peer_instances or "none",
    )
    logger.info("Mode: STANDALONE (direct Kalshi API + LLM predictions)")

    # Wait for the next configured cycle boundary before starting (unless --once flag is set)
    if not args.once:
        now = datetime.now(UTC)
        next_cycle = _next_cycle_boundary(now, poll_interval)
        seconds_until_next_cycle = (next_cycle - now).total_seconds()

        logger.info(
            "Waiting until next cycle boundary: %s UTC (%.0f seconds, interval=%ds, offset=%ds)",
            next_cycle.strftime("%H:%M"), seconds_until_next_cycle, poll_interval, poll_offset_sec
        )

        # Show local time too
        try:
            import zoneinfo
            local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
            next_cycle_local = next_cycle.astimezone(local_tz)
            logger.info(
                "First cycle will run at: %s UTC / %s PST",
                next_cycle.strftime("%H:%M"),
                next_cycle_local.strftime("%H:%M")
            )
        except:
            pass

        if seconds_until_next_cycle > 0 and not _shutdown_requested:
            time.sleep(seconds_until_next_cycle)

    while not _shutdown_requested:
        try:
            run_cycle(args)
        except SystemExit:
            break
        except Exception as e:
            traceback.print_exc()
            try:
                from ai_prophet_core.betting.db import create_db_engine
                _err_engine = create_db_engine()
                log_system_event(
                    _err_engine,
                    "ERROR",
                    f"Worker loop crashed: {type(e).__name__}: {e}",
                    instance_name=INSTANCE_NAME,
                )
                log_heartbeat(_err_engine, message="cycle_error", instance_name=INSTANCE_NAME)
                _err_engine.dispose()
            except Exception:
                pass

        if args.once:
            logger.info("--once flag set, exiting after single cycle.")
            break

        # Calculate time until the next configured cycle boundary
        now = datetime.now(UTC)

        # Find the next cycle boundary
        next_cycle = _next_cycle_boundary(now, poll_interval)
        seconds_until_next_cycle = (next_cycle - now).total_seconds()

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

        # If peers are still running and would finish after the next cycle boundary, wait longer
        if max_cycle_end is not None:
            max_cycle_end_aware = max_cycle_end.replace(tzinfo=UTC) if max_cycle_end.tzinfo is None else max_cycle_end
            if max_cycle_end_aware > next_cycle:
                # Wait until the interval boundary after peers finish.
                next_cycle = _next_cycle_boundary(max_cycle_end_aware, poll_interval)
                seconds_until_next_cycle = (next_cycle - now).total_seconds()
                # Show local time too
                try:
                    import zoneinfo
                    local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                    next_cycle_local = next_cycle.astimezone(local_tz)
                    logger.info(
                        "Sync: peers still running, waiting until %s UTC / %s PST (%.0f seconds)",
                        next_cycle.strftime("%H:%M"),
                        next_cycle_local.strftime("%H:%M"),
                        seconds_until_next_cycle
                    )
                except:
                    logger.info(
                        "Sync: peers still running, waiting until %s UTC (%.0f seconds)",
                        next_cycle.strftime("%H:%M"), seconds_until_next_cycle
                    )
            else:
                # Show local time too
                try:
                    import zoneinfo
                    local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                    next_cycle_local = next_cycle.astimezone(local_tz)
                    logger.info(
                        "Next cycle will run at the next %d-hour boundary: %s UTC / %s PST (%.0f seconds)",
                        max(1, poll_interval // 3600),
                        next_cycle.strftime("%H:%M"),
                        next_cycle_local.strftime("%H:%M"),
                        seconds_until_next_cycle
                    )
                except:
                    logger.info(
                        "Next cycle will run at the next %d-hour boundary: %s UTC (%.0f seconds)",
                        max(1, poll_interval // 3600),
                        next_cycle.strftime("%H:%M"), seconds_until_next_cycle
                    )
        else:
            # Show local time too
            try:
                import zoneinfo
                local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
                next_cycle_local = next_cycle.astimezone(local_tz)
                logger.info(
                    "Next cycle will run at the next %d-hour boundary: %s UTC / %s PST (%.0f seconds)",
                    max(1, poll_interval // 3600),
                    next_cycle.strftime("%H:%M"),
                    next_cycle_local.strftime("%H:%M"),
                    seconds_until_next_cycle
                )
            except:
                logger.info(
                    "Next cycle will run at the next %d-hour boundary: %s UTC (%.0f seconds)",
                    max(1, poll_interval // 3600),
                    next_cycle.strftime("%H:%M"), seconds_until_next_cycle
                )

        # Sleep until the next cycle boundary, checking for shutdown every second
        for _ in range(int(seconds_until_next_cycle)):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("Worker stopped.")


if __name__ == "__main__":
    main()
