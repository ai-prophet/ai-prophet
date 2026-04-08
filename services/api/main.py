"""FastAPI backend for the Kalshi trading dashboard.

Serves trade history, positions, P&L, markets, and health data
by reading from the shared Supabase PostgreSQL database.

Usage:
    uvicorn services.api.main:app --reload
    cd services/api && uvicorn main:app --reload

Environment variables:
    DATABASE_URL              — PostgreSQL connection string (required)
    KALSHI_API_KEY_ID         — For live position fetching (optional)
    KALSHI_PRIVATE_KEY_B64    — For live position fetching (optional)
    API_CORS_ORIGINS          — Comma-separated allowed origins (default: *)
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()

import logging

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, text
from instance_config import DEFAULT_INSTANCE_NAME, get_instance_env, normalize_instance_name
from position_replay import EPSILON, InventoryPosition, load_replayable_orders, normalize_order

logger = logging.getLogger(__name__)

from ai_prophet_core.betting.db import create_db_engine, get_session
from ai_prophet_core.betting.config import MAX_SPREAD
from ai_prophet_core.betting.db_schema import (
    Base as CoreBase,
    BettingDeferredFlip,
    BettingOrder,
    BettingPrediction,
    BettingSignal,
)

# Import dashboard-specific models
from db_models import (
    AlertDismissal,
    KalshiBalanceSnapshot,
    KalshiOrderSnapshot,
    KalshiPositionSnapshot,
    MarketPriceSnapshot,
    ModelRun,
    SystemLog,
    TradingMarket,
    TradingMarketLifecycle,
    TradingPosition,
)
from kalshi_state import (
    build_latest_order_activity_by_ticker,
    build_pending_orders_by_ticker,
    build_portfolio_summary,
    build_position_views,
    get_latest_balance_snapshot,
    get_latest_order_snapshots,
    get_latest_position_snapshots,
)

# ── App setup ─────────────────────────────────────────────────────

_db_engine = None
MIN_PROFITABLE_PRICE = 0.03
MAX_PROFITABLE_PRICE = 0.97
MIN_REBALANCE_TRADE = 0.005
WITHIN_SPREAD_BUFFER = 0.02
DISPLAY_CUTOFF_UTC = datetime(2026, 3, 24, 23, 0, tzinfo=UTC)  # Mar 24, 2026 6:00 PM America/Chicago
DISPLAY_CUTOFF_LABEL = "Mar 24, 2026 6:00 PM CDT"
DISPLAY_BASELINE_OVERRIDES: dict[str, float] = {
    "Haifeng": 441.65,
    "Jibang": 475.43,
}
MAX_CYCLE_RUNNING_AGE_SEC = 60 * 60


def _deferred_flip_pending_reason(status: str, last_error: str | None) -> str:
    normalized = (status or "").upper()
    if normalized == "WAITING_SELL":
        return "Queued after the sell leg finishes."
    if normalized == "WAITING_POSITION_SYNC":
        return "Queued after Kalshi position sync catches up."
    if normalized == "WAITING_RETRY":
        return last_error or "Queued to retry at the original limit price."
    return last_error or "Queued as the deferred second leg of this rebalance."


def get_db():
    global _db_engine
    if _db_engine is None:
        _db_engine = create_db_engine()
    return _db_engine


def _display_visible_ts(value: datetime | None) -> bool:
    return value is not None and value >= DISPLAY_CUTOFF_UTC


def _display_visible_market_activity(session, instance_name: str) -> tuple[set[str], set[str]]:
    visible_tickers = {
        ticker
        for (ticker,) in (
            _instance_query(session, BettingOrder, instance_name)
            .filter(BettingOrder.created_at >= DISPLAY_CUTOFF_UTC)
            .with_entities(BettingOrder.ticker)
            .all()
        )
        if ticker
    }
    visible_market_ids = {
        market_id
        for (market_id,) in (
            _instance_query(session, ModelRun, instance_name)
            .filter(ModelRun.timestamp >= DISPLAY_CUTOFF_UTC)
            .with_entities(ModelRun.market_id)
            .all()
        )
        if market_id
    }
    visible_market_ids.update(
        market_id
        for (market_id,) in (
            _instance_query(session, BettingPrediction, instance_name)
            .filter(BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC)
            .with_entities(BettingPrediction.market_id)
            .all()
        )
        if market_id
    )
    visible_tickers.update(
        ticker
        for (ticker,) in (
            session.query(KalshiOrderSnapshot.ticker)
            .filter(
                KalshiOrderSnapshot.instance_name == instance_name,
                or_(
                    KalshiOrderSnapshot.created_ts >= DISPLAY_CUTOFF_UTC,
                    KalshiOrderSnapshot.last_update_ts >= DISPLAY_CUTOFF_UTC,
                ),
            )
            .all()
            )
            if ticker
        )
    live_position_views = build_position_views(session, instance_name)
    visible_tickers.update(
        view.ticker
        for view in live_position_views
        if view.ticker
    )
    visible_market_ids.update(
        view.market_id
        for view in live_position_views
        if view.market_id
    )
    visible_market_ids.update(
        market_id
        for (market_id,) in (
            _instance_query(session, TradingPosition, instance_name)
            .filter(TradingPosition.quantity > 1e-9)
            .with_entities(TradingPosition.market_id)
            .all()
        )
        if market_id
    )
    if visible_market_ids:
        visible_tickers.update(
            ticker
            for (ticker,) in (
                _instance_query(session, TradingMarket, instance_name)
                .filter(TradingMarket.market_id.in_(visible_market_ids))
                .with_entities(TradingMarket.ticker)
                .all()
            )
            if ticker
    )
    return visible_tickers, visible_market_ids


def _latest_predictions_by_market_id(
    session,
    instance_name: str,
    market_ids: list[str] | set[str] | tuple[str, ...],
) -> dict[str, BettingPrediction]:
    market_id_list = sorted({market_id for market_id in market_ids if market_id})
    if not market_id_list:
        return {}

    latest_created_at = (
        session.query(
            BettingPrediction.market_id.label("market_id"),
            func.max(BettingPrediction.created_at).label("latest_created_at"),
        )
        .filter(
            BettingPrediction.instance_name == instance_name,
            BettingPrediction.market_id.in_(market_id_list),
        )
        .group_by(BettingPrediction.market_id)
        .subquery()
    )

    rows = (
        session.query(BettingPrediction)
        .join(
            latest_created_at,
            (BettingPrediction.market_id == latest_created_at.c.market_id)
            & (BettingPrediction.created_at == latest_created_at.c.latest_created_at),
        )
        .filter(BettingPrediction.instance_name == instance_name)
        .order_by(BettingPrediction.market_id.asc(), BettingPrediction.id.desc())
        .all()
    )

    latest_by_market_id: dict[str, BettingPrediction] = {}
    for row in rows:
        latest_by_market_id.setdefault(row.market_id, row)
    return latest_by_market_id


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    try:
        engine = get_db()
        CoreBase.metadata.create_all(engine, checkfirst=True)
        with get_session(engine) as session:
            session.execute(text("SELECT 1"))
    except Exception as e:
        print(f"WARNING: DB connection failed at startup: {e}")
    yield
    # Shutdown
    if _db_engine is not None:
        _db_engine.dispose()


app = FastAPI(
    title="Kalshi Trading Dashboard API",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
_DEFAULT_CORS = "http://localhost:3000,https://kalshi-trading-dashboard.onrender.com"
cors_origins = os.getenv("API_CORS_ORIGINS", _DEFAULT_CORS).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch all unhandled exceptions and return JSON so CORS middleware can add headers.

    Without this, SQLAlchemy errors (OperationalError, TimeoutError) escape
    ExceptionMiddleware, hit ServerErrorMiddleware outside the CORS layer, and
    return a plain-text 500 with no Access-Control-Allow-Origin header — which
    the browser reports as "Failed to fetch" instead of a meaningful error.
    """
    logger.error("Unhandled exception on %s %s: %s", request.method, request.url.path, exc, exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


# ── Helpers ──────────────────────────────────────────────────────


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns *default* when denominator is zero or near-zero."""
    if abs(denominator) < 1e-12:
        return default
    return numerator / denominator


def _instance_name(instance_name: str | None) -> str:
    return normalize_instance_name(instance_name or os.getenv("TRADING_INSTANCE_NAME", DEFAULT_INSTANCE_NAME))


def _instance_filter(query, model, instance_name: str):
    if hasattr(model, "instance_name"):
        return query.filter(model.instance_name == instance_name)
    return query


def _instance_query(session, model, instance_name: str):
    return _instance_filter(session.query(model), model, instance_name)


def _instance_setting(key: str, instance_name: str, default: str) -> str:
    return str(get_instance_env(key, instance_name, default=default) or default)


def _instance_bool_setting(key: str, instance_name: str, default: bool) -> bool:
    raw = _instance_setting(key, instance_name, "true" if default else "false").strip().lower()
    return raw in ("true", "1", "yes", "on")


def _instance_list_setting(key: str, instance_name: str) -> list[str]:
    raw = _instance_setting(key, instance_name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _instance_float_setting(key: str, instance_name: str, default: float) -> float:
    raw = _instance_setting(key, instance_name, str(default))
    try:
        return float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return float(default)


def _display_baseline_for_instance(session, instance_name: str) -> dict[str, Any]:
    initial_loaded = _instance_float_setting("WORKER_STARTING_CASH", instance_name, 500.0)
    hardcoded_starting_total = DISPLAY_BASELINE_OVERRIDES.get(instance_name)

    if hardcoded_starting_total is not None:
        return {
            "instance_name": instance_name,
            "initial_loaded": round(initial_loaded, 2),
            "balance": round(hardcoded_starting_total, 2),
            "portfolio_value": 0.0,
            "starting_total": round(hardcoded_starting_total, 2),
            "difference_from_initial": round(hardcoded_starting_total - initial_loaded, 2),
            "snapshot_ts": None,
            "used_fallback": False,
        }

    cutoff_snapshot = (
        session.query(KalshiBalanceSnapshot)
        .filter(
            KalshiBalanceSnapshot.instance_name == instance_name,
            KalshiBalanceSnapshot.snapshot_ts >= DISPLAY_CUTOFF_UTC,
        )
        .order_by(KalshiBalanceSnapshot.snapshot_ts.asc(), KalshiBalanceSnapshot.id.asc())
        .first()
    )

    if cutoff_snapshot is not None:
        balance = float(cutoff_snapshot.balance or 0.0)
        portfolio_value = float(cutoff_snapshot.portfolio_value or 0.0)
        snapshot_ts = cutoff_snapshot.snapshot_ts.isoformat()
        used_fallback = False
    else:
        balance = initial_loaded
        portfolio_value = 0.0
        snapshot_ts = None
        used_fallback = True

    starting_total = balance + portfolio_value

    return {
        "instance_name": instance_name,
        "initial_loaded": round(initial_loaded, 2),
        "balance": round(balance, 2),
        "portfolio_value": round(portfolio_value, 2),
        "starting_total": round(starting_total, 2),
        "difference_from_initial": round(starting_total - initial_loaded, 2),
        "snapshot_ts": snapshot_ts,
        "used_fallback": used_fallback,
    }


def _hold_reason_from_market_context(
    *,
    model_decision: str | None,
    strategy_metadata: dict[str, Any] | None = None,
    has_order: bool = False,
    p_yes: float | None,
    yes_ask: float | None,
    no_ask: float | None,
) -> str:
    if yes_ask is None or no_ask is None or p_yes is None:
        return "Holding because no trade was placed this cycle."

    if strategy_metadata and strategy_metadata.get("flatten_reason") == "WITHIN_SPREAD":
        lower_bound = strategy_metadata.get("lower_bound")
        upper_bound = strategy_metadata.get("upper_bound")
        current_pos = strategy_metadata.get("current_pos")
        if lower_bound is not None and upper_bound is not None and current_pos is not None:
            return (
                f"Holding because the model stayed within spread, so the strategy flattened the existing "
                f"position of {abs(float(current_pos)) * 100:.0f} contracts back to zero inside the "
                f"[{float(lower_bound) * 100:.1f}%, {float(upper_bound) * 100:.1f}%] band."
            )
        return "Holding because the model stayed within spread, so the strategy flattened the existing position back to zero."

    spread = yes_ask + no_ask
    lower_bound = max(0.0, 1.0 - no_ask - WITHIN_SPREAD_BUFFER)
    upper_bound = min(1.0, yes_ask + WITHIN_SPREAD_BUFFER)
    edge = (p_yes - yes_ask) * 100

    if spread > MAX_SPREAD:
        return f"No trade because the bid-ask spread is too wide ({spread:.2f} > {MAX_SPREAD:.2f})."

    if spread < 0.90:
        return f"No trade because the market prices look crossed or unreliable (spread {spread:.2f} < 0.90)."

    if lower_bound <= p_yes <= upper_bound:
        return (
            f"Holding because the model probability {(p_yes * 100):.1f}% sits inside the market band "
            f"[{(lower_bound * 100):.1f}%, {(upper_bound * 100):.1f}%], so there is no clear edge."
        )

    return f"No trade was placed this cycle despite edge {edge:.1f}%."


def _heartbeat_components() -> tuple[str, ...]:
    """Components that may emit liveness heartbeats."""
    return ("worker", "comparison_worker")


def _heartbeat_query(session, instance_name: str):
    return (
        _instance_query(session, SystemLog, instance_name)
        .filter(
            SystemLog.level == "HEARTBEAT",
            SystemLog.component.in_(_heartbeat_components()),
        )
    )


def _worker_poll_interval(instance_name: str) -> int:
    return int(_instance_setting("WORKER_POLL_INTERVAL_SEC", instance_name, "14400"))


def _sync_poll_interval(instance_name: str) -> int:
    return int(_instance_setting("SYNC_INTERVAL_SEC", instance_name, "1800"))


def _worker_stale_threshold_sec(instance_name: str) -> int:
    return max(1800, int(_worker_poll_interval(instance_name) * 1.5))


def _log_created_at_utc(row: Any | None) -> datetime | None:
    if row is None:
        return None
    created_at = getattr(row, "created_at", None)
    if created_at is None:
        return None
    return created_at if created_at.tzinfo is not None else created_at.replace(tzinfo=UTC)


def _is_cycle_running(
    cycle_start_row: Any | None,
    cycle_end_row: Any | None,
    *,
    now: datetime,
    max_age_sec: int = MAX_CYCLE_RUNNING_AGE_SEC,
) -> bool:
    start_ts = _log_created_at_utc(cycle_start_row)
    if start_ts is None:
        return False

    if (now - start_ts).total_seconds() > max_age_sec:
        return False

    end_ts = _log_created_at_utc(cycle_end_row)
    if end_ts is None:
        return True
    return start_ts > end_ts


def _build_kalshi_adapter(instance_name: str):
    from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter

    dry_run = _instance_bool_setting("LIVE_BETTING_DRY_RUN", instance_name, True)
    api_key_id = _instance_setting("KALSHI_API_KEY_ID", instance_name, "")
    private_key_base64 = _instance_setting("KALSHI_PRIVATE_KEY_B64", instance_name, "")

    # Log which credentials are being used for debugging
    suffix = instance_name.replace(" ", "_").upper()
    using_instance_specific = (
        f"KALSHI_API_KEY_ID_{suffix}" in os.environ
    )
    logger.info(
        f"Building Kalshi adapter for {instance_name}: "
        f"using {'instance-specific' if using_instance_specific else 'generic'} credentials"
    )

    return KalshiAdapter(
        api_key_id=api_key_id,
        private_key_base64=private_key_base64,
        base_url=_instance_setting("KALSHI_BASE_URL", instance_name, "https://api.elections.kalshi.com"),
        dry_run=dry_run,
    )


def _normalized_binary_outcome(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if abs(numeric - 1.0) < 1e-9:
        return 1.0
    if abs(numeric - 0.0) < 1e-9:
        return 0.0
    return None


def _fetch_raw_market(adapter: Any, ticker: str) -> dict[str, Any] | None:
    try:
        path = f"/trade-api/v2/markets/{ticker}"
        headers = adapter._sign_request("GET", path)
        response = adapter._session.get(
            adapter._base_url + path,
            headers=headers,
            timeout=getattr(adapter, "_timeout", 10),
        )
        response.raise_for_status()
        return response.json().get("market", {})
    except Exception as e:
        logger.warning("Failed to fetch raw market %s for resolution lookup: %s", ticker, e)
        return None


def _fetch_market_resolution_outcome(adapter: Any, ticker: str) -> float | None:
    market = _fetch_raw_market(adapter, ticker)
    if not market:
        return None

    result = str(market.get("result") or "").strip().lower()
    if result == "yes":
        return 1.0
    if result == "no":
        return 0.0

    status = str(market.get("status") or "").strip().lower()
    settled_price = _normalized_binary_outcome(
        market.get("settlement_price_dollars", market.get("last_price_dollars"))
    )
    if status in {"settled", "resolved", "finalized"}:
        return settled_price
    return None


def _visible_market_scope_filter(visible_tickers: set[str], visible_market_ids: set[str]):
    filters = []
    if visible_market_ids:
        filters.append(TradingMarket.market_id.in_(visible_market_ids))
    if visible_tickers:
        filters.append(TradingMarket.ticker.in_(visible_tickers))
    if not filters:
        return False
    if len(filters) == 1:
        return filters[0]
    return or_(*filters)


def _load_resolved_visible_markets(
    session: Any,
    instance_name: str,
) -> list[tuple[TradingMarket, float]]:
    # For resolved markets, include ALL tickers that ever had fills (no cutoff)
    # so pre-cutoff traded markets like KXDHSFUND-26MAR26 still appear.
    visible_tickers, visible_market_ids = _display_visible_market_activity(session, instance_name)

    # Add ALL tickers with filled order snapshots (regardless of cutoff)
    all_traded_tickers = {
        ticker for (ticker,) in (
            session.query(KalshiOrderSnapshot.ticker)
            .filter(
                KalshiOrderSnapshot.instance_name == instance_name,
                KalshiOrderSnapshot.fill_count > 0,
            )
            .distinct()
            .all()
        )
        if ticker
    }
    visible_tickers = visible_tickers | all_traded_tickers

    visible_filter = _visible_market_scope_filter(visible_tickers, visible_market_ids)
    if visible_filter is False:
        return []

    # Also include markets whose lifecycle status is closed/inactive/settled/finalized
    closed_tickers = {
        ticker for (ticker,) in (
            _instance_query(session, TradingMarketLifecycle, instance_name)
            .filter(TradingMarketLifecycle.status.in_(["closed", "inactive", "settled", "finalized"]))
            .with_entities(TradingMarketLifecycle.ticker)
            .all()
        )
        if ticker
    }

    expired_or_closed = [TradingMarket.expiration < datetime.now(UTC)]
    if closed_tickers:
        expired_or_closed.append(TradingMarket.ticker.in_(closed_tickers))

    markets = (
        _instance_query(session, TradingMarket, instance_name)
        .filter(
            or_(*expired_or_closed),
            visible_filter,
        )
        .order_by(TradingMarket.expiration.desc())
        .all()
    )

    # Backfill: find traded tickers (from order snapshots) that are missing from
    # trading_markets entirely.  Create minimal rows so they show up.
    found_tickers = {m.ticker for m in markets}
    traded_but_missing = visible_tickers - found_tickers - {""}
    if traded_but_missing:
        existing_all = {
            t for (t,) in (
                _instance_query(session, TradingMarket, instance_name)
                .with_entities(TradingMarket.ticker)
                .filter(TradingMarket.ticker.in_(traded_but_missing))
                .all()
            )
        }
        truly_missing = traded_but_missing - existing_all
        if truly_missing:
            logger.info("Backfilling %d traded tickers missing from trading_markets: %s",
                        len(truly_missing), truly_missing)
            # Create minimal rows first (no API calls — avoids rate limits).
            # The outcome lookup loop below will enrich via Kalshi API one
            # market at a time with its existing rate-limit handling.
            for ticker in truly_missing:
                snap = (
                    session.query(KalshiOrderSnapshot)
                    .filter(
                        KalshiOrderSnapshot.instance_name == instance_name,
                        KalshiOrderSnapshot.ticker == ticker,
                    )
                    .order_by(KalshiOrderSnapshot.last_update_ts.desc())
                    .first()
                )
                if not snap:
                    continue

                row = TradingMarket(
                    instance_name=instance_name,
                    market_id=f"kalshi:{ticker}",
                    ticker=ticker,
                    event_ticker="-".join(ticker.split("-")[:2]) if "-" in ticker else ticker,
                    title=ticker,
                    category=None,
                    expiration=snap.created_ts,
                    last_price=None,
                    yes_bid=None,
                    yes_ask=None,
                    no_bid=None,
                    no_ask=None,
                    volume_24h=None,
                    updated_at=datetime.now(UTC),
                )
                session.add(row)
                markets.append(row)
            session.flush()

    logger.info(
        "Resolved markets query: found %d markets for %s (filter: %d tickers, %d market_ids, %d closed lifecycle)",
        len(markets), instance_name, len(visible_tickers), len(visible_market_ids), len(closed_tickers)
    )
    if not markets:
        return []

    adapter: Any | None = None
    looked_up_live = False
    updated_rows = False
    resolved: list[tuple[TradingMarket, float]] = []

    for market in markets:
        outcome = _normalized_binary_outcome(market.last_price)
        needs_enrichment = market.title == market.ticker  # placeholder title from backfill
        if (outcome is None or needs_enrichment) and market.ticker:
            if not looked_up_live:
                looked_up_live = True
                try:
                    adapter = _build_kalshi_adapter(instance_name)
                except Exception as e:
                    logger.warning("Failed to build Kalshi adapter for %s resolution lookup: %s", instance_name, e)
                    adapter = None
            if adapter is not None:
                if outcome is None:
                    outcome = _fetch_market_resolution_outcome(adapter, market.ticker)
                    if outcome is not None:
                        market.last_price = outcome
                        market.yes_bid = outcome
                        market.yes_ask = outcome
                        market.no_bid = 1.0 - outcome
                        market.no_ask = 1.0 - outcome
                        market.updated_at = datetime.now(UTC)
                        updated_rows = True
                # Enrich placeholder titles from Kalshi API
                if needs_enrichment:
                    try:
                        mkt_data = adapter.get_market(market.ticker)
                        if mkt_data:
                            market.title = mkt_data.get("title", market.title)
                            market.category = mkt_data.get("category")
                            market.event_ticker = mkt_data.get("event_ticker", market.event_ticker)
                            exp_str = mkt_data.get("close_time") or mkt_data.get("expiration_time")
                            if exp_str:
                                try:
                                    market.expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
                                except (ValueError, AttributeError):
                                    pass
                            if outcome is None:
                                result = mkt_data.get("result")
                                if result == "yes":
                                    outcome = 1.0
                                    market.last_price = 1.0
                                elif result == "no":
                                    outcome = 0.0
                                    market.last_price = 0.0
                            updated_rows = True
                    except Exception as e:
                        logger.debug("Could not enrich %s from Kalshi: %s", market.ticker, e)

        # Include the market even if we can't determine the outcome
        # Use -1 as a sentinel value for "unknown outcome"
        if outcome is None:
            outcome = -1.0  # Indicates resolution unknown
        resolved.append((market, outcome))

    if updated_rows:
        session.flush()

    return resolved


def _build_resolved_market_trade_state(
    session: Any,
    instance_name: str,
    tickers: list[str],
) -> dict[str, dict[str, InventoryPosition]]:
    orders = load_replayable_orders(session, BettingOrder, instance_name, tickers=tickers)
    positions: dict[str, InventoryPosition] = {}

    # Track which tickers have BettingOrder data
    tickers_with_orders: set[str] = set()

    for order in orders:
        ticker = getattr(order, "ticker", "")
        if not ticker:
            continue

        tickers_with_orders.add(ticker)
        pos = positions.setdefault(ticker, InventoryPosition())
        action, side, shares, _, _ = normalize_order(order)
        if shares <= EPSILON:
            continue

        status = str(getattr(order, "status", "") or "").upper()
        if status == "SETTLED":
            continue

        pos.apply_order(order, ticker=ticker)

    # For tickers with NO BettingOrder records, fall back to KalshiOrderSnapshot
    missing_tickers = [t for t in tickers if t and t not in tickers_with_orders]
    if missing_tickers:
        from kalshi_state import get_latest_order_snapshots
        snapshots = get_latest_order_snapshots(session, instance_name, tickers=missing_tickers)
        # Sort chronologically for correct replay
        snapshots.sort(key=lambda s: s.created_ts or s.last_update_ts or s.captured_at)
        for snap in snapshots:
            if not snap.ticker or snap.fill_count <= 0:
                continue
            if (snap.status or "").upper() == "SETTLED":
                continue
            pos = positions.setdefault(snap.ticker, InventoryPosition())
            action = (snap.action or "BUY").upper()
            side = (snap.side or "yes").lower()
            shares = float(snap.fill_count)
            price = float(snap.avg_fill_price or 0)
            # Correct inverted SELL fill prices
            if action == "SELL" and 0 < price < 1 and snap.limit_price is not None:
                limit = float(snap.limit_price)
                if limit > 0 and abs(price - limit) > abs((1 - price) - limit):
                    price = 1.0 - price
            fee = float(snap.fee_paid or 0)
            order_proxy = SimpleNamespace(
                action=action, side=side,
                filled_shares=shares, fill_price=price,
                fee_paid=fee, status=snap.status,
            )
            pos.apply_order(order_proxy, ticker=snap.ticker)

    state: dict[str, dict[str, Any]] = {}
    for ticker in tickers:
        state[ticker] = {
            "position": positions.get(ticker, InventoryPosition()),
        }
    return state


def _build_pred_by_signal(
    session, signal_ids: list[int], instance_name: str
) -> dict[int, BettingPrediction]:
    """Build a mapping of signal_id -> BettingPrediction."""
    pred_by_signal: dict[int, BettingPrediction] = {}
    if not signal_ids:
        return pred_by_signal
    signals = (
        _instance_query(session, BettingSignal, instance_name)
        .filter(BettingSignal.id.in_(signal_ids))
        .all()
    )
    pred_ids = [s.prediction_id for s in signals if s.prediction_id]
    preds: dict[int, BettingPrediction] = {}
    if pred_ids:
        for p in (
            _instance_query(session, BettingPrediction, instance_name)
            .filter(BettingPrediction.id.in_(pred_ids))
            .all()
        ):
            preds[p.id] = p
    for s in signals:
        if s.prediction_id and s.prediction_id in preds:
            pred_by_signal[s.id] = preds[s.prediction_id]
    return pred_by_signal


# ── GET /health ───────────────────────────────────────────────────


@app.get("/health")
def health(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """System health: DB status, last worker heartbeat, trading mode."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    now = datetime.now(UTC)
    db_ok = False
    last_heartbeat = None
    worker_status = "unknown"
    last_cycle_end = None
    effective_last_cycle_end = None
    last_sync_end = None
    poll_interval = _worker_poll_interval(resolved_instance)
    stale_threshold_sec = _worker_stale_threshold_sec(resolved_instance)

    try:
        with get_session(engine) as session:
            # 1. DB ping
            session.execute(text("SELECT 1"))
            db_ok = True

            # 2. Last worker heartbeat
            hb_row = (
                _heartbeat_query(session, resolved_instance)
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            if hb_row:
                last_heartbeat = hb_row.created_at.isoformat()
                age_sec = (now - hb_row.created_at.replace(tzinfo=UTC)).total_seconds()
                worker_status = "healthy" if age_sec < stale_threshold_sec else "stale"

            # 3. Last cycle_end for this instance
            ce_row = (
                _heartbeat_query(session, resolved_instance)
                .filter(
                    SystemLog.message == "cycle_end",
                )
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            if ce_row:
                last_cycle_end = ce_row.created_at.isoformat()

            # 3a. Check if cycle is currently running
            # (cycle_start more recent than cycle_end)
            cycle_running = False
            cs_row = (
                _heartbeat_query(session, resolved_instance)
                .filter(
                    SystemLog.message == "cycle_start",
                )
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            cycle_running = _is_cycle_running(cs_row, ce_row, now=now)

            # 4. Effective last cycle_end across ALL instances
            effective_last_cycle_end = last_cycle_end
            ce_rows = (
                session.query(SystemLog)
                .filter(
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component.in_(_heartbeat_components()),
                    SystemLog.message == "cycle_end",
                )
                .order_by(SystemLog.created_at.desc())
                .limit(10)
                .all()
            )
            seen: set[str] = set()
            latest_per: dict[str, Any] = {}
            for r in ce_rows:
                if r.instance_name not in seen:
                    seen.add(r.instance_name)
                    latest_per[r.instance_name] = r.created_at
            if latest_per:
                effective_last_cycle_end = max(latest_per.values()).isoformat()

            # 4a. Sync heartbeat status for the independent Kalshi sync service
            sync_start_row = (
                session.query(SystemLog)
                .filter(
                    SystemLog.instance_name == resolved_instance,
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "kalshi_sync",
                    SystemLog.message == "sync_start",
                )
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            sync_end_row = (
                session.query(SystemLog)
                .filter(
                    SystemLog.instance_name == resolved_instance,
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "kalshi_sync",
                )
                .filter(
                    or_(
                        SystemLog.message == "sync_end",
                        SystemLog.message.like("Cycle #% complete"),
                    )
                )
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            sync_running = False
            if sync_end_row:
                last_sync_end = sync_end_row.created_at.isoformat()
            if sync_start_row and sync_end_row:
                sync_running = sync_start_row.created_at > sync_end_row.created_at
            elif sync_start_row and not sync_end_row:
                sync_running = True
    except Exception:
        pass

    dry_run = _instance_bool_setting("LIVE_BETTING_DRY_RUN", resolved_instance, True)
    enabled = _instance_bool_setting("LIVE_BETTING_ENABLED", resolved_instance, False)
    worker_models = _instance_list_setting("WORKER_MODELS", resolved_instance)

    # Only show cycle timing when there's actual worker activity
    # If no heartbeat or last_cycle_end, return null to hide countdown
    show_cycle_timing = last_heartbeat is not None or last_cycle_end is not None

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "worker": worker_status,
        "last_heartbeat": last_heartbeat,
        "last_cycle_end": last_cycle_end if show_cycle_timing else None,
        "effective_last_cycle_end": effective_last_cycle_end if show_cycle_timing else None,
        "last_sync_end": last_sync_end,
        "poll_interval_sec": poll_interval,
        "sync_interval_sec": _sync_poll_interval(resolved_instance),
        "cycle_running": cycle_running if 'cycle_running' in locals() else False,
        "sync_running": sync_running if 'sync_running' in locals() else False,
        "mode": "dry_run" if dry_run else "live",
        "betting_enabled": enabled,
        "instance_name": resolved_instance,
        "worker_models": worker_models,
        "timestamp": now.isoformat(),
    }


# ── GET /trades ───────────────────────────────────────────────────


@app.get("/trades")
def get_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Recent trades (betting orders) with prediction context.

    Returns paginated results with total count.
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    try:
        with get_session(engine) as session:
            # Load ALL orders (no display cutoff) so trade history is complete.
            # Without pre-cutoff trades, position replay can show impossible
            # sequences (e.g. sell 12 after only buying 2).
            local_rows = (
                _instance_query(session, BettingOrder, resolved_instance)
                .order_by(BettingOrder.created_at.desc())
                .all()
            )
            deferred_rows = (
                _instance_query(session, BettingDeferredFlip, resolved_instance)
                .filter(
                    BettingDeferredFlip.status.in_((
                        "WAITING_SELL",
                        "WAITING_POSITION_SYNC",
                        "WAITING_RETRY",
                    )),
                )
                .order_by(BettingDeferredFlip.updated_at.desc(), BettingDeferredFlip.id.desc())
                .all()
            )
            latest_snapshots = list(get_latest_order_snapshots(session, resolved_instance))

            # Bulk-load signals, predictions, and market titles (avoid N+1)
            signal_ids = [r.signal_id for r in local_rows if r.signal_id]
            signal_ids.extend(
                flip.signal_id for flip in deferred_rows
                if flip.signal_id is not None
            )
            signals_by_id: dict[int, BettingSignal] = {}
            if signal_ids:
                for s in (
                    _instance_query(session, BettingSignal, resolved_instance)
                    .filter(BettingSignal.id.in_(signal_ids))
                    .all()
                ):
                    signals_by_id[s.id] = s

            pred_ids = [s.prediction_id for s in signals_by_id.values() if s.prediction_id]
            preds_by_id: dict[int, BettingPrediction] = {}
            if pred_ids:
                for p in (
                    _instance_query(session, BettingPrediction, resolved_instance)
                    .filter(BettingPrediction.id.in_(pred_ids))
                    .all()
                ):
                    preds_by_id[p.id] = p

            snapshot_tickers = {snap.ticker for snap in latest_snapshots if snap.ticker}
            trade_market_ids = list(
                {f"kalshi:{r.ticker}" for r in local_rows if r.ticker}
                | {f"kalshi:{ticker}" for ticker in snapshot_tickers}
                | {flip.market_id for flip in deferred_rows if flip.market_id}
                | {f"kalshi:{flip.ticker}" for flip in deferred_rows if flip.ticker}
            )
            market_titles: dict[str, str] = {}
            if trade_market_ids:
                for m in (
                    _instance_query(session, TradingMarket, resolved_instance)
                    .filter(TradingMarket.market_id.in_(trade_market_ids))
                    .all()
                ):
                    market_titles[m.market_id] = m.title

            fallback_signals_by_market: dict[str, list[tuple[BettingSignal, BettingPrediction, dict[str, Any]]]] = defaultdict(list)
            if trade_market_ids:
                fallback_signal_rows = (
                    session.query(BettingSignal, BettingPrediction)
                    .join(BettingPrediction, BettingPrediction.id == BettingSignal.prediction_id)
                    .filter(
                        BettingSignal.instance_name == resolved_instance,
                        BettingPrediction.market_id.in_(trade_market_ids),
                        BettingSignal.created_at >= DISPLAY_CUTOFF_UTC,
                    )
                    .order_by(BettingSignal.created_at.desc(), BettingSignal.id.desc())
                    .all()
                )
                for sig, pred in fallback_signal_rows:
                    metadata: dict[str, Any] = {}
                    if sig.metadata_json:
                        try:
                            loaded = json.loads(sig.metadata_json)
                            if isinstance(loaded, dict):
                                metadata = loaded
                        except (json.JSONDecodeError, TypeError):
                            metadata = {}
                    fallback_signals_by_market[pred.market_id].append((sig, pred, metadata))

            prediction_market_ids = list({
                p.market_id for p in preds_by_id.values() if p.market_id
            } | set(trade_market_ids))
            model_runs_by_market: dict[str, list[ModelRun]] = defaultdict(list)
            if prediction_market_ids:
                for run in (
                    _instance_query(session, ModelRun, resolved_instance)
                    .filter(ModelRun.market_id.in_(prediction_market_ids))
                    .order_by(ModelRun.timestamp.desc())
                    .all()
                ):
                    model_runs_by_market[run.market_id].append(run)

            local_by_exchange_order_id: dict[str, BettingOrder] = {}
            local_by_internal_order_id: dict[str, BettingOrder] = {}
            active_buy_signals: set[int] = set()
            for row in local_rows:
                if row.exchange_order_id:
                    local_by_exchange_order_id[row.exchange_order_id] = row
                local_by_internal_order_id[row.order_id] = row
                if (
                    row.signal_id is not None
                    and (row.action or "").upper() == "BUY"
                    and (row.status or "").upper() in {"FILLED", "DRY_RUN", "PENDING", "PARTIALLY_FILLED"}
                ):
                    active_buy_signals.add(row.signal_id)

            def find_nearest_signal_prediction(row: BettingOrder) -> tuple[BettingSignal | None, BettingPrediction | None]:
                if not row.ticker:
                    return None, None
                market_id = f"kalshi:{row.ticker}"
                candidates = fallback_signals_by_market.get(market_id, [])
                if not candidates:
                    return None, None

                trade_ts = row.created_at
                match_window = timedelta(minutes=15)
                best_match: tuple[int, float, int, BettingSignal, BettingPrediction] | None = None
                action = (row.action or "").upper()
                side = (row.side or "").lower()

                for sig, pred, metadata in candidates:
                    delta_seconds = abs((sig.created_at - trade_ts).total_seconds())
                    if delta_seconds > match_window.total_seconds():
                        continue

                    score = 0
                    if action == "SELL":
                        if side and sig.side and side != sig.side.lower():
                            score += 5
                        raw_portion = metadata.get("sell_portion")
                    elif action == "BUY":
                        if side and sig.side and side == sig.side.lower():
                            score += 5
                        raw_portion = metadata.get("buy_portion")
                    else:
                        raw_portion = None

                    if raw_portion is not None:
                        try:
                            portion_count = max(0, round(abs(float(raw_portion)) * 100))
                            if abs(portion_count - int(row.count or 0)) <= 1:
                                score += 20
                        except (TypeError, ValueError):
                            pass

                    candidate = (score, -delta_seconds, sig.id, sig, pred)
                    if best_match is None or candidate > best_match:
                        best_match = candidate

                if best_match is None:
                    return None, None
                return best_match[3], best_match[4]

            def build_prediction(row: BettingOrder | None) -> dict[str, Any] | None:
                if row is None:
                    return None
                sig = signals_by_id.get(row.signal_id) if row.signal_id else None
                pred = preds_by_id.get(sig.prediction_id) if sig and sig.prediction_id else None
                if pred is None:
                    sig, pred = find_nearest_signal_prediction(row)
                if pred is None:
                    return None

                reasoning = None
                sources: list[dict[str, str]] = []
                trade_ts = row.created_at
                match_window = timedelta(minutes=15)
                market_runs = model_runs_by_market.get(pred.market_id, [])
                matched_meta = None
                matched_delta = None

                for run in market_runs:
                    if run.model_name != pred.source:
                        continue
                    delta_seconds = abs((run.timestamp - trade_ts).total_seconds())
                    if delta_seconds > match_window.total_seconds():
                        continue
                    if not run.metadata_json:
                        continue
                    try:
                        meta = json.loads(run.metadata_json)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    p_yes = meta.get("p_yes")
                    if p_yes is None or abs(float(p_yes) - float(pred.p_yes)) > 0.0005:
                        continue
                    if matched_delta is None or delta_seconds < matched_delta:
                        matched_meta = meta
                        matched_delta = delta_seconds

                if matched_meta:
                    reasoning = matched_meta.get("reasoning")
                    sources = matched_meta.get("sources", [])

                return {
                    "p_yes": pred.p_yes,
                    "yes_ask": pred.yes_ask,
                    "no_ask": pred.no_ask,
                    "source": pred.source,
                    "market_id": pred.market_id,
                    "reasoning": reasoning,
                    "sources": sources,
                }

            merged_results: list[dict[str, Any]] = []
            seen_keys: set[str] = set()

            def _correct_sell_fill_price(action: str, fill_price: float | None, limit_price: float | None) -> float | None:
                """Correct inverted fill prices for SELL orders.

                Kalshi reports fill_cost as the exposure cost for SELLs,
                which is (1 - actual_price).  Detect and fix the inversion
                by comparing against the limit price when available.
                """
                if fill_price is None or fill_price <= 0:
                    return fill_price
                if action.upper() != "SELL":
                    return fill_price
                # If limit is available and fill_price is suspiciously far from it,
                # the stored value is the complement.  Also handle the general case
                # where no limit is available — assume inversion if price < 0.5.
                if limit_price is not None and limit_price > 0:
                    if abs(fill_price - limit_price) > abs((1 - fill_price) - limit_price):
                        return 1.0 - fill_price
                return fill_price

            for idx, snap in enumerate(latest_snapshots, start=1):
                local_row = local_by_exchange_order_id.get(snap.order_id) or local_by_internal_order_id.get(snap.order_id)
                ticker = local_row.ticker if local_row else snap.ticker
                if not ticker:
                    continue
                prediction = build_prediction(local_row)
                count = int(round(snap.initial_count)) if snap.initial_count > 0 else (local_row.count if local_row else 0)
                action = (snap.action or (local_row.action if local_row else "BUY")).upper()
                raw_fill_price = snap.avg_fill_price if snap.fill_count > 0 else None
                corrected_fill_price = _correct_sell_fill_price(action, raw_fill_price, snap.limit_price)
                display_price = corrected_fill_price if corrected_fill_price is not None else snap.limit_price
                if display_price is None and local_row is not None:
                    display_price = local_row.fill_price if (local_row.fill_price or 0) > 0 else (local_row.price_cents / 100)
                created_at = snap.created_ts or snap.last_update_ts or snap.captured_at or (local_row.created_at if local_row else None)
                if created_at is None:
                    continue
                merge_key = f"exchange:{snap.order_id}"
                seen_keys.add(merge_key)
                merged_results.append({
                    "id": local_row.id if local_row else -idx,
                    "order_id": local_row.order_id if local_row else snap.order_id,
                    "ticker": ticker,
                    "action": action,
                    "side": (snap.side or (local_row.side if local_row else "YES")).upper(),
                    "count": count,
                    "price_cents": int(round((display_price or 0.0) * 100)),
                    "status": snap.status,
                    "filled_shares": snap.fill_count,
                    "fill_price": corrected_fill_price,
                    "fee_paid": snap.fee_paid,
                    "exchange_order_id": snap.order_id,
                    "dry_run": local_row.dry_run if local_row else False,
                    "created_at": created_at.isoformat(),
                    "prediction": prediction,
                    "market_title": market_titles.get(f"kalshi:{ticker}"),
                })

            local_only_rows = sorted(local_rows, key=lambda row: row.created_at, reverse=True)
            for row in local_only_rows:
                merge_key = f"exchange:{row.exchange_order_id}" if row.exchange_order_id else f"local:{row.order_id}"
                if merge_key in seen_keys:
                    continue
                prediction = build_prediction(row)
                if isinstance(prediction, dict):
                    source = str(prediction.get("source") or "").lower()
                    if source.startswith("kalshi:"):
                        continue
                market_title = market_titles.get(f"kalshi:{row.ticker}")
                local_action = (row.action or "BUY").upper()
                local_fill = _correct_sell_fill_price(
                    local_action,
                    row.fill_price,
                    row.price_cents / 100.0 if row.price_cents else None,
                )
                local_price_cents = int(round((local_fill or row.price_cents / 100.0 if row.price_cents else 0) * 100))
                merged_results.append({
                    "id": row.id,
                    "order_id": row.order_id,
                    "ticker": row.ticker,
                    "action": local_action,
                    "side": row.side,
                    "count": row.count,
                    "price_cents": local_price_cents,
                    "status": row.status,
                    "filled_shares": row.filled_shares,
                    "fill_price": local_fill,
                    "fee_paid": row.fee_paid,
                    "exchange_order_id": row.exchange_order_id,
                    "dry_run": row.dry_run,
                    "created_at": row.created_at.isoformat(),
                    "prediction": prediction,
                    "market_title": market_title,
                })

            for flip in deferred_rows:
                if flip.buy_count <= 0 or not flip.ticker:
                    continue
                if flip.signal_id in active_buy_signals:
                    continue

                synthetic_created_at = flip.created_at
                sell_row = local_by_internal_order_id.get(flip.sell_order_id)
                if sell_row is not None and sell_row.created_at is not None and synthetic_created_at <= sell_row.created_at:
                    synthetic_created_at = sell_row.created_at + timedelta(microseconds=1)

                prediction = build_prediction(
                    SimpleNamespace(
                        signal_id=flip.signal_id,
                        created_at=synthetic_created_at,
                        ticker=flip.ticker,
                        count=flip.buy_count,
                        action="BUY",
                        side=flip.buy_side,
                    )
                )
                merged_results.append({
                    "id": -1000000 - flip.id,
                    "order_id": f"deferred-flip-{flip.id}",
                    "ticker": flip.ticker,
                    "action": "BUY",
                    "side": flip.buy_side.upper(),
                    "count": flip.buy_count,
                    "price_cents": flip.buy_price_cents,
                    "status": "PENDING",
                    "filled_shares": 0,
                    "fill_price": None,
                    "fee_paid": 0,
                    "exchange_order_id": None,
                    "dry_run": False,
                    "created_at": synthetic_created_at.isoformat(),
                    "prediction": prediction,
                    "market_title": market_titles.get(flip.market_id) or market_titles.get(f"kalshi:{flip.ticker}"),
                    "synthetic_kind": "DEFERRED_FLIP",
                    "deferred_status": flip.status,
                    "pending_reason": _deferred_flip_pending_reason(flip.status, flip.last_error),
                    "related_order_id": flip.sell_order_id,
                })

            merged_results.sort(key=lambda item: item["created_at"], reverse=True)
            total_count = len(merged_results)
            results = merged_results[offset:offset + limit]

            return {
                "trades": results,
                "total": total_count,
                "has_more": (offset + limit) < total_count,
            }
    except Exception as e:
        logger.warning("GET /trades DB error: %s", e)
        return {"trades": [], "total": 0, "has_more": False}


# ── DELETE /trades/dry-runs ───────────────────────────────────────


@app.delete("/trades/dry-runs")
def delete_dry_run_trades(
    instance_name: str | None = Query(None),
    cleanup_positions: bool = Query(True, description="Also delete positions created by dry run trades"),
) -> dict[str, Any]:
    """Delete all dry run trades (orders with dry_run=true).

    Also optionally deletes positions created by those trades to prevent phantom positions.

    Returns count of deleted trades and positions.
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    try:
        with get_session(engine) as session:
            # Get tickers that have dry run trades
            dry_run_orders = (
                _instance_query(session, BettingOrder, resolved_instance)
                .filter(BettingOrder.dry_run == True)
                .all()
            )

            if not dry_run_orders:
                return {
                    "deleted_trades": 0,
                    "deleted_positions": 0,
                    "message": "No dry run trades found"
                }

            dry_run_tickers = {order.ticker for order in dry_run_orders}
            dry_run_count = len(dry_run_orders)

            # Delete positions first (if requested)
            deleted_positions = 0
            if cleanup_positions and dry_run_tickers:
                # Find positions for these tickers
                dry_run_market_ids = {f"kalshi:{ticker}" for ticker in dry_run_tickers}
                pos_delete_query = (
                    _instance_query(session, TradingPosition, resolved_instance)
                    .filter(TradingPosition.market_id.in_(dry_run_market_ids))
                )
                deleted_positions = pos_delete_query.delete(synchronize_session=False)

            # Delete dry run trades
            delete_query = (
                _instance_query(session, BettingOrder, resolved_instance)
                .filter(BettingOrder.dry_run == True)
            )
            deleted_trades = delete_query.delete(synchronize_session=False)
            session.commit()

            return {
                "deleted_trades": deleted_trades,
                "deleted_positions": deleted_positions,
                "tickers_affected": list(dry_run_tickers),
                "message": f"Successfully deleted {deleted_trades} dry run trades and {deleted_positions} positions"
            }
    except Exception as e:
        logger.error("DELETE /trades/dry-runs error: %s", e)
        return {
            "deleted_trades": 0,
            "deleted_positions": 0,
            "error": str(e)
        }


# ── DELETE /positions/orphaned ───────────────────────────────────


@app.delete("/positions/orphaned")
def delete_orphaned_positions(
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Delete positions that have no corresponding filled orders.

    This cleans up phantom positions left behind when dry run trades are deleted.

    Returns count of deleted positions.
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    try:
        with get_session(engine) as session:
            # Get all positions
            positions = _instance_query(session, TradingPosition, resolved_instance).all()

            # Get all tickers with filled orders
            filled_tickers = set()
            filled_orders = (
                _instance_query(session, BettingOrder, resolved_instance)
                .filter(BettingOrder.status == "FILLED")
                .all()
            )
            for order in filled_orders:
                filled_tickers.add(f"kalshi:{order.ticker}")

            # Find orphaned positions (positions without any filled orders)
            orphaned_ids = []
            orphaned_tickers = []
            for pos in positions:
                if pos.market_id not in filled_tickers:
                    orphaned_ids.append(pos.id)
                    orphaned_tickers.append(pos.market_id.replace("kalshi:", ""))

            if not orphaned_ids:
                return {
                    "deleted": 0,
                    "message": "No orphaned positions found"
                }

            # Delete orphaned positions
            deleted = (
                _instance_query(session, TradingPosition, resolved_instance)
                .filter(TradingPosition.id.in_(orphaned_ids))
                .delete(synchronize_session=False)
            )
            session.commit()

            return {
                "deleted": deleted,
                "tickers_cleaned": orphaned_tickers,
                "message": f"Successfully deleted {deleted} orphaned positions"
            }
    except Exception as e:
        logger.error("DELETE /positions/orphaned error: %s", e)
        return {
            "deleted": 0,
            "error": str(e)
        }


# ── GET /markets ──────────────────────────────────────────────────


@app.get("/markets")
def get_markets(
    limit: int = Query(50, ge=1, le=200),
    instance_name: str | None = Query(None),
) -> list[dict[str, Any]]:
    """Markets currently being tracked, with latest model prediction."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    try:
      with get_session(engine) as session:
        def _build_model_prediction(run: ModelRun) -> dict[str, Any]:
            p_yes = None
            reasoning = None
            sources: list[dict[str, Any]] = []
            if run.metadata_json:
                try:
                    meta = json.loads(run.metadata_json)
                    p_yes = meta.get("p_yes")
                    reasoning = meta.get("reasoning")
                    sources = meta.get("sources", [])
                except (json.JSONDecodeError, TypeError):
                    pass
            return {
                "model_name": run.model_name,
                "decision": run.decision,
                "confidence": run.confidence,
                "p_yes": p_yes,
                "timestamp": run.timestamp.isoformat(),
                "reasoning": reasoning,
                "sources": sources,
            }

        def _is_non_skip_prediction(pred: dict[str, Any]) -> bool:
            decision = str(pred.get("decision") or "").upper()
            return pred.get("p_yes") is not None and decision not in {"SKIP", "HOLD", "CYCLE_SKIPPED"}

        visible_tickers, visible_market_ids = _display_visible_market_activity(session, resolved_instance)
        rows = (
            _instance_query(session, TradingMarket, resolved_instance)
            .order_by(TradingMarket.updated_at.desc())
            .all()
        )
        lifecycle_rows = (
            _instance_query(session, TradingMarketLifecycle, resolved_instance)
            .all()
        )
        lifecycle_by_market_id = {row.market_id: row for row in lifecycle_rows}
        # Build set of market_ids that have an active position
        # (these are shown regardless of spread)
        kalshi_position_views = build_position_views(session, resolved_instance)
        active_positions: set[str] = {p.market_id for p in kalshi_position_views}
        latest_order_snaps = get_latest_order_snapshots(
            session,
            resolved_instance,
            tickers=(row.ticker for row in rows),
        )
        pending_orders_by_ticker = build_pending_orders_by_ticker(
            session,
            resolved_instance,
            min_created_ts=DISPLAY_CUTOFF_UTC,
            latest_orders=latest_order_snaps,
        )
        latest_order_time_by_ticker, kalshi_order_count_by_ticker = build_latest_order_activity_by_ticker(
            session,
            resolved_instance,
            min_created_ts=DISPLAY_CUTOFF_UTC,
            latest_orders=latest_order_snaps,
        )

        # Bulk-load all recent model runs for these markets (avoid N+1)
        market_ids_for_runs = [r.market_id for r in rows]
        all_recent_runs = (
            _instance_query(session, ModelRun, resolved_instance)
            .filter(
                ModelRun.market_id.in_(market_ids_for_runs),
                ModelRun.timestamp >= DISPLAY_CUTOFF_UTC,
            )
            .order_by(ModelRun.timestamp.desc())
            .all()
        )
        # Group runs by market_id (already desc order)
        runs_by_market: dict[str, list] = defaultdict(list)
        for run in all_recent_runs:
            runs_by_market[run.market_id].append(run)

        results = []
        for row in rows:
            if row.ticker not in visible_tickers and row.market_id not in visible_market_ids:
                continue

            market_runs = runs_by_market.get(row.market_id, [])
            latest_run = market_runs[0] if market_runs else None

            model_predictions = []
            aggregated_p_yes = None
            model_prediction = None
            latest_non_skip_prediction = None

            for run in market_runs:
                pred = _build_model_prediction(run)
                if _is_non_skip_prediction(pred):
                    latest_non_skip_prediction = pred
                    break

            if latest_run:
                # Filter to same cycle (within 10min of latest) using the pre-loaded runs
                cycle_start = latest_run.timestamp - timedelta(seconds=600)
                cycle_runs = [
                    r for r in market_runs
                    if cycle_start <= r.timestamp <= latest_run.timestamp
                ]
                cycle_runs.sort(key=lambda r: r.timestamp)
                seen_models: set[str] = set()
                for run in cycle_runs:
                    if run.model_name in seen_models:
                        continue
                    seen_models.add(run.model_name)
                    pred = _build_model_prediction(run)
                    model_predictions.append(pred)
                    if aggregated_p_yes is None:
                        aggregated_p_yes = pred["p_yes"]

                model_prediction = model_predictions[-1] if model_predictions else None
            if latest_non_skip_prediction is not None:
                aggregated_p_yes = latest_non_skip_prediction["p_yes"]
                if model_prediction is None or model_prediction.get("p_yes") is None:
                    model_prediction = latest_non_skip_prediction

            results.append({
                "id": row.id,
                "market_id": row.market_id,
                "ticker": row.ticker,
                "event_ticker": row.event_ticker,
                "title": row.title,
                "category": row.category,
                "expiration": row.expiration.isoformat() if row.expiration else None,
                "last_price": row.last_price,
                "yes_bid": row.yes_bid,
                "yes_ask": row.yes_ask,
                "no_bid": row.no_bid,
                "no_ask": row.no_ask,
                "volume_24h": row.volume_24h,
                "updated_at": row.updated_at.isoformat(),
                "market_status": lifecycle_by_market_id.get(row.market_id).status if lifecycle_by_market_id.get(row.market_id) else None,
                "market_result": lifecycle_by_market_id.get(row.market_id).result if lifecycle_by_market_id.get(row.market_id) else None,
                "model_prediction": model_prediction,
                "model_predictions": model_predictions,
                "aggregated_p_yes": aggregated_p_yes,
                "latest_non_skip_p_yes": latest_non_skip_prediction["p_yes"] if latest_non_skip_prediction else None,
                "pending_orders": pending_orders_by_ticker.get(row.ticker, []),
                "latest_order_time": latest_order_time_by_ticker.get(row.ticker),
                "kalshi_order_count": kalshi_order_count_by_ticker.get(row.ticker, 0),
            })
        return results[:limit]
    except Exception as e:
        logger.warning("GET /markets DB error: %s", e)
        return []


# ── GET /positions ────────────────────────────────────────────────


@app.get("/positions")
def get_positions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, description="Search by market title or ticker"),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Current trading positions with market context (paginated)."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        visible_tickers, visible_market_ids = _display_visible_market_activity(session, resolved_instance)
        kalshi_positions = [
            row for row in build_position_views(session, resolved_instance)
            if row.ticker in visible_tickers
        ]

        if kalshi_positions:
            kalshi_positions.sort(key=lambda row: row.updated_at, reverse=True)
            total = len(kalshi_positions)
            rows = kalshi_positions[offset:offset + limit]
        else:
            query = (
                _instance_query(session, TradingPosition, resolved_instance)
                .filter(TradingPosition.market_id.in_(visible_market_ids) if visible_market_ids else False)
                .order_by(TradingPosition.updated_at.desc())
            )
            total = query.count()
            rows = query.offset(offset).limit(limit).all()

        # Bulk-load market info for all positions (avoid N+1)
        pos_market_ids = [r.market_id for r in rows]
        pos_markets: dict[str, TradingMarket] = {}
        if pos_market_ids:
            for m in (
                _instance_query(session, TradingMarket, resolved_instance)
                .filter(TradingMarket.market_id.in_(pos_market_ids))
                .all()
            ):
                pos_markets[m.market_id] = m

        tickers = [
            (pos_markets.get(row.market_id).ticker if pos_markets.get(row.market_id) else getattr(row, "ticker", None))
            for row in rows
        ]
        latest_order_snaps = get_latest_order_snapshots(
            session,
            resolved_instance,
            tickers=tickers,
        )
        latest_order_time_by_ticker, kalshi_order_count_by_ticker = build_latest_order_activity_by_ticker(
            session,
            resolved_instance,
            min_created_ts=DISPLAY_CUTOFF_UTC,
            latest_orders=latest_order_snaps,
        )
        pending_orders_by_ticker = build_pending_orders_by_ticker(
            session,
            resolved_instance,
            min_created_ts=DISPLAY_CUTOFF_UTC,
            latest_orders=latest_order_snaps,
        )

        results = []
        for row in rows:
            mkt = pos_markets.get(row.market_id)
            market_title = mkt.title if mkt else None
            event_ticker = mkt.event_ticker if mkt else None
            ticker = mkt.ticker if mkt else None
            resolved_ticker = ticker or getattr(row, "ticker", None)

            # Apply search filter after join (search on title or ticker)
            if search:
                needle = search.lower()
                if not (
                    (market_title and needle in market_title.lower())
                    or (resolved_ticker and needle in resolved_ticker.lower())
                    or (event_ticker and needle in event_ticker.lower())
                ):
                    total -= 1
                    continue

            results.append({
                "id": getattr(row, "id", 0),
                "market_id": row.market_id,
                "ticker": resolved_ticker,
                "event_ticker": event_ticker,
                "market_title": market_title,
                "contract": row.contract,
                "quantity": row.quantity,
                "avg_price": row.avg_price,
                "realized_pnl": row.realized_pnl,
                "market_exposure": getattr(row, "market_exposure", None),
                "total_cost": getattr(row, "total_cost", None),
                "fees_paid": getattr(row, "fees_paid", None),
                "pending_orders": pending_orders_by_ticker.get(resolved_ticker, []),
                "latest_order_time": latest_order_time_by_ticker.get(resolved_ticker),
                "kalshi_order_count": kalshi_order_count_by_ticker.get(resolved_ticker, 0),
                "updated_at": row.updated_at.isoformat(),
            })
        return {
            "positions": results,
            "total": total,
            "has_more": offset + limit < total,
        }


# ── GET /pnl ─────────────────────────────────────────────────────


# Simple in-memory cache for PnL endpoint
_pnl_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_PNL_CACHE_TTL = 10  # seconds


def _build_snapshot_backed_pnl_series(
    balance_snapshots: list[KalshiBalanceSnapshot],
    realized_events: list[tuple[datetime, float]],
    target_net_pnl: float,
) -> list[dict[str, Any]]:
    """Build a Kalshi-backed net-P&L series from recorded balance snapshots.

    The portfolio chart should track Kalshi account truth rather than local
    order replay. We anchor the series so the final snapshot matches the same
    net-P&L shown in the header cards, while the path between points follows
    recorded Kalshi balance/portfolio snapshots.
    """
    if not balance_snapshots:
        return []

    latest = balance_snapshots[-1]
    latest_equity = float(latest.balance or 0.0) + float(latest.portfolio_value or 0.0)
    baseline_equity = latest_equity - float(target_net_pnl or 0.0)

    realized_events = sorted(realized_events, key=lambda item: item[0])
    realized_idx = 0
    realized_so_far = 0.0
    series: list[dict[str, Any]] = []

    for snap in balance_snapshots:
        while realized_idx < len(realized_events) and realized_events[realized_idx][0] <= snap.snapshot_ts:
            realized_so_far = realized_events[realized_idx][1]
            realized_idx += 1

        open_value = float(snap.portfolio_value or 0.0)
        pnl = (float(snap.balance or 0.0) + open_value) - baseline_equity
        cash_spent = open_value + realized_so_far - pnl

        series.append({
            "timestamp": snap.snapshot_ts.isoformat(),
            "pnl": round(pnl, 4),
            "cash_pnl": round(realized_so_far, 4),
            "open_value": round(open_value, 4),
            "cash_spent": round(cash_spent, 4),
            "trade_cost": 0.0,
            "trade_fee": 0.0,
            "ticker": "",
            "side": "",
            "action": "SNAPSHOT",
        })

    return series


@app.get("/pnl")
def get_pnl(
    days: int = Query(30, ge=1, le=365),
    market_id: str | None = Query(None, description="Filter by market_id"),
    model: str | None = Query(None, description="Filter by model/source"),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """P&L data: cumulative P&L over time from filled orders.

    Returns combined, realized, and unrealized series plus trade markers.
    Supports filtering by market_id and model/source.
    """
    resolved_instance = _instance_name(instance_name)

    # Check cache (only for default params - no filters)
    cache_key = f"{resolved_instance}:{days}:{market_id or ''}:{model or ''}"
    if not market_id and not model and cache_key in _pnl_cache:
        cached_time, cached_data = _pnl_cache[cache_key]
        if time.time() - cached_time < _PNL_CACHE_TTL:
            return cached_data

    engine = get_db()
    with get_session(engine) as session:
        # Limit orders to recent history based on days parameter (performance optimization)
        cutoff_date = max(datetime.now(UTC) - timedelta(days=days), DISPLAY_CUTOFF_UTC)
        query = (
            _instance_query(session, BettingOrder, resolved_instance)
            .filter(
                BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
                BettingOrder.created_at >= cutoff_date,
            )
            .order_by(BettingOrder.created_at.asc())
            .limit(2000)  # Hard limit for performance
        )
        rows = query.all()

        # Build map: signal_id -> prediction (for historical prices at trade time)
        signal_ids = [r.signal_id for r in rows if r.signal_id]
        pred_by_signal = _build_pred_by_signal(session, signal_ids, resolved_instance)

        # Apply filters: narrow rows based on market_id and model/source
        if market_id or model:
            filtered_rows = []
            for row in rows:
                pred = pred_by_signal.get(row.signal_id)
                if market_id and pred:
                    if pred.market_id != market_id and f"kalshi:{row.ticker}" != market_id:
                        continue
                elif market_id:
                    if f"kalshi:{row.ticker}" != market_id:
                        continue
                if model and pred:
                    if pred.source != model:
                        continue
                elif model:
                    continue  # No prediction means we can't verify the model
                filtered_rows.append(row)
            rows = filtered_rows

        # Current prices + titles: single TradingMarket query (was 2 separate queries)
        current_prices: dict[str, dict[str, float | None]] = {}
        market_titles: dict[str, str] = {}
        for mkt in _instance_query(session, TradingMarket, resolved_instance).all():
            current_prices[mkt.ticker] = {
                "yes_ask": mkt.yes_ask,
                "no_ask": mkt.no_ask,
                "yes_bid": mkt.yes_bid,
                "no_bid": mkt.no_bid,
            }
            market_titles[mkt.ticker] = mkt.title or ""

        # Pre-load price snapshots only for tickers in the order set + time window
        order_tickers = list({r.ticker for r in rows if r.ticker})
        snap_query = _instance_query(session, MarketPriceSnapshot, resolved_instance)
        if order_tickers:
            snap_query = snap_query.filter(MarketPriceSnapshot.ticker.in_(order_tickers))
        if rows:
            earliest_order = min(r.created_at for r in rows)
            snap_query = snap_query.filter(MarketPriceSnapshot.timestamp >= earliest_order - timedelta(hours=1))
        all_snapshots = snap_query.order_by(MarketPriceSnapshot.timestamp.asc()).all()

        # Group snapshots by ticker, sorted by time — for bisect lookup
        snapshots_by_ticker: dict[str, list[tuple[datetime, float, float]]] = defaultdict(list)
        for snap in all_snapshots:
            mid = snap.market_id or ""
            tk = snap.ticker or (mid[len("kalshi:"):] if mid.startswith("kalshi:") else mid) or "unknown"
            snapshots_by_ticker[tk].append((snap.timestamp, snap.yes_ask, snap.no_ask))

        def _price_at(ticker: str, ts: datetime) -> dict[str, float] | None:
            """Find the closest price snapshot for ticker at or before ts."""
            snaps = snapshots_by_ticker.get(ticker)
            if not snaps:
                return None
            # Binary search for the last snapshot at or before ts
            lo, hi = 0, len(snaps) - 1
            result_idx = -1
            while lo <= hi:
                mid = (lo + hi) // 2
                if snaps[mid][0] <= ts:
                    result_idx = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            if result_idx < 0:
                return None
            _, ya, na = snaps[result_idx]
            return {"yes_ask": ya, "no_ask": na}

        ticker_positions: dict[str, InventoryPosition] = {}
        pnl_series: list[dict[str, Any]] = []
        trade_markers: list[dict[str, Any]] = []
        total_trades = 0
        total_volume = 0.0
        cumulative_realized = 0.0
        realized_events: list[tuple[datetime, float]] = []

        for row in rows:
            action, side, shares, price, fee_paid = normalize_order(row)
            cost = price * shares
            total_trades += 1
            total_volume += cost

            ticker = row.ticker
            pos = ticker_positions.setdefault(ticker, InventoryPosition())

            pnl_impact = pos.apply_order(row, ticker=ticker)
            cumulative_realized += pnl_impact
            realized_events.append((row.created_at, cumulative_realized))

            # Compute open_value and cash_spent at this trade's timestamp
            # using actual recorded price snapshots (not predictions).
            trade_ts = row.created_at
            point_open_value = 0.0
            point_cash_spent = 0.0
            for t, p in ticker_positions.items():
                pos_side, pos_qty, avg_price = p.current_position()
                if pos_side is None or pos_qty <= 0:
                    continue
                prices = _price_at(t, trade_ts)
                bid: float | None = None
                if prices:
                    if pos_side == "yes":
                        bid = 1.0 - prices["no_ask"] if prices.get("no_ask") is not None else None
                    else:
                        bid = 1.0 - prices["yes_ask"] if prices.get("yes_ask") is not None else None
                elif price > 0:
                    # Last resort: use the trade's own price
                    bid = price if side == pos_side else (1.0 - price)
                point_open_value += (bid if bid is not None else 0.0) * pos_qty
                point_cash_spent += avg_price * pos_qty

            cumulative_pnl = cumulative_realized + point_open_value - point_cash_spent

            ts = row.created_at.isoformat()

            pnl_series.append({
                "timestamp": ts,
                "pnl": round(cumulative_pnl, 4),
                "cash_pnl": round(cumulative_realized, 4),
                "open_value": round(point_open_value, 4),
                "cash_spent": round(point_cash_spent, 4),
                "trade_cost": round(cost, 4),
                "trade_fee": round(fee_paid, 4),
                "ticker": row.ticker,
                "side": row.side,
                "action": action,
            })

            trade_markers.append({
                "timestamp": ts,
                "ticker": row.ticker,
                "side": side,
                "action": action,
                "count": shares,
                "price_cents": round(price * 100),
                "pnl_impact": round(pnl_impact, 4),
            })

        # Final point: use LIVE prices (not historical) so the chart endpoint
        # reflects current portfolio value — same as the header.
        total_cash_pnl = cumulative_realized
        total_open_value = 0.0
        total_cash_spent = 0.0
        for t, p in ticker_positions.items():
            pos_side, pos_qty, avg_price = p.current_position()
            if pos_side is None or pos_qty <= 0:
                continue
            prices = current_prices.get(t)
            bid: float | None = None
            if prices:
                if pos_side == "yes":
                    bid = prices.get("yes_bid") or (1.0 - prices["no_ask"] if prices.get("no_ask") is not None else None)
                else:
                    bid = prices.get("no_bid") or (1.0 - prices["yes_ask"] if prices.get("yes_ask") is not None else None)
            total_open_value += (bid if bid is not None else 0.0) * pos_qty
            total_cash_spent += avg_price * pos_qty
        total_pnl = total_cash_pnl + total_open_value - total_cash_spent

        # Correct the final series point to match live P&L
        if pnl_series:
            pnl_series[-1]["pnl"] = round(total_pnl, 4)
            pnl_series[-1]["cash_pnl"] = round(total_cash_pnl, 4)
            pnl_series[-1]["open_value"] = round(total_open_value, 4)
            pnl_series[-1]["cash_spent"] = round(total_cash_spent, 4)

        result = {
            "series": pnl_series,
            "trade_markers": trade_markers,
            "summary": {
                "total_pnl": round(total_pnl, 4),
                "total_trades": total_trades,
                "total_volume": round(total_volume, 4),
                "active_positions": sum(1 for p in ticker_positions.values() if p.current_position()[0] is not None),
            },
        }

        # Prefer Kalshi-backed balance snapshots for the unfiltered headline
        # chart so the graph follows the same account-truth source as the
        # top-line Net P&L card instead of stale local order replay.
        if not market_id and not model:
            visible_tickers, _ = _display_visible_market_activity(session, resolved_instance)
            display_baseline = _display_baseline_for_instance(session, resolved_instance)
            portfolio_summary = build_portfolio_summary(
                session,
                resolved_instance,
                tickers=visible_tickers,
                starting_total=display_baseline["starting_total"],
                prefer_synced_portfolio_value=True,
            )
            balance_snapshots = (
                session.query(KalshiBalanceSnapshot)
                .filter(
                    KalshiBalanceSnapshot.instance_name == resolved_instance,
                    KalshiBalanceSnapshot.snapshot_ts >= cutoff_date,
                )
                .order_by(KalshiBalanceSnapshot.snapshot_ts.asc(), KalshiBalanceSnapshot.id.asc())
                .all()
            )
            snapshot_series = _build_snapshot_backed_pnl_series(
                balance_snapshots,
                realized_events,
                portfolio_summary.net_pnl,
            )
            if snapshot_series:
                result["series"] = snapshot_series
                result["summary"]["total_pnl"] = round(portfolio_summary.net_pnl, 4)
                result["summary"]["active_positions"] = portfolio_summary.open_positions

        # Update cache for default queries
        if not market_id and not model:
            _pnl_cache[cache_key] = (time.time(), result)

        return result


# ── GET /analytics/summary ──────────────────────────────────────

# Simple in-memory cache to prevent redundant heavy queries
_analytics_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_ANALYTICS_CACHE_TTL = 10  # seconds


@app.get("/analytics/summary")
def get_analytics_summary(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Comprehensive trading analytics: risk metrics, win rate, model/market breakdowns."""
    resolved_instance = _instance_name(instance_name)

    # Check cache
    cache_key = resolved_instance
    if cache_key in _analytics_cache:
        cached_time, cached_data = _analytics_cache[cache_key]
        if time.time() - cached_time < _ANALYTICS_CACHE_TTL:
            return cached_data

    engine = get_db()
    with get_session(engine) as session:
        visible_tickers, visible_market_ids = _display_visible_market_activity(session, resolved_instance)
        display_baseline = _display_baseline_for_instance(session, resolved_instance)
        portfolio_summary = build_portfolio_summary(
            session,
            resolved_instance,
            tickers=visible_tickers,
            starting_total=display_baseline["starting_total"],
            prefer_synced_portfolio_value=True,
        )
        latest_kalshi_orders = [
            row for row in get_latest_order_snapshots(session, resolved_instance)
            if (
                row.ticker in visible_tickers
                and _display_visible_ts(row.created_ts or row.last_update_ts or row.captured_at)
            )
        ]
        orders = [
            SimpleNamespace(
                ticker=row.ticker,
                action=row.action,
                side=row.side,
                count=row.initial_count,
                price_cents=int(round((row.avg_fill_price or row.limit_price or 0.0) * 100)),
                fee_paid=row.fee_paid,
                created_at=row.created_ts or row.last_update_ts or row.captured_at,
                status=row.status,
                filled_shares=row.fill_count,
            )
            for row in latest_kalshi_orders
            if row.fill_count > 0
        ]

        if not orders:
            orders = (
                _instance_query(session, BettingOrder, resolved_instance)
                .filter(
                    BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
                    BettingOrder.created_at >= DISPLAY_CUTOFF_UTC,
                    BettingOrder.ticker.in_(visible_tickers) if visible_tickers else False,
                )
                .order_by(BettingOrder.created_at.desc())
                .limit(1000)
                .all()
            )

        position_views = [
            row
            for row in build_position_views(session, resolved_instance)
            if row.ticker in visible_tickers
        ]
        positions = [
            SimpleNamespace(
                market_id=row.market_id,
                quantity=row.quantity,
                avg_price=row.avg_price,
                realized_pnl=row.realized_pnl,
            )
            for row in position_views
        ]
        if not positions:
            positions = (
                _instance_query(session, TradingPosition, resolved_instance)
                .filter(TradingPosition.market_id.in_(visible_market_ids) if visible_market_ids else False)
                .all()
            )
        # Fetch only markets with positions or recent trades (limited for performance)
        position_market_ids = {p.market_id for p in positions}
        markets = (
            _instance_query(session, TradingMarket, resolved_instance)
            .filter(TradingMarket.market_id.in_(position_market_ids) if position_market_ids else False)
            .all()
        ) if position_market_ids else []
        market_by_id: dict[str, TradingMarket] = {m.market_id: m for m in markets}

        # ── Per-trade P&L computation ─────────────────────────────
        # Use TradingPosition as the source of truth for per-market P&L
        # (consistent with the position heatmap).
        trade_pnls: list[float] = []
        pnl_by_model: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0}
        )
        pnl_by_market: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "title": ""}
        )

        # Build model source lookup: market_id -> model name
        # Only fetch predictions for markets we have positions in (performance optimization)
        market_model_source: dict[str, str] = {}
        if position_market_ids:
            try:
                # Build IN clause for positions only
                placeholders = ", ".join([f":mid_{i}" for i in range(len(position_market_ids))])
                params = {"inst": resolved_instance}
                params.update({f"mid_{i}": mid for i, mid in enumerate(position_market_ids)})

                latest_sources = (
                    session.execute(
                        text(
                            f"SELECT DISTINCT ON (market_id) market_id, source "
                            f"FROM betting_predictions "
                            f"WHERE instance_name = :inst AND market_id IN ({placeholders}) "
                            f"ORDER BY market_id, created_at DESC"
                        ),
                        params,
                    ).fetchall()
                )
                for row in latest_sources:
                    market_model_source[row[0]] = row[1]
            except Exception:
                # Fallback for SQLite (no DISTINCT ON): load minimal columns for position markets only
                try:
                    preds = (
                        _instance_query(session, BettingPrediction, resolved_instance)
                        .filter(BettingPrediction.market_id.in_(position_market_ids))
                        .with_entities(BettingPrediction.market_id, BettingPrediction.source)
                        .order_by(BettingPrediction.created_at.desc())
                        .all()
                    )
                    for mid, src in preds:
                        if mid not in market_model_source:
                            market_model_source[mid] = src
                except Exception:
                    pass

        # Count orders per market for trade counts
        orders_per_market: dict[str, int] = defaultdict(int)
        for o in orders:
            orders_per_market[f"kalshi:{o.ticker}"] += 1

        for pos in positions:
            market_key = pos.market_id
            mkt = market_by_id.get(market_key)
            total_pnl = pos.realized_pnl
            trade_count = orders_per_market.get(market_key, 0)
            trade_pnls.append(total_pnl)

            # Per-market attribution
            pnl_by_market[market_key]["pnl"] = total_pnl
            pnl_by_market[market_key]["trades"] = trade_count
            pnl_by_market[market_key]["title"] = mkt.title if mkt else ""

            # Per-model attribution: use the source from BettingPrediction
            source = market_model_source.get(market_key, "unknown")
            pnl_by_model[source]["pnl"] += total_pnl
            pnl_by_model[source]["trades"] += 1
            if total_pnl > 0:
                pnl_by_model[source]["wins"] += 1
            elif total_pnl < 0:
                pnl_by_model[source]["losses"] += 1

        # ── Risk metrics ──────────────────────────────────────────
        winning_trades = sum(1 for p in trade_pnls if p > 0)
        losing_trades = sum(1 for p in trade_pnls if p < 0)
        decided_trades = winning_trades + losing_trades
        win_rate = _safe_div(winning_trades, decided_trades)

        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p < 0]
        avg_win = _safe_div(sum(wins), len(wins)) if wins else 0.0
        avg_loss = _safe_div(sum(losses), len(losses)) if losses else 0.0

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))
        profit_factor = _safe_div(gross_profit, gross_loss)

        # Daily returns for Sharpe/Sortino
        daily_pnl: dict[str, float] = defaultdict(float)
        for o in orders:
            action = getattr(o, "action", "BUY") or "BUY"
            day_key = o.created_at.strftime("%Y-%m-%d")
            fee_paid = float(getattr(o, "fee_paid", 0) or 0)
            cost = (o.price_cents / 100.0) * o.count
            if action.upper() == "SELL":
                daily_pnl[day_key] += cost - fee_paid  # sell proceeds net of fees
            else:
                daily_pnl[day_key] -= cost + fee_paid  # buy cost plus fees

        starting_total = float(display_baseline["starting_total"] or 0.0)
        daily_returns = [
            _safe_div(value, starting_total)
            for _, value in sorted(daily_pnl.items())
        ] if daily_pnl and starting_total > 1e-9 else []

        if len(daily_returns) >= 2:
            mean_ret = sum(daily_returns) / len(daily_returns)
            variance = sum((r - mean_ret) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
            std_ret = math.sqrt(variance) if variance > 0 else 0.0
            sharpe_ratio = round(_safe_div(mean_ret, std_ret) * math.sqrt(252), 4)
            volatility = round(std_ret * math.sqrt(252), 4)

            # Sortino: only downside deviation
            downside = [r for r in daily_returns if r < 0]
            if len(downside) >= 2:
                down_var = sum(r ** 2 for r in downside) / len(downside)
                down_std = math.sqrt(down_var)
                sortino_ratio = round(_safe_div(mean_ret, down_std) * math.sqrt(252), 4)
            else:
                sortino_ratio = 0.0
        else:
            sharpe_ratio = 0.0
            volatility = 0.0
            sortino_ratio = 0.0

        # Drawdown from cumulative PnL
        cumulative = 0.0
        peak_equity = starting_total
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for _, pnl_value in sorted(daily_pnl.items()):
            cumulative += pnl_value
            equity = starting_total + cumulative
            if equity > peak_equity:
                peak_equity = equity
            dd = max(0.0, peak_equity - equity)
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_pct = _safe_div(dd, peak_equity) if peak_equity > 0 else 0.0

        # Today's PnL
        today_str = datetime.now(UTC).strftime("%Y-%m-%d")
        today_pnl = daily_pnl.get(today_str, 0.0)

        # Total exposure
        total_exposure = sum(
            p.quantity * p.avg_price for p in positions if p.quantity > 0
        )

        # Format model/market breakdowns
        formatted_pnl_by_model = {
            name: {
                "pnl": round(data["pnl"], 4),
                "trades": data["trades"],
                "win_rate": round(_safe_div(data["wins"], data["wins"] + data["losses"]), 4),
            }
            for name, data in pnl_by_model.items()
        }

        formatted_pnl_by_market = {
            mid: {
                "pnl": round(data["pnl"], 4),
                "trades": data["trades"],
                "title": data["title"],
            }
            for mid, data in pnl_by_market.items()
        }

        result = {
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": round(max_drawdown, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "volatility": volatility,
            "sortino_ratio": sortino_ratio,
            "profit_factor": round(profit_factor, 4),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "total_trades": decided_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "pnl_by_model": formatted_pnl_by_model,
            "pnl_by_market": formatted_pnl_by_market,
            "today_pnl": round(today_pnl, 4),
            "total_exposure": round(total_exposure, 4),
            "cash_balance": round(portfolio_summary.cash_balance, 4),
            "cash_pnl": round(portfolio_summary.cash_pnl, 4),
            "open_value": round(portfolio_summary.open_value, 4),
            "cash_spent": round(portfolio_summary.cash_spent, 4),
            "net_pnl": round(portfolio_summary.net_pnl, 4),
            "starting_total": round(portfolio_summary.starting_total, 4),
            "total_fees": round(portfolio_summary.total_fees, 4),
            "open_positions": portfolio_summary.open_positions,
            "active_markets": portfolio_summary.active_markets,
            "return_pct": round(portfolio_summary.return_pct, 6),
        }

        # Update cache
        _analytics_cache[cache_key] = (time.time(), result)
        return result


# ── GET /analytics/model-calibration ────────────────────────────


@app.get("/analytics/model-calibration")
def get_model_calibration(
    model_name: str | None = Query(None, description="Filter by model name"),
    bins: int = Query(10, ge=2, le=50, description="Number of calibration bins"),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Model calibration analysis: compare predicted probabilities with outcomes.

    For resolved markets (expired with last_price of 0 or 1), compares
    model p_yes predictions against actual outcomes.
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        resolved_markets = _load_resolved_visible_markets(session, resolved_instance)
        resolved_map: dict[str, float] = {
            market.market_id: outcome for market, outcome in resolved_markets
        }

        if not resolved_map:
            return {
                "calibration": [],
                "brier_score": 0.0,
                "models": [],
                "by_model": {},
            }

        # Get predictions for resolved markets
        pred_query = _instance_query(session, BettingPrediction, resolved_instance).filter(
            BettingPrediction.market_id.in_(list(resolved_map.keys())),
            BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC,
        )
        if model_name:
            pred_query = pred_query.filter(BettingPrediction.source == model_name)
        predictions = pred_query.all()

        if not predictions:
            return {
                "calibration": [],
                "brier_score": 0.0,
                "models": [],
                "by_model": {},
            }

        # Collect all model names
        all_models = sorted(set(p.source for p in predictions))

        # Build calibration data
        def compute_calibration(
            preds: list[BettingPrediction], n_bins: int
        ) -> tuple[list[dict[str, Any]], float]:
            """Compute calibration bins and Brier score for a set of predictions."""
            bin_width = 1.0 / n_bins
            bin_data: dict[int, dict[str, Any]] = {
                i: {"predicted_sum": 0.0, "outcome_sum": 0.0, "count": 0}
                for i in range(n_bins)
            }

            brier_sum = 0.0
            total = 0

            # Deduplicate: use latest prediction per market per source
            latest_by_market: dict[tuple[str, str], BettingPrediction] = {}
            for p in preds:
                key = (p.market_id, p.source)
                if key not in latest_by_market or p.created_at > latest_by_market[key].created_at:
                    latest_by_market[key] = p

            for p in latest_by_market.values():
                outcome = resolved_map.get(p.market_id)
                if outcome is None:
                    continue

                bin_idx = min(int(p.p_yes / bin_width), n_bins - 1)
                bin_data[bin_idx]["predicted_sum"] += p.p_yes
                bin_data[bin_idx]["outcome_sum"] += outcome
                bin_data[bin_idx]["count"] += 1

                brier_sum += (p.p_yes - outcome) ** 2
                total += 1

            calibration = []
            for i in range(n_bins):
                bd = bin_data[i]
                if bd["count"] > 0:
                    calibration.append({
                        "bin_center": round((i + 0.5) * bin_width, 4),
                        "predicted_avg": round(bd["predicted_sum"] / bd["count"], 4),
                        "observed_freq": round(bd["outcome_sum"] / bd["count"], 4),
                        "count": bd["count"],
                    })
                else:
                    calibration.append({
                        "bin_center": round((i + 0.5) * bin_width, 4),
                        "predicted_avg": 0.0,
                        "observed_freq": 0.0,
                        "count": 0,
                    })

            brier_score = round(_safe_div(brier_sum, total), 6) if total > 0 else 0.0
            return calibration, brier_score

        # Overall calibration
        overall_cal, overall_brier = compute_calibration(predictions, bins)

        # Market baseline Brier score: use yes_ask as the "prediction"
        # (how well the market price predicts outcomes)
        latest_by_market_pred: dict[tuple[str, str], BettingPrediction] = {}
        for p in predictions:
            key = (p.market_id, p.source)
            if key not in latest_by_market_pred or p.created_at > latest_by_market_pred[key].created_at:
                latest_by_market_pred[key] = p
        # Deduplicate by market_id for baseline (one prediction per market)
        seen_market_ids: set[str] = set()
        baseline_brier_sum = 0.0
        baseline_total = 0
        for p in latest_by_market_pred.values():
            if p.market_id in seen_market_ids:
                continue
            seen_market_ids.add(p.market_id)
            outcome = resolved_map.get(p.market_id)
            if outcome is None:
                continue
            baseline_brier_sum += (p.yes_ask - outcome) ** 2
            baseline_total += 1
        market_baseline_brier = round(
            baseline_brier_sum / baseline_total, 6
        ) if baseline_total > 0 else 0.25

        # Per-model calibration
        by_model: dict[str, dict[str, Any]] = {}
        for m in all_models:
            model_preds = [p for p in predictions if p.source == m]
            cal, brier = compute_calibration(model_preds, bins)
            by_model[m] = {
                "brier_score": brier,
                "total_predictions": len(model_preds),
                "calibration": cal,
            }

        return {
            "calibration": overall_cal,
            "brier_score": overall_brier,
            "market_baseline_brier": market_baseline_brier,
            "models": all_models,
            "by_model": by_model,
        }


# ── GET /analytics/brier-scores ─────────────────────────────────


@app.get("/analytics/brier-scores")
def get_brier_scores(
    model_name: str | None = Query(None, description="Filter by model name"),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Timestep-level Brier scores for resolved events.

    For each BettingPrediction on a resolved market, computes a Brier score
    for both the model probability and the market price, framed from the
    resolved side (outcome = 1).  Lower scores are better.

    - Model Brier:  (model_prob_resolved_side - 1)^2
    - Market Brier: (market_prob_resolved_side - 1)^2

    Where:
      resolved YES  → model_prob = p_yes,       market_prob = yes_ask
      resolved NO   → model_prob = 1 - p_yes,   market_prob = no_ask
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        resolved_markets = _load_resolved_visible_markets(session, resolved_instance)
        resolved_map: dict[str, float] = {
            market.market_id: outcome for market, outcome in resolved_markets
        }
        title_map: dict[str, str] = {
            market.market_id: market.title for market, _ in resolved_markets
        }

        empty_response: dict[str, Any] = {
            "series": [],
            "summary": {
                "model_avg_brier": 0.0,
                "market_avg_brier": 0.0,
                "total_predictions": 0,
                "total_markets": 0,
            },
            "by_model": {},
        }

        if not resolved_map:
            return empty_response

        # 2. All predictions for resolved markets (every timestep)
        pred_query = _instance_query(
            session, BettingPrediction, resolved_instance
        ).filter(
            BettingPrediction.market_id.in_(list(resolved_map.keys())),
            BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC,
        )
        if model_name:
            pred_query = pred_query.filter(BettingPrediction.source == model_name)
        predictions = pred_query.order_by(BettingPrediction.created_at.asc()).all()

        if not predictions:
            return empty_response

        # 3. Compute per-timestep Brier scores
        series: list[dict[str, Any]] = []
        model_brier_sum = 0.0
        market_brier_sum = 0.0
        total = 0
        seen_markets: set[str] = set()

        # Per-model accumulators
        by_model_acc: dict[str, dict[str, float | int]] = defaultdict(
            lambda: {"model_brier_sum": 0.0, "market_brier_sum": 0.0, "count": 0}
        )

        for p in predictions:
            outcome = resolved_map.get(p.market_id)
            if outcome is None:
                continue

            resolved_yes = outcome == 1.0

            if resolved_yes:
                model_prob = p.p_yes
                market_prob = p.yes_ask
            else:
                model_prob = 1.0 - p.p_yes
                market_prob = p.no_ask

            m_brier = (model_prob - 1.0) ** 2
            mkt_brier = (market_prob - 1.0) ** 2

            series.append({
                "timestamp": p.created_at.isoformat(),
                "market_id": p.market_id,
                "market_title": title_map.get(p.market_id, ""),
                "source": p.source,
                "outcome": "YES" if resolved_yes else "NO",
                "model_prob": round(model_prob, 6),
                "market_prob": round(market_prob, 6),
                "model_brier": round(m_brier, 6),
                "market_brier": round(mkt_brier, 6),
            })

            model_brier_sum += m_brier
            market_brier_sum += mkt_brier
            total += 1
            seen_markets.add(p.market_id)

            acc = by_model_acc[p.source]
            acc["model_brier_sum"] += m_brier
            acc["market_brier_sum"] += mkt_brier
            acc["count"] += 1

        # 4. Build response
        by_model: dict[str, dict[str, Any]] = {}
        for model, acc in sorted(by_model_acc.items()):
            cnt = int(acc["count"])
            by_model[model] = {
                "model_avg_brier": round(acc["model_brier_sum"] / cnt, 6) if cnt else 0.0,
                "market_avg_brier": round(acc["market_brier_sum"] / cnt, 6) if cnt else 0.0,
                "count": cnt,
            }

        return {
            "series": series,
            "summary": {
                "model_avg_brier": round(model_brier_sum / total, 6) if total else 0.0,
                "market_avg_brier": round(market_brier_sum / total, 6) if total else 0.0,
                "total_predictions": total,
                "total_markets": len(seen_markets),
            },
            "by_model": by_model,
        }


# ── GET /analytics/resolved-markets ─────────────────────────────


@app.get("/analytics/resolved-markets")
def get_resolved_markets(
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Resolved markets P&L for visible expired markets with a known final outcome."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        markets = _load_resolved_visible_markets(session, resolved_instance)

        if not markets:
            return {
                "markets": [],
                "summary": {
                    "total_pnl": 0.0,
                    "total_markets": 0,
                    "markets_with_position": 0,
                    "win_count": 0,
                    "loss_count": 0,
                    "total_capital": 0.0,
                    "win_rate": 0.0,
                },
            }

        tickers = [market.ticker for market, _ in markets if market.ticker]
        trade_state = _build_resolved_market_trade_state(session, resolved_instance, tickers)

        # Load all orders for trade history (BettingOrder + KalshiOrderSnapshot fallback)
        all_orders = load_replayable_orders(session, BettingOrder, resolved_instance, tickers=tickers)
        bo_tickers = {getattr(o, "ticker", "") for o in all_orders}
        snapshot_only_tickers = [t for t in tickers if t and t not in bo_tickers]
        if snapshot_only_tickers:
            from kalshi_state import get_latest_order_snapshots
            extra_snaps = get_latest_order_snapshots(session, resolved_instance, tickers=snapshot_only_tickers)
            for snap in extra_snaps:
                if snap.fill_count <= 0:
                    continue
                price = float(snap.avg_fill_price or 0)
                action = (snap.action or "BUY").upper()
                # Correct inverted SELL prices
                if action == "SELL" and 0 < price < 1 and snap.limit_price is not None:
                    limit = float(snap.limit_price)
                    if limit > 0 and abs(price - limit) > abs((1 - price) - limit):
                        price = 1.0 - price
                all_orders.append(SimpleNamespace(
                    ticker=snap.ticker, action=action,
                    side=(snap.side or "yes").lower(),
                    filled_shares=snap.fill_count, fill_price=price,
                    fee_paid=snap.fee_paid or 0,
                    price_cents=int(round(price * 100)),
                    status=snap.status,
                    created_at=snap.created_ts or snap.last_update_ts or snap.captured_at,
                    count=int(round(snap.fill_count)),
                ))
            all_orders.sort(key=lambda o: getattr(o, "created_at", None) or datetime.min.replace(tzinfo=UTC))
        logger.info("Loaded %d orders for %d tickers for instance %s", len(all_orders), len(tickers), resolved_instance)

        rows = []
        for mkt, outcome in markets:
            replay = trade_state.get(mkt.ticker, {})
            replay_pos = replay.get("position")
            resolved_at = mkt.expiration.isoformat() if mkt.expiration else None
            position_side: str | None = None
            quantity = 0.0
            avg_price = 0.0
            capital = 0.0
            pnl = 0.0

            if isinstance(replay_pos, InventoryPosition):
                side, qty, avg = replay_pos.current_position()
                if side is not None and qty > EPSILON:
                    # Active position at resolution time
                    if outcome >= 0:
                        settlement_price = outcome if side == "yes" else 1.0 - outcome
                        unrealized = (settlement_price - avg) * qty
                        pnl = round(replay_pos.realized_pnl + unrealized, 4)
                    else:
                        pnl = round(replay_pos.realized_pnl, 4)
                    position_side = side.upper()
                    quantity = round(qty, 4)
                    avg_price = round(avg, 4)
                    capital = round(qty * avg, 4)
                elif abs(replay_pos.realized_pnl) > EPSILON:
                    # Fully exited before resolution — still show with realized P&L
                    pnl = round(replay_pos.realized_pnl, 4)
                    if replay_pos.last_side:
                        position_side = replay_pos.last_side.upper()
                    capital = round(replay_pos.total_buy_cost, 4)

            ret_pct = round(pnl / capital * 100, 2) if capital else 0.0
            correct = None
            if position_side is not None and outcome >= 0:
                correct = (
                    (position_side == "YES" and outcome == 1.0) or
                    (position_side == "NO" and outcome == 0.0)
                )

            # Determine outcome display string
            if outcome == 1.0:
                outcome_str = "YES"
            elif outcome == 0.0:
                outcome_str = "NO"
            else:
                outcome_str = "PENDING"  # Market expired but outcome unknown

            # Get trade history for this market
            trade_history = []
            if mkt.ticker:
                market_orders = [o for o in all_orders if getattr(o, "ticker", "") == mkt.ticker]
                for order in market_orders:
                    status = str(getattr(order, "status", "")).upper()
                    # Skip settlement orders — settlement is computed from outcome
                    if status == "SETTLED":
                        continue

                    action = str(getattr(order, "action", "")).upper()
                    side = str(getattr(order, "side", "")).lower()
                    # Use filled_shares (actual fills), matching normalize_order()
                    shares = float(getattr(order, "filled_shares", 0) or 0)
                    price = float(getattr(order, "fill_price", 0) or 0)
                    if price <= 0:
                        price_cents = float(getattr(order, "price_cents", 0) or 0)
                        price = price_cents / 100.0
                    if price > 1.0:
                        price = price / 100.0

                    created_at = getattr(order, "created_at", None)

                    if shares > 0:
                        trade_value = shares * price
                        trade_history.append({
                            "date": created_at.isoformat() if created_at else None,
                            "action": action,
                            "side": side.upper(),
                            "shares": round(shares, 2),
                            "price": round(price, 4),
                            "value": round(trade_value, 2),
                            "status": status,
                        })

            rows.append({
                "market_id": mkt.market_id,
                "title": mkt.title,
                "ticker": mkt.ticker,
                "category": mkt.category,
                "resolved_at": resolved_at,
                "outcome": outcome_str,
                "position_side": position_side,
                "quantity": quantity,
                "avg_price": avg_price,
                "capital": capital,
                "pnl": pnl,
                "return_pct": ret_pct,
                "correct": correct,
                "trades": trade_history,
            })

        with_pos = [r for r in rows if r["position_side"] is not None]
        total_pnl = round(sum(r["pnl"] for r in with_pos), 4)
        total_capital = round(sum(r["capital"] for r in with_pos), 4)
        win_count = sum(1 for r in with_pos if r["pnl"] > 0)
        loss_count = sum(1 for r in with_pos if r["pnl"] < 0)
        win_rate = round(win_count / len(with_pos) * 100, 1) if with_pos else 0.0

        # Compute Brier scores for resolved markets
        brier_score: float | None = None
        market_baseline_brier: float | None = None

        if markets:
            resolved_market_ids = {market.market_id: outcome for market, outcome in markets}
            predictions = (
                _instance_query(session, BettingPrediction, resolved_instance)
                .filter(
                    BettingPrediction.market_id.in_(list(resolved_market_ids.keys())),
                    BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC,
                )
                .all()
            )

            # Deduplicate: latest prediction per market per source
            latest_by_market: dict[tuple[str, str], BettingPrediction] = {}
            for p in predictions:
                key = (p.market_id, p.source)
                if key not in latest_by_market or p.created_at > latest_by_market[key].created_at:
                    latest_by_market[key] = p

            # Model Brier score
            model_brier_sum = 0.0
            model_count = 0
            for p in latest_by_market.values():
                outcome = resolved_market_ids.get(p.market_id)
                if outcome is not None:
                    model_brier_sum += (p.p_yes - outcome) ** 2
                    model_count += 1

            # Market baseline Brier score (yes_ask)
            seen_markets: set[str] = set()
            market_brier_sum = 0.0
            market_count = 0
            for p in latest_by_market.values():
                if p.market_id in seen_markets:
                    continue
                seen_markets.add(p.market_id)
                outcome = resolved_market_ids.get(p.market_id)
                if outcome is not None:
                    market_brier_sum += (p.yes_ask - outcome) ** 2
                    market_count += 1

            if model_count > 0:
                brier_score = round(model_brier_sum / model_count, 6)
            if market_count > 0:
                market_baseline_brier = round(market_brier_sum / market_count, 6)

        summary: dict[str, Any] = {
            "total_pnl": total_pnl,
            "total_markets": len(rows),
            "markets_with_position": len(with_pos),
            "win_count": win_count,
            "loss_count": loss_count,
            "total_capital": total_capital,
            "win_rate": win_rate,
        }

        if brier_score is not None:
            summary["brier_score"] = brier_score
        if market_baseline_brier is not None:
            summary["market_baseline_brier"] = market_baseline_brier

        return {
            "markets": rows,
            "summary": summary,
        }


# ── GET /alerts ──────────────────────────────────────────────────


class AlertClearRequest(BaseModel):
    alert_key: str
    instance_name: str | None = None


def _build_alert(
    *,
    key: str,
    alert_type: str,
    severity: str,
    message: str,
    timestamp: str,
    market_id: str | None = None,
) -> dict[str, Any]:
    alert = {
        "key": key,
        "type": alert_type,
        "severity": severity,
        "message": message,
        "timestamp": timestamp,
    }
    if market_id is not None:
        alert["market_id"] = market_id
    return alert


@app.get("/alerts")
def get_alerts(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Active system alerts: stale workers, exposure limits, model divergences, errors."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    alerts: list[dict[str, Any]] = []
    now = datetime.now(UTC)
    stale_threshold_sec = _worker_stale_threshold_sec(resolved_instance)
    sticky_alert_keys = {"stale_worker", "exposure"}

    with get_session(engine) as session:
        # 1. Stale worker check: heartbeat older than 1.5x poll interval
        last_heartbeat = (
            _heartbeat_query(session, resolved_instance)
            .order_by(SystemLog.created_at.desc())
            .first()
        )
        if last_heartbeat:
            age_sec = (now - last_heartbeat.created_at.replace(tzinfo=UTC)).total_seconds()
            if age_sec > stale_threshold_sec:
                minutes_ago = int(age_sec / 60)
                alerts.append(_build_alert(
                    key="stale_worker",
                    alert_type="stale_worker",
                    severity="error",
                    message=(
                        f"Worker heartbeat is {minutes_ago} minutes old. "
                        f"Last seen: {last_heartbeat.created_at.isoformat()}"
                    ),
                    timestamp=now.isoformat(),
                ))
        else:
            alerts.append(_build_alert(
                key="stale_worker",
                alert_type="stale_worker",
                severity="error",
                message="No worker heartbeat found. Worker may have never started.",
                timestamp=now.isoformat(),
            ))

        # 2. Total exposure check
        positions = _instance_query(session, TradingPosition, resolved_instance).all()
        total_exposure = sum(
            p.quantity * p.avg_price for p in positions if p.quantity > 0
        )
        exposure_threshold = float(os.getenv("ALERT_EXPOSURE_THRESHOLD", "500"))
        if total_exposure > exposure_threshold:
            alerts.append(_build_alert(
                key="exposure",
                alert_type="exposure",
                severity="warning",
                message=(
                    f"Total exposure ${total_exposure:.2f} exceeds threshold "
                    f"${exposure_threshold:.2f}"
                ),
                timestamp=now.isoformat(),
            ))

        # 3. Model-market divergence > 20pp
        markets = _instance_query(session, TradingMarket, resolved_instance).all()
        latest_predictions = _latest_predictions_by_market_id(
            session,
            resolved_instance,
            [mkt.market_id for mkt in markets],
        )
        for mkt in markets:
            if mkt.yes_ask is None:
                continue
            latest_pred = latest_predictions.get(mkt.market_id)
            if latest_pred:
                divergence = abs(latest_pred.p_yes - mkt.yes_ask)
                if divergence > 0.20:
                    alerts.append(_build_alert(
                        key=f"divergence:{mkt.market_id}:{latest_pred.id}",
                        alert_type="divergence",
                        severity="warning",
                        message=(
                            f"Model-market divergence of {divergence:.0%} on "
                            f"'{mkt.title}': model={latest_pred.p_yes:.2f}, "
                            f"market={mkt.yes_ask:.2f}"
                        ),
                        market_id=mkt.market_id,
                        timestamp=now.isoformat(),
                    ))

        # 4. Recent ERROR logs (last 1 hour)
        one_hour_ago = now - timedelta(hours=1)
        error_logs = (
            _instance_query(session, SystemLog, resolved_instance)
            .filter(
                SystemLog.level == "ERROR",
                SystemLog.created_at >= one_hour_ago,
            )
            .order_by(SystemLog.created_at.desc())
            .limit(10)
            .all()
        )
        for log in error_logs:
            alerts.append(_build_alert(
                key=f"system_error:{log.id}",
                alert_type="system_error",
                severity="error",
                message=f"[{log.component}] {log.message}",
                timestamp=log.created_at.isoformat(),
            ))

        active_keys = {alert["key"] for alert in alerts}
        inactive_sticky_keys = sticky_alert_keys - active_keys
        if inactive_sticky_keys:
            (
                _instance_query(session, AlertDismissal, resolved_instance)
                .filter(AlertDismissal.alert_key.in_(inactive_sticky_keys))
                .delete(synchronize_session=False)
            )

        dismissed_keys = {
            row.alert_key
            for row in _instance_query(session, AlertDismissal, resolved_instance).all()
        }

    return {"alerts": [alert for alert in alerts if alert["key"] not in dismissed_keys]}


@app.post("/alerts/clear")
def clear_alert(req: AlertClearRequest) -> dict[str, Any]:
    resolved_instance = _instance_name(req.instance_name)
    engine = get_db()
    with get_session(engine) as session:
        existing = (
            _instance_query(session, AlertDismissal, resolved_instance)
            .filter(AlertDismissal.alert_key == req.alert_key)
            .first()
        )
        if existing is None:
            session.add(
                AlertDismissal(
                    instance_name=resolved_instance,
                    alert_key=req.alert_key,
                    created_at=datetime.now(UTC),
                )
            )

    return {"ok": True, "alert_key": req.alert_key, "instance_name": resolved_instance}


class AlertClearAllRequest(BaseModel):
    instance_name: str | None = None


@app.post("/alerts/clear-all")
def clear_all_alerts(req: AlertClearAllRequest) -> dict[str, Any]:
    """Dismiss all currently active alerts for the instance."""
    resolved_instance = _instance_name(req.instance_name)
    # Reuse get_alerts to find active alert keys
    active_alerts = get_alerts(resolved_instance)["alerts"]
    if not active_alerts:
        return {"ok": True, "cleared": 0, "instance_name": resolved_instance}

    engine = get_db()
    cleared = 0
    with get_session(engine) as session:
        existing_keys = {
            row.alert_key
            for row in _instance_query(session, AlertDismissal, resolved_instance).all()
        }
        for alert in active_alerts:
            if alert["key"] not in existing_keys:
                session.add(
                    AlertDismissal(
                        instance_name=resolved_instance,
                        alert_key=alert["key"],
                        created_at=datetime.now(UTC),
                    )
                )
                cleared += 1

    return {"ok": True, "cleared": cleared, "instance_name": resolved_instance}


# ── GET /cycle-evaluations ────────────────────────────────────────


@app.get("/cycle-evaluations")
def get_cycle_evaluations(
    ticker: str | None = Query(None, description="Filter by market ticker"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Get all cycle evaluations including holds, buys, and sells.

    Shows every time the comparison worker evaluated a market,
    what it predicted, and what decision was made.
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()

    with get_session(engine) as session:
        evaluations = []

        # Model runs are the authoritative record of whether a market was
        # evaluated in a cycle. Predictions/orders may be absent for skipped
        # or failed cycles, so join them on as optional nearby records.
        query = text("""
            SELECT
                mr.id as eval_id,
                mr.market_id,
                mr.model_name,
                mr.timestamp as eval_time,
                tm.ticker as market_ticker,
                tm.title as market_title,
                bp.id as pred_id,
                bp.p_yes as pred_p_yes,
                bp.yes_ask as pred_yes_ask,
                bp.no_ask as pred_no_ask,
                bp.skip_reason as skip_reason,
                bo.id as order_id,
                bo.order_id as local_order_id,
                bo.exchange_order_id as exchange_order_id,
                bo.action as order_action,
                bo.side as order_side,
                bo.count as order_count,
                bo.status as order_status,
                bo.price_cents as order_price,
                bo.filled_shares as order_filled,
                bo.fee_paid as order_fee,
                mr.metadata_json as model_metadata,
                mr.decision as model_decision
            FROM model_runs mr
            LEFT JOIN trading_markets tm ON tm.market_id = mr.market_id AND tm.instance_name = mr.instance_name
            LEFT JOIN LATERAL (
                SELECT bp.*
                FROM betting_predictions bp
                WHERE bp.instance_name = mr.instance_name
                  AND bp.market_id = mr.market_id
                  AND bp.source = mr.model_name
                  AND bp.created_at BETWEEN mr.timestamp - INTERVAL '2 minutes' AND mr.timestamp + INTERVAL '2 minutes'
                ORDER BY ABS(EXTRACT(EPOCH FROM (bp.created_at - mr.timestamp))), bp.id DESC
                LIMIT 1
            ) bp ON TRUE
            LEFT JOIN LATERAL (
                SELECT bo.*
                FROM betting_orders bo
                WHERE bo.instance_name = mr.instance_name
                  AND tm.ticker IS NOT NULL
                  AND bo.ticker = tm.ticker
                  AND bo.created_at BETWEEN mr.timestamp - INTERVAL '2 minutes' AND mr.timestamp + INTERVAL '2 minutes'
                ORDER BY ABS(EXTRACT(EPOCH FROM (bo.created_at - mr.timestamp))), bo.id DESC
                LIMIT 1
            ) bo ON TRUE
            WHERE mr.instance_name = :instance
            AND mr.timestamp >= :cutoff
            AND (:ticker IS NULL OR tm.ticker = :ticker)
            ORDER BY mr.timestamp DESC, mr.id DESC
            LIMIT :limit OFFSET :offset
        """)

        result = session.execute(
            query,
            {
                "instance": resolved_instance,
                "ticker": ticker,
                "cutoff": DISPLAY_CUTOFF_UTC,
                "limit": limit,
                "offset": offset
            }
        )
        rows = result.fetchall()

        timeline_tickers = {row.market_ticker for row in rows if row.market_ticker}
        latest_order_snaps = get_latest_order_snapshots(session, resolved_instance, tickers=timeline_tickers)
        snap_by_exchange = {snap.order_id: snap for snap in latest_order_snaps}
        snap_by_client = {snap.client_order_id: snap for snap in latest_order_snaps if snap.client_order_id}

        for row in rows:
            market_ticker = row.market_ticker
            parsed_p_yes = row.pred_p_yes
            parsed_yes_ask = row.pred_yes_ask
            parsed_no_ask = row.pred_no_ask
            live_order = None
            if row.exchange_order_id:
                live_order = snap_by_exchange.get(row.exchange_order_id)
            if live_order is None and row.local_order_id:
                live_order = snap_by_client.get(row.local_order_id)

            model_reasoning = None
            model_rationale = None
            model_skip_reason = None
            strategy_metadata = None
            if row.model_metadata:
                try:
                    metadata = json.loads(row.model_metadata)
                    model_reasoning = metadata.get("reasoning")
                    model_rationale = metadata.get("rationale")
                    model_skip_reason = metadata.get("skip_reason")
                    strategy_metadata = metadata.get("strategy") if isinstance(metadata.get("strategy"), dict) else None
                    if parsed_p_yes is None:
                        parsed_p_yes = metadata.get("p_yes")
                    if parsed_yes_ask is None:
                        parsed_yes_ask = metadata.get("yes_ask")
                    if parsed_no_ask is None:
                        parsed_no_ask = metadata.get("no_ask")
                except (json.JSONDecodeError, TypeError):
                    strategy_metadata = None

            p_yes = float(parsed_p_yes) if parsed_p_yes is not None else None
            yes_ask = float(parsed_yes_ask) if parsed_yes_ask is not None else None
            no_ask = float(parsed_no_ask) if parsed_no_ask is not None else None

            edge = None
            if p_yes is not None and yes_ask is not None:
                edge = (p_yes - yes_ask) * 100

            # Determine the action taken based on whether an order exists
            if row.model_decision in {"SKIP", "CYCLE_SKIPPED", "NO_PREDICTION"}:
                action_taken = "NO PREDICTION" if row.model_decision == "NO_PREDICTION" else "SKIP"
                action_type = "skip"
            elif row.model_decision == "HOLD" and row.order_id and strategy_metadata and strategy_metadata.get("flatten_reason") == "WITHIN_SPREAD":
                action_taken = "HOLD (within spread)"
                action_type = "hold"
            elif row.order_id:
                action_type = row.order_action.lower() if row.order_action else "buy"

                if row.order_status == "FILLED":
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side}"
                elif row.order_status == "DRY_RUN":
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side} (dry run)"
                elif row.order_status == "REJECTED":
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side} (rejected)"
                elif row.order_status == "CANCELLED":
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side} (cancelled)"
                elif row.order_status == "ERROR":
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side} (error)"
                else:
                    action_taken = f"{row.order_action} {row.order_count} {row.order_side} (pending)"
            elif row.model_decision and row.model_decision.startswith("BUY_"):
                action_taken = row.model_decision.replace("_", " ")
                action_type = "buy"
            elif row.model_decision and row.model_decision.startswith("SELL_"):
                action_taken = row.model_decision.replace("_", " ")
                action_type = "sell"
            elif row.model_decision == "SKIP":
                action_taken = "SKIP"
                action_type = "skip"
            else:
                action_taken = "HOLD"
                action_type = "hold"

            hold_reason = None
            if action_type == "hold":
                hold_reason = _hold_reason_from_market_context(
                    model_decision=row.model_decision,
                    strategy_metadata=strategy_metadata,
                    has_order=bool(row.order_id),
                    p_yes=p_yes,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                )

            # Determine reason for action/inaction
            # First check database skip_reason field
            if row.skip_reason:
                reason = row.skip_reason
            elif action_type == "hold":
                reason = hold_reason
            elif action_type == "skip" and model_skip_reason:
                reason = model_skip_reason
            elif model_reasoning or model_rationale:
                reason = model_reasoning or model_rationale
            elif action_type == "skip":
                reason = "Skipped because the order would consume more than $50 of capital."
            elif edge is not None:
                reason = f"Edge {edge:.1f}% → {action_type}"
            else:
                reason = None

            evaluations.append({
                "id": row.eval_id,
                "ticker": market_ticker,
                "market_id": row.market_id,
                "market_title": row.market_title,
                "timestamp": row.eval_time.isoformat() if row.eval_time else None,
                "model": row.model_name,
                "prediction": {
                    "p_yes": p_yes,
                    "edge": edge,
                    "yes_ask": yes_ask,
                    "no_ask": no_ask,
                },
                "action": {
                    "type": action_type,
                    "description": action_taken,
                    "reason": reason,
                    "rationale": model_rationale,  # Add the actual LLM rationale
                },
                "order": {
                    "action": row.order_action,
                    "side": row.order_side,
                    "count": live_order.initial_count if live_order else row.order_count,
                    "filled": live_order.fill_count if live_order else row.order_filled,
                    "price_cents": int(round((live_order.avg_fill_price or live_order.limit_price or 0) * 100)) if live_order else row.order_price,
                    "status": live_order.status if live_order else row.order_status,
                    "fee_paid": float(live_order.fee_paid if live_order else (row.order_fee or 0)),
                } if row.order_id else None,
            })

        # Get total count for pagination
        count_query = text("""
            SELECT COUNT(*)
            FROM model_runs mr
            LEFT JOIN trading_markets tm ON tm.market_id = mr.market_id AND tm.instance_name = mr.instance_name
            WHERE mr.instance_name = :instance
            AND mr.timestamp >= :cutoff
            AND (:ticker IS NULL OR tm.ticker = :ticker)
        """)

        total = session.execute(
            count_query,
            {"instance": resolved_instance, "ticker": ticker, "cutoff": DISPLAY_CUTOFF_UTC}
        ).scalar() or 0

        # Post-process to collapse consecutive skips with same reason
        collapsed_evaluations = []
        consecutive_skips = []

        for eval in evaluations:
            # Check if this is a skip with a reason
            is_skip = (eval["action"]["type"] == "skip" or eval["action"]["type"] == "hold") and eval["action"]["reason"]

            if is_skip and consecutive_skips and consecutive_skips[-1]["action"]["reason"] == eval["action"]["reason"] and consecutive_skips[-1]["ticker"] == eval["ticker"]:
                # Same skip reason and same market, accumulate
                consecutive_skips.append(eval)
            else:
                # Different action or reason - flush any accumulated skips
                if len(consecutive_skips) > 2:  # Collapse if 3+ consecutive skips
                    first_skip = consecutive_skips[0]
                    last_skip = consecutive_skips[-1]
                    collapsed_evaluations.append({
                        **first_skip,
                        "collapsed": True,
                        "skip_count": len(consecutive_skips),
                        "time_range": {
                            "start": first_skip["timestamp"],
                            "end": last_skip["timestamp"],
                        },
                        "action": {
                            **first_skip["action"],
                            "description": f"Skipped {len(consecutive_skips)} cycles",
                            "reason": f"{first_skip['action']['reason']} ({len(consecutive_skips)} cycles)",
                        }
                    })
                else:
                    # Add individual skips if less than 3
                    collapsed_evaluations.extend(consecutive_skips)

                # Reset accumulator
                consecutive_skips = [eval] if is_skip else []

                # Add current eval if not a skip
                if not is_skip:
                    collapsed_evaluations.append(eval)

        # Handle any remaining consecutive skips
        if len(consecutive_skips) > 2:
            first_skip = consecutive_skips[0]
            last_skip = consecutive_skips[-1]
            collapsed_evaluations.append({
                **first_skip,
                "collapsed": True,
                "skip_count": len(consecutive_skips),
                "time_range": {
                    "start": first_skip["timestamp"],
                    "end": last_skip["timestamp"],
                },
                "action": {
                    **first_skip["action"],
                    "description": f"Skipped {len(consecutive_skips)} cycles",
                    "reason": f"{first_skip['action']['reason']} ({len(consecutive_skips)} cycles)",
                }
            })
        else:
            collapsed_evaluations.extend(consecutive_skips)

        return {
            "evaluations": collapsed_evaluations,
            "total": total,
            "has_more": offset + limit < total,
            "ticker": ticker,
        }


# ── GET /predictions/{market_id} ─────────────────────────────────


@app.get("/predictions/{market_id}")
def get_predictions(
    market_id: str,
    limit: int = Query(500, ge=1, le=5000),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Time series of predictions and prices for a specific market."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        # Get predictions for this market
        predictions = (
            _instance_query(session, BettingPrediction, resolved_instance)
            .filter(
                BettingPrediction.market_id == market_id,
                BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC,
            )
            .order_by(BettingPrediction.created_at.asc())
            .limit(limit)
            .all()
        )

        # Get price snapshots for this market
        snapshots = (
            _instance_query(session, MarketPriceSnapshot, resolved_instance)
            .filter(
                MarketPriceSnapshot.market_id == market_id,
                MarketPriceSnapshot.timestamp >= DISPLAY_CUTOFF_UTC,
            )
            .order_by(MarketPriceSnapshot.timestamp.asc())
            .limit(limit)
            .all()
        )

        # Merge predictions and snapshots into a unified series
        series: list[dict[str, Any]] = []

        # Add prediction data points
        for p in predictions:
            edge = p.p_yes - p.yes_ask if p.yes_ask else 0.0
            series.append({
                "timestamp": p.created_at.isoformat(),
                "p_yes": p.p_yes,
                "yes_ask": p.yes_ask,
                "no_ask": p.no_ask,
                "source": p.source,
                "edge": round(edge, 4),
            })

        # Add price snapshot data points (only if no prediction at that time)
        pred_timestamps = {p.created_at.isoformat() for p in predictions}
        for s in snapshots:
            ts = s.timestamp.isoformat()
            if ts not in pred_timestamps:
                series.append({
                    "timestamp": ts,
                    "p_yes": s.model_p_yes,
                    "yes_ask": s.yes_ask,
                    "no_ask": s.no_ask,
                    "source": s.model_name,
                    "edge": round((s.model_p_yes or 0) - s.yes_ask, 4) if s.model_p_yes else 0.0,
                })

        # Sort by timestamp
        series.sort(key=lambda x: x["timestamp"])

        return {
            "market_id": market_id,
            "series": series,
        }


# ── GET /market-price-history/{market_id} ────────────────────────


@app.get("/market-price-history/{market_id}")
def get_market_price_history(
    market_id: str,
    limit: int = Query(1000, ge=1, le=10000),
    instance_name: str | None = Query(None),
) -> dict[str, Any]:
    """Time series of price snapshots from MarketPriceSnapshot table."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        snapshots = (
            _instance_query(session, MarketPriceSnapshot, resolved_instance)
            .filter(
                MarketPriceSnapshot.market_id == market_id,
                MarketPriceSnapshot.timestamp >= DISPLAY_CUTOFF_UTC,
            )
            .order_by(MarketPriceSnapshot.timestamp.asc())
            .limit(limit)
            .all()
        )

        series = [
            {
                "timestamp": s.timestamp.isoformat(),
                "yes_ask": s.yes_ask,
                "no_ask": s.no_ask,
                "volume_24h": s.volume_24h,
                "model_p_yes": s.model_p_yes,
                "model_name": s.model_name,
            }
            for s in snapshots
        ]

        return {
            "market_id": market_id,
            "series": series,
            "count": len(series),
        }


# ── GET /model-runs ──────────────────────────────────────────────


@app.get("/model-runs")
def get_model_runs(
    limit: int = Query(50, ge=1, le=200),
    model_name: str | None = Query(None),
    market_id: str | None = Query(None),
    instance_name: str | None = Query(None),
) -> list[dict[str, Any]]:
    """Recent model decisions with prediction data."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        query = (
            _instance_query(session, ModelRun, resolved_instance)
            .filter(ModelRun.timestamp >= DISPLAY_CUTOFF_UTC)
            .order_by(ModelRun.timestamp.desc())
        )
        if model_name:
            query = query.filter(ModelRun.model_name == model_name)
        if market_id:
            query = query.filter(ModelRun.market_id == market_id)
        rows = query.limit(limit).all()

        # Bulk-load prediction skip reasons so constraint-based skips
        # (e.g. "Market unchanged: 5¢ since last forecast") are surfaced.
        run_market_ids = list({r.market_id for r in rows})
        pred_skip_by_market_ts: dict[tuple[str, str], str] = {}
        if run_market_ids:
            preds_with_skip = (
                _instance_query(session, BettingPrediction, resolved_instance)
                .filter(
                    BettingPrediction.market_id.in_(run_market_ids),
                    BettingPrediction.skip_reason.isnot(None),
                    BettingPrediction.created_at >= DISPLAY_CUTOFF_UTC,
                )
                .all()
            )
            for p in preds_with_skip:
                # Key by (market_id, source) with latest timestamp winning
                key = (p.market_id, p.source)
                ts_key = p.created_at.isoformat()
                pred_skip_by_market_ts[(p.market_id, ts_key)] = p.skip_reason

        results = []
        for row in rows:
            p_yes = None
            reasoning = None
            skip_reason = None
            sources = []
            models_breakdown = None
            if row.metadata_json:
                try:
                    meta = json.loads(row.metadata_json)
                    p_yes = meta.get("p_yes")
                    reasoning = meta.get("reasoning")
                    skip_reason = meta.get("skip_reason")
                    sources = meta.get("sources", [])
                    models_breakdown = meta.get("models")
                except (json.JSONDecodeError, TypeError):
                    pass

            # Check for constraint skip reason from BettingPrediction
            # Match by market_id and close timestamp (within 60s)
            if not skip_reason:
                run_ts = row.timestamp
                for (mid, ts_str), reason in pred_skip_by_market_ts.items():
                    if mid != row.market_id:
                        continue
                    pred_ts = datetime.fromisoformat(ts_str)
                    if abs((run_ts - pred_ts).total_seconds()) < 60:
                        skip_reason = reason
                        break

            effective_reasoning = reasoning or skip_reason
            entry = {
                "id": row.id,
                "model_name": row.model_name,
                "timestamp": row.timestamp.isoformat(),
                "decision": row.decision,
                "confidence": row.confidence,
                "market_id": row.market_id,
                "p_yes": p_yes,
                "reasoning": effective_reasoning,
                "sources": sources,
            }
            if models_breakdown:
                entry["models"] = models_breakdown
            results.append(entry)
        return results


# ── GET /system-logs ─────────────────────────────────────────────


@app.get("/system-logs")
def get_system_logs(
    limit: int = Query(50, ge=1, le=200),
    level: str | None = Query(None),
    instance_name: str | None = Query(None),
) -> list[dict[str, Any]]:
    """Recent system logs."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    with get_session(engine) as session:
        query = _instance_query(session, SystemLog, resolved_instance).order_by(SystemLog.created_at.desc())
        if level:
            query = query.filter(SystemLog.level == level)
        rows = query.limit(limit).all()
        return [
            {
                "id": row.id,
                "level": row.level,
                "message": row.message,
                "component": row.component,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


# ── GET /kalshi/balance ──────────────────────────────────────────


@app.get("/kalshi/balance")
def get_kalshi_balance(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Return the latest recorded Kalshi balance snapshot for the instance."""
    try:
        resolved_instance = _instance_name(instance_name)
        dry_run = _instance_bool_setting("LIVE_BETTING_DRY_RUN", resolved_instance, True)
        engine = get_db()
        with get_session(engine) as session:
            snapshot = get_latest_balance_snapshot(session, resolved_instance)

        if snapshot is None:
            adapter = _build_kalshi_adapter(resolved_instance)
            try:
                raw = adapter.get_balance_details()
            finally:
                adapter.close()
            balance = float(raw.get("balance", 0)) / 100.0
            portfolio_value = float(raw.get("portfolio_value", 0)) / 100.0
            timestamp = datetime.now(UTC).isoformat()
        else:
            balance = snapshot.balance
            portfolio_value = snapshot.portfolio_value
            timestamp = snapshot.snapshot_ts.isoformat()

        return {
            "balance": balance,
            "portfolio_value": portfolio_value,
            "dry_run": dry_run,
            "instance_name": resolved_instance,
            "timestamp": timestamp,
        }
    except Exception as e:
        logger.error("Failed to fetch Kalshi balance: %s", e)
        return {
            "balance": 0.0,
            "portfolio_value": 0.0,
            "dry_run": True,
            "error": str(e),
            "instance_name": _instance_name(instance_name),
            "timestamp": datetime.now(UTC).isoformat(),
        }


@app.get("/display-baseline")
def get_display_baseline(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Return the post-cutoff starting bankroll baseline for one instance."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()

    with get_session(engine) as session:
        baseline = _display_baseline_for_instance(session, resolved_instance)

    return {
        **baseline,
        "cutoff_timestamp": DISPLAY_CUTOFF_UTC.isoformat(),
        "cutoff_label": DISPLAY_CUTOFF_LABEL,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── GET /kalshi/positions ────────────────────────────────────────


@app.get("/kalshi/positions")
def get_kalshi_positions(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Return the latest recorded Kalshi positions for the instance."""
    try:
        resolved_instance = _instance_name(instance_name)
        dry_run = _instance_bool_setting("LIVE_BETTING_DRY_RUN", resolved_instance, True)
        engine = get_db()
        with get_session(engine) as session:
            snapshots = get_latest_position_snapshots(session, resolved_instance)

        if snapshots:
            positions = [
                json.loads(snapshot.raw_json) if snapshot.raw_json else {
                    "ticker": snapshot.ticker,
                    "position_fp": snapshot.signed_quantity,
                    "market_exposure_dollars": snapshot.market_exposure,
                    "realized_pnl_dollars": snapshot.realized_pnl,
                    "fees_paid_dollars": snapshot.fees_paid,
                    "resting_orders_count": snapshot.resting_orders_count,
                }
                for snapshot in snapshots.values()
            ]
            timestamp = max(snapshot.snapshot_ts for snapshot in snapshots.values()).isoformat()
        else:
            adapter = _build_kalshi_adapter(resolved_instance)
            try:
                positions = adapter.get_positions()
            finally:
                adapter.close()
            timestamp = datetime.now(UTC).isoformat()

        return {
            "positions": positions,
            "dry_run": dry_run,
            "instance_name": resolved_instance,
            "timestamp": timestamp,
        }
    except Exception as e:
        logger.error("Failed to fetch Kalshi positions: %s", e)
        return {
            "positions": [],
            "dry_run": True,
            "error": str(e),
            "instance_name": _instance_name(instance_name),
            "timestamp": datetime.now(UTC).isoformat(),
        }


# ── GET /comparison-models ───────────────────────────────────────

COMPARISON_INSTANCE_NAMES = ["GPT5", "Grok4", "Opus46"]
COMPARISON_MODEL_LABELS: dict[str, str] = {
    "GPT5": "GPT-5.4",
    "Grok4": "Grok 4",
    "Opus46": "Claude Opus 4.6",
}


@app.get("/comparison-models")
def get_comparison_models() -> dict[str, Any]:
    """Summary of comparison model dry-run instances (GPT-5.4, Grok 4, Opus 4.6).

    Returns balance, P&L, trade count, win rate, and last update for each model.
    These instances always run in dry-run mode for performance benchmarking.
    """
    engine = get_db()
    starting_cash = float(
        os.getenv("COMPARISON_STARTING_CASH", os.getenv("WORKER_STARTING_CASH", "500"))
    )
    results: dict[str, Any] = {}

    for inst in COMPARISON_INSTANCE_NAMES:
        try:
            with get_session(engine) as session:
                positions = (
                    session.query(TradingPosition)
                    .filter(TradingPosition.instance_name == inst)
                    .all()
                )
                capital_deployed = sum(p.avg_price * p.quantity for p in positions if p.quantity > 0)
                realized_pnl = sum(p.realized_pnl for p in positions)
                balance = starting_cash - capital_deployed + realized_pnl

                trade_count = (
                    session.query(func.count(BettingOrder.id))
                    .filter(
                        BettingOrder.instance_name == inst,
                        BettingOrder.status.in_(["FILLED", "DRY_RUN"]),
                    )
                    .scalar()
                    or 0
                )

                last_run = (
                    session.query(ModelRun)
                    .filter(ModelRun.instance_name == inst)
                    .order_by(ModelRun.timestamp.desc())
                    .first()
                )

                positions_with_pnl = [p for p in positions if p.realized_pnl != 0]
                winning = sum(1 for p in positions_with_pnl if p.realized_pnl > 0)
                win_rate = winning / len(positions_with_pnl) if positions_with_pnl else 0.0

                results[inst] = {
                    "instance_name": inst,
                    "model_label": COMPARISON_MODEL_LABELS.get(inst, inst),
                    "balance": round(balance, 2),
                    "total_pnl": round(realized_pnl, 4),
                    "starting_cash": starting_cash,
                    "trade_count": trade_count,
                    "open_positions": len([p for p in positions if p.quantity > 0]),
                    "win_rate": round(win_rate, 4),
                    "last_updated": last_run.timestamp.isoformat() if last_run else None,
                }
        except Exception as e:
            logger.error("Failed to get comparison data for %s: %s", inst, e)
            results[inst] = {
                "instance_name": inst,
                "model_label": COMPARISON_MODEL_LABELS.get(inst, inst),
                "balance": starting_cash,
                "total_pnl": 0.0,
                "starting_cash": starting_cash,
                "trade_count": 0,
                "open_positions": 0,
                "win_rate": 0.0,
                "last_updated": None,
                "error": str(e),
            }

    return {"models": results, "timestamp": datetime.now(UTC).isoformat()}


# ── DELETE /data/clear ───────────────────────────────────────────


@app.delete("/data/clear")
def clear_all_data(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Clear all trading data from the database."""
    resolved_instance = _instance_name(instance_name)
    engine = get_db()
    deleted = {}
    with get_session(engine) as session:
        deleted["betting_orders"] = _instance_query(session, BettingOrder, resolved_instance).delete()
        deleted["betting_signals"] = _instance_query(session, BettingSignal, resolved_instance).delete()
        deleted["betting_predictions"] = _instance_query(session, BettingPrediction, resolved_instance).delete()
        deleted["trading_positions"] = _instance_query(session, TradingPosition, resolved_instance).delete()
        deleted["trading_markets"] = _instance_query(session, TradingMarket, resolved_instance).delete()
        deleted["kalshi_balance_snapshots"] = _instance_query(session, KalshiBalanceSnapshot, resolved_instance).delete()
        deleted["kalshi_position_snapshots"] = _instance_query(session, KalshiPositionSnapshot, resolved_instance).delete()
        deleted["kalshi_order_snapshots"] = _instance_query(session, KalshiOrderSnapshot, resolved_instance).delete()
        deleted["model_runs"] = _instance_query(session, ModelRun, resolved_instance).delete()
        deleted["system_logs"] = _instance_query(session, SystemLog, resolved_instance).delete()
        session.commit()

    return {
        "status": "cleared",
        "instance_name": resolved_instance,
        "deleted": deleted,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── GET /order-monitoring ─────────────────────────────────────────


@app.get("/order-monitoring")
def get_order_monitoring(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Get order monitoring data for edge case detection.

    Returns:
    - Pending orders with age
    - Stale orders (pending > 1 hour)
    - Recent cancellations
    - Order status breakdown
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()

    now = datetime.now(UTC)
    one_hour_ago = now - timedelta(hours=1)

    with get_session(engine) as session:
        latest_orders = [
            row for row in get_latest_order_snapshots(session, resolved_instance)
            if _display_visible_ts(row.created_ts or row.last_update_ts or row.captured_at)
        ]
        if not latest_orders:
            latest_orders = []
        pending_orders = [o for o in latest_orders if o.status == "PENDING" and o.remaining_count > 0]
        stale_orders = [
            o for o in pending_orders
            if (o.created_ts or o.last_update_ts or o.captured_at) < one_hour_ago
        ]
        cancelled_orders = [
            o for o in latest_orders
            if o.status == "CANCELLED" and (o.last_update_ts or o.created_ts or o.captured_at) >= now - timedelta(hours=24)
        ][:20]

        status_breakdown: dict[str, int] = defaultdict(int)
        for order in latest_orders:
            status_breakdown[order.status] += 1

        # Get market titles for context
        market_titles = {}
        for order in pending_orders:
            if order.ticker not in market_titles:
                market = (
                    _instance_query(session, TradingMarket, resolved_instance)
                    .filter(TradingMarket.ticker == order.ticker)
                    .first()
                )
                if market:
                    market_titles[order.ticker] = market.title or order.ticker
                else:
                    market_titles[order.ticker] = order.ticker

        # Format pending orders with age
        pending_list = []
        for order in pending_orders:
            created_at = order.created_ts or order.last_update_ts or order.captured_at
            age_minutes = (now - created_at).total_seconds() / 60
            filled = order.fill_count or 0
            pending_list.append({
                "order_id": order.client_order_id or order.order_id,
                "ticker": order.ticker,
                "market_title": market_titles.get(order.ticker, order.ticker),
                "side": order.side,
                "count": order.initial_count,
                "filled_shares": filled,
                "price_cents": int(round((order.limit_price or 0.0) * 100)),
                "created_at": created_at.isoformat(),
                "age_minutes": round(age_minutes, 1),
                "is_stale": age_minutes > 60,
            })

        # Format stale orders
        stale_list = []
        for order in stale_orders:
            created_at = order.created_ts or order.last_update_ts or order.captured_at
            age_minutes = (now - created_at).total_seconds() / 60
            stale_list.append({
                "order_id": order.client_order_id or order.order_id,
                "ticker": order.ticker,
                "side": order.side,
                "count": order.initial_count,
                "created_at": created_at.isoformat(),
                "age_hours": round(age_minutes / 60, 1),
            })

        # Format recent cancellations
        cancelled_list = []
        for order in cancelled_orders:
            created_at = order.created_ts or order.last_update_ts or order.captured_at
            cancelled_list.append({
                "order_id": order.client_order_id or order.order_id,
                "ticker": order.ticker,
                "side": order.side,
                "count": order.initial_count,
                "created_at": created_at.isoformat(),
            })

    return {
        "instance_name": resolved_instance,
        "pending_orders": pending_list,
        "stale_orders": stale_list,
        "recent_cancellations": cancelled_list,
        "status_breakdown": dict(status_breakdown),
        "alert_level": "critical" if len(stale_list) > 5 else "warning" if len(stale_list) > 0 else "ok",
        "timestamp": now.isoformat(),
    }


# ── GET /system-alerts ────────────────────────────────────────────


@app.get("/system-alerts")
def get_system_alerts(instance_name: str | None = Query(None), hours: int = Query(24)) -> dict[str, Any]:
    """Get recent system alerts and warnings.

    Includes:
    - Position drift alerts
    - Order management warnings
    - System errors
    """
    resolved_instance = _instance_name(instance_name)
    engine = get_db()

    cutoff = datetime.now(UTC) - timedelta(hours=hours)

    with get_session(engine) as session:
        # Get ALERT level logs
        alerts = (
            _instance_query(session, SystemLog, resolved_instance)
            .filter(
                SystemLog.level == "ALERT",
                SystemLog.created_at >= cutoff,
            )
            .order_by(SystemLog.created_at.desc())
            .limit(50)
            .all()
        )

        # Get ERROR level logs
        errors = (
            _instance_query(session, SystemLog, resolved_instance)
            .filter(
                SystemLog.level == "ERROR",
                SystemLog.created_at >= cutoff,
            )
            .order_by(SystemLog.created_at.desc())
            .limit(50)
            .all()
        )

        # Format alerts
        alert_list = []
        for log in alerts:
            alert_list.append({
                "id": log.id,
                "message": log.message,
                "component": log.component,
                "created_at": log.created_at.isoformat(),
            })

        # Format errors
        error_list = []
        for log in errors:
            error_list.append({
                "id": log.id,
                "message": log.message,
                "component": log.component,
                "created_at": log.created_at.isoformat(),
            })

    return {
        "instance_name": resolved_instance,
        "alerts": alert_list,
        "errors": error_list,
        "alert_count": len(alert_list),
        "error_count": len(error_list),
        "has_critical_alerts": len(alert_list) > 0,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/strategy-marker")
def get_strategy_marker(instance_name: str | None = Query(None)) -> dict[str, Any]:
    """Get current strategy marker and transition information."""
    resolved_instance = _instance_name(instance_name)

    engine = get_db()
    with get_session(engine) as session:
        # Import here to avoid circular dependency
        try:
            from services.strategy_marker import get_current_strategy, get_strategy_pnl
        except ImportError:
            # If strategy marker module doesn't exist yet, return placeholder
            return {
                "instance_name": resolved_instance,
                "strategy": {
                    "version": "v1.0-legacy",
                    "name": "Legacy Strategy",
                    "start_timestamp": DISPLAY_CUTOFF_UTC.isoformat(),
                    "constraints": {
                        "pre_resolution_block_hours": 24,
                        "min_hours_between_trades": None,
                        "min_price_deviation_cents": None,
                        "spread_filter": 1.03,
                    }
                },
                "timestamp": datetime.now(UTC).isoformat(),
            }

        current = get_current_strategy(session, resolved_instance)
        if current:
            pnl_data = get_strategy_pnl(session, resolved_instance)
            return {
                "instance_name": resolved_instance,
                "strategy": {
                    "id": current["id"],
                    "version": current["version"],
                    "start_timestamp": current["start_timestamp"].isoformat() if current["start_timestamp"] else None,
                    "config": current["config"],
                    "starting_balance": current["starting_balance"],
                    "notes": current["notes"],
                },
                "pnl": pnl_data if "error" not in pnl_data else {},
                "timestamp": datetime.now(UTC).isoformat(),
            }

        # No active strategy
        return {
            "instance_name": resolved_instance,
            "strategy": None,
            "pnl": {},
            "timestamp": datetime.now(UTC).isoformat(),
        }


@app.post("/strategy-marker/transition")
def create_strategy_transition(
    instance_name: str | None = Query(None),
    notes: str | None = Query(None)
) -> dict[str, Any]:
    """Mark a strategy transition point."""
    resolved_instance = _instance_name(instance_name)

    engine = get_db()
    with get_session(engine) as session:
        try:
            from services.strategy_marker import mark_strategy_transition, CURRENT_STRATEGY_VERSION

            marker_id = mark_strategy_transition(
                session,
                resolved_instance,
                new_version=CURRENT_STRATEGY_VERSION,
                notes=notes
            )

            return {
                "success": True,
                "marker_id": marker_id,
                "instance_name": resolved_instance,
                "version": CURRENT_STRATEGY_VERSION,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "instance_name": resolved_instance,
                "timestamp": datetime.now(UTC).isoformat(),
            }
