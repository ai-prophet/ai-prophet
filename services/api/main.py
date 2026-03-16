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
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from dotenv import load_dotenv

# Ensure project root is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()

import logging

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, text

logger = logging.getLogger(__name__)

from ai_prophet_core.betting.db import create_db_engine, get_session
from ai_prophet_core.betting.db_schema import (
    BettingOrder,
    BettingPrediction,
    BettingSignal,
)

# Import dashboard-specific models
from db_models import (
    MarketPriceSnapshot,
    ModelRun,
    SystemLog,
    TradingMarket,
    TradingPosition,
)

# ── App setup ─────────────────────────────────────────────────────

_db_engine = None


def get_db():
    global _db_engine
    if _db_engine is None:
        _db_engine = create_db_engine()
    return _db_engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: verify DB connection
    try:
        engine = get_db()
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
cors_origins = os.getenv("API_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ──────────────────────────────────────────────────────


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    """Safe division that returns *default* when denominator is zero or near-zero."""
    if abs(denominator) < 1e-12:
        return default
    return numerator / denominator


def _build_pred_by_signal(
    session, signal_ids: list[int]
) -> dict[int, BettingPrediction]:
    """Build a mapping of signal_id -> BettingPrediction."""
    pred_by_signal: dict[int, BettingPrediction] = {}
    if not signal_ids:
        return pred_by_signal
    signals = (
        session.query(BettingSignal)
        .filter(BettingSignal.id.in_(signal_ids))
        .all()
    )
    pred_ids = [s.prediction_id for s in signals if s.prediction_id]
    preds: dict[int, BettingPrediction] = {}
    if pred_ids:
        for p in (
            session.query(BettingPrediction)
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
def health() -> dict[str, Any]:
    """System health: DB status, last worker heartbeat, trading mode."""
    engine = get_db()
    db_ok = False
    try:
        with get_session(engine) as session:
            session.execute(text("SELECT 1"))
            db_ok = True
    except Exception:
        pass

    # Last worker heartbeat
    last_heartbeat = None
    worker_status = "unknown"
    try:
        with get_session(engine) as session:
            row = (
                session.query(SystemLog)
                .filter(SystemLog.level == "HEARTBEAT", SystemLog.component == "worker")
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            if row:
                last_heartbeat = row.created_at.isoformat()
                age_sec = (datetime.now(UTC) - row.created_at.replace(tzinfo=UTC)).total_seconds()
                worker_status = "healthy" if age_sec < 1800 else "stale"
    except Exception:
        pass

    # Find last cycle_end heartbeat for countdown timer
    last_cycle_end = None
    try:
        with get_session(engine) as session:
            row = (
                session.query(SystemLog)
                .filter(
                    SystemLog.level == "HEARTBEAT",
                    SystemLog.component == "worker",
                    SystemLog.message == "cycle_end",
                )
                .order_by(SystemLog.created_at.desc())
                .first()
            )
            if row:
                last_cycle_end = row.created_at.isoformat()
    except Exception:
        pass

    dry_run = os.getenv("LIVE_BETTING_DRY_RUN", "true").lower() in ("true", "1", "yes")
    enabled = os.getenv("LIVE_BETTING_ENABLED", "false").lower() in ("true", "1", "yes")
    poll_interval = int(os.getenv("WORKER_POLL_INTERVAL_SEC", "900"))

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
        "worker": worker_status,
        "last_heartbeat": last_heartbeat,
        "last_cycle_end": last_cycle_end,
        "poll_interval_sec": poll_interval,
        "mode": "dry_run" if dry_run else "live",
        "betting_enabled": enabled,
        "timestamp": datetime.now(UTC).isoformat(),
    }


# ── GET /trades ───────────────────────────────────────────────────


@app.get("/trades")
def get_trades(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    """Recent trades (betting orders) with prediction context.

    Returns paginated results with total count.
    """
    engine = get_db()
    with get_session(engine) as session:
        total_count = session.query(func.count(BettingOrder.id)).scalar() or 0

        rows = (
            session.query(BettingOrder)
            .order_by(BettingOrder.created_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        results = []
        for row in rows:
            # Get the prediction for context (via signal -> prediction)
            prediction = None
            if row.signal_id:
                signal = session.query(BettingSignal).filter_by(id=row.signal_id).first()
                if signal and signal.prediction_id:
                    pred = session.query(BettingPrediction).filter_by(id=signal.prediction_id).first()
                    if pred:
                        prediction = {
                            "p_yes": pred.p_yes,
                            "yes_ask": pred.yes_ask,
                            "no_ask": pred.no_ask,
                            "source": pred.source,
                            "market_id": pred.market_id,
                        }

            # Look up market title
            market_title = None
            mkt = session.query(TradingMarket).filter_by(
                market_id=f"kalshi:{row.ticker}"
            ).first()
            if mkt:
                market_title = mkt.title

            results.append({
                "id": row.id,
                "order_id": row.order_id,
                "ticker": row.ticker,
                "action": row.action,
                "side": row.side,
                "count": row.count,
                "price_cents": row.price_cents,
                "status": row.status,
                "filled_shares": row.filled_shares,
                "fill_price": row.fill_price,
                "exchange_order_id": row.exchange_order_id,
                "dry_run": row.dry_run,
                "created_at": row.created_at.isoformat(),
                "prediction": prediction,
                "market_title": market_title,
            })

        return {
            "trades": results,
            "total": total_count,
            "has_more": (offset + limit) < total_count,
        }


# ── GET /markets ──────────────────────────────────────────────────


@app.get("/markets")
def get_markets(
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Markets currently being tracked, with latest model prediction."""
    engine = get_db()
    with get_session(engine) as session:
        rows = (
            session.query(TradingMarket)
            .order_by(TradingMarket.updated_at.desc())
            .limit(limit)
            .all()
        )
        results = []
        for row in rows:
            # Get all model runs from the latest cycle for this market
            # Find the most recent aggregated run to determine cycle timestamp
            latest_agg = (
                session.query(ModelRun)
                .filter(ModelRun.market_id == row.market_id,
                        ModelRun.model_name == "aggregated")
                .order_by(ModelRun.timestamp.desc())
                .first()
            )

            model_predictions = []
            aggregated_p_yes = None
            model_prediction = None  # backward-compat: latest single prediction

            if latest_agg:
                # Get all model runs from the same cycle.
                # Gemini calls can take 30-60s each, so with multiple models
                # the first model's run can be several minutes before the
                # aggregated run.
                cycle_start = latest_agg.timestamp - timedelta(seconds=600)
                cycle_end = latest_agg.timestamp + timedelta(seconds=5)
                cycle_runs = (
                    session.query(ModelRun)
                    .filter(ModelRun.market_id == row.market_id,
                            ModelRun.timestamp >= cycle_start,
                            ModelRun.timestamp <= cycle_end)
                    .order_by(ModelRun.timestamp.asc())
                    .all()
                )
                for run in cycle_runs:
                    p_yes = None
                    reasoning = None
                    models_breakdown = None
                    if run.metadata_json:
                        try:
                            meta = json.loads(run.metadata_json)
                            p_yes = meta.get("p_yes")
                            reasoning = meta.get("reasoning")
                            models_breakdown = meta.get("models")
                        except (json.JSONDecodeError, TypeError):
                            pass
                    pred = {
                        "model_name": run.model_name,
                        "decision": run.decision,
                        "confidence": run.confidence,
                        "p_yes": p_yes,
                        "timestamp": run.timestamp.isoformat(),
                    }
                    if run.model_name == "aggregated":
                        aggregated_p_yes = p_yes
                        pred["models"] = models_breakdown
                    else:
                        pred["reasoning"] = reasoning
                    model_predictions.append(pred)

                # backward-compat
                model_prediction = {
                    "model_name": "aggregated",
                    "decision": latest_agg.decision,
                    "confidence": latest_agg.confidence,
                    "p_yes": aggregated_p_yes,
                    "timestamp": latest_agg.timestamp.isoformat(),
                }
            else:
                # Fallback: no aggregated run yet, use latest individual run
                latest_run = (
                    session.query(ModelRun)
                    .filter(ModelRun.market_id == row.market_id)
                    .order_by(ModelRun.timestamp.desc())
                    .first()
                )
                if latest_run:
                    p_yes = None
                    if latest_run.metadata_json:
                        try:
                            meta = json.loads(latest_run.metadata_json)
                            p_yes = meta.get("p_yes")
                        except (json.JSONDecodeError, TypeError):
                            pass
                    model_prediction = {
                        "model_name": latest_run.model_name,
                        "decision": latest_run.decision,
                        "confidence": latest_run.confidence,
                        "p_yes": p_yes,
                        "timestamp": latest_run.timestamp.isoformat(),
                    }
                    model_predictions.append(model_prediction)
                    aggregated_p_yes = p_yes

            results.append({
                "id": row.id,
                "market_id": row.market_id,
                "ticker": row.ticker,
                "event_ticker": row.event_ticker,
                "title": row.title,
                "category": row.category,
                "expiration": row.expiration.isoformat() if row.expiration else None,
                "last_price": row.last_price,
                "yes_ask": row.yes_ask,
                "no_ask": row.no_ask,
                "volume_24h": row.volume_24h,
                "updated_at": row.updated_at.isoformat(),
                "model_prediction": model_prediction,
                "model_predictions": model_predictions,
                "aggregated_p_yes": aggregated_p_yes,
            })
        return results


# ── GET /positions ────────────────────────────────────────────────


@app.get("/positions")
def get_positions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    search: str | None = Query(None, description="Search by market title or ticker"),
) -> dict[str, Any]:
    """Current trading positions with market context (paginated)."""
    engine = get_db()
    with get_session(engine) as session:
        query = session.query(TradingPosition).order_by(TradingPosition.updated_at.desc())

        # Get total count before pagination
        total = query.count()

        rows = query.offset(offset).limit(limit).all()

        results = []
        for row in rows:
            market_title = None
            event_ticker = None
            ticker = None
            mkt = session.query(TradingMarket).filter_by(market_id=row.market_id).first()
            if mkt:
                market_title = mkt.title
                event_ticker = mkt.event_ticker
                ticker = mkt.ticker

            # Apply search filter after join (search on title or ticker)
            if search:
                needle = search.lower()
                if not (
                    (market_title and needle in market_title.lower())
                    or (ticker and needle in ticker.lower())
                    or (event_ticker and needle in event_ticker.lower())
                ):
                    total -= 1
                    continue

            results.append({
                "id": row.id,
                "market_id": row.market_id,
                "ticker": ticker,
                "event_ticker": event_ticker,
                "market_title": market_title,
                "contract": row.contract,
                "quantity": row.quantity,
                "avg_price": row.avg_price,
                "realized_pnl": row.realized_pnl,
                "unrealized_pnl": row.unrealized_pnl,
                "max_position": row.max_position,
                "realized_trades": row.realized_trades,
                "updated_at": row.updated_at.isoformat(),
            })
        return {
            "positions": results,
            "total": total,
            "has_more": offset + limit < total,
        }


# ── GET /pnl ─────────────────────────────────────────────────────


@app.get("/pnl")
def get_pnl(
    days: int = Query(30, ge=1, le=365),
    market_id: str | None = Query(None, description="Filter by market_id"),
    model: str | None = Query(None, description="Filter by model/source"),
) -> dict[str, Any]:
    """P&L data: cumulative P&L over time from filled orders.

    Returns combined, realized, and unrealized series plus trade markers.
    Supports filtering by market_id and model/source.
    """
    engine = get_db()
    with get_session(engine) as session:
        query = (
            session.query(BettingOrder)
            .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
            .order_by(BettingOrder.created_at.asc())
        )
        rows = query.all()

        # Build map: signal_id -> prediction (for historical prices at trade time)
        signal_ids = [r.signal_id for r in rows if r.signal_id]
        pred_by_signal = _build_pred_by_signal(session, signal_ids)

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

        # Current prices for final valuation
        current_prices: dict[str, dict[str, float | None]] = {}
        for mkt in session.query(TradingMarket).all():
            current_prices[mkt.ticker] = {
                "yes_ask": mkt.yes_ask,
                "no_ask": mkt.no_ask,
            }

        # Market titles for trade markers
        market_titles: dict[str, str] = {}
        for mkt in session.query(TradingMarket).all():
            market_titles[mkt.ticker] = mkt.title or ""

        ticker_positions: dict[str, dict[str, Any]] = {}
        pnl_series: list[dict[str, Any]] = []
        trade_markers: list[dict[str, Any]] = []
        total_trades = 0
        total_volume = 0.0
        cumulative_realized = 0.0
        # Running snapshot of market prices updated as we process each trade
        historical_prices: dict[str, dict[str, float | None]] = {}

        for row in rows:
            cost = (row.price_cents / 100.0) * row.count
            total_trades += 1
            total_volume += cost

            ticker = row.ticker
            side = row.side.lower()
            action = getattr(row, "action", "BUY") or "BUY"

            if ticker not in ticker_positions:
                ticker_positions[ticker] = {
                    "side": side,
                    "qty": 0,
                    "total_cost": 0.0,
                }

            pos = ticker_positions[ticker]

            # Track realized P&L for SELL orders
            pnl_impact = 0.0
            if action.upper() == "SELL" and pos["qty"] > 0:
                avg_cost_per_share = _safe_div(pos["total_cost"], pos["qty"])
                sell_proceeds = (row.price_cents / 100.0) * row.count
                cost_basis = avg_cost_per_share * row.count
                pnl_impact = sell_proceeds - cost_basis
                cumulative_realized += pnl_impact
                pos["qty"] -= row.count
                pos["total_cost"] -= cost_basis
            else:
                pos["qty"] += row.count
                pos["total_cost"] += cost
                pos["side"] = side

            # Update price snapshot from this trade's prediction
            pred = pred_by_signal.get(row.signal_id)
            if pred:
                pred_ticker = pred.market_id
                if pred_ticker.startswith("kalshi:"):
                    pred_ticker = pred_ticker[len("kalshi:"):]
                historical_prices[pred_ticker] = {
                    "yes_ask": pred.yes_ask,
                    "no_ask": pred.no_ask,
                }

            # Compute portfolio unrealized P&L using prices known at this point
            cumulative_unrealized = 0.0
            for t, p in ticker_positions.items():
                if p["qty"] <= 0:
                    continue
                # Use current market prices (most accurate), fall back to historical
                prices = current_prices.get(t) or historical_prices.get(t)
                if prices:
                    price_key = f"{p['side']}_ask"
                    mkt_price = prices.get(price_key)
                    if mkt_price is not None:
                        cumulative_unrealized += mkt_price * p["qty"] - p["total_cost"]

            cumulative_pnl = cumulative_realized + cumulative_unrealized

            ts = row.created_at.isoformat()

            # Embed realized and unrealized directly in each series point
            pnl_series.append({
                "timestamp": ts,
                "pnl": round(cumulative_pnl, 4),
                "realized_pnl": round(cumulative_realized, 4),
                "unrealized_pnl": round(cumulative_unrealized, 4),
                "trade_cost": round(cost, 4),
                "ticker": row.ticker,
                "side": row.side,
                "action": action,
            })

            trade_markers.append({
                "timestamp": ts,
                "ticker": row.ticker,
                "side": row.side,
                "action": action,
                "count": row.count,
                "price_cents": row.price_cents,
                "pnl_impact": round(pnl_impact, 4),
            })

        # Position-level P&L for summary (most accurate / source of truth)
        positions = session.query(TradingPosition).all()
        total_realized = sum(p.realized_pnl for p in positions)
        total_unrealized = sum(p.unrealized_pnl for p in positions)
        total_pnl = total_realized + total_unrealized

        # Correct the final series point to match position-based P&L
        # (transaction replay can drift from update_positions due to rounding)
        if pnl_series:
            pnl_series[-1]["pnl"] = round(total_pnl, 4)
            pnl_series[-1]["realized_pnl"] = round(total_realized, 4)
            pnl_series[-1]["unrealized_pnl"] = round(total_unrealized, 4)

        return {
            "series": pnl_series,
            "trade_markers": trade_markers,
            "summary": {
                "total_pnl": round(total_pnl, 4),
                "total_trades": total_trades,
                "total_volume": round(total_volume, 4),
                "active_positions": len(positions),
            },
        }


# ── GET /analytics/summary ──────────────────────────────────────


@app.get("/analytics/summary")
def get_analytics_summary() -> dict[str, Any]:
    """Comprehensive trading analytics: risk metrics, win rate, model/market breakdowns."""
    engine = get_db()
    with get_session(engine) as session:
        # Fetch all filled orders
        orders = (
            session.query(BettingOrder)
            .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
            .order_by(BettingOrder.created_at.asc())
            .all()
        )

        # Fetch positions for realized/unrealized PnL
        positions = session.query(TradingPosition).all()
        # Fetch market info
        markets = session.query(TradingMarket).all()
        market_by_id: dict[str, TradingMarket] = {m.market_id: m for m in markets}

        # ── Per-trade P&L computation ─────────────────────────────
        # Use TradingPosition as the source of truth for per-market P&L
        # (consistent with the position heatmap).
        trade_pnls: list[float] = []
        pnl_by_model: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "wins": 0}
        )
        pnl_by_market: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"pnl": 0.0, "trades": 0, "title": ""}
        )

        # Build per-model edge contributions from aggregated ModelRun records.
        # Each aggregated run stores {"models": {model_spec: {"edge": ...}}}
        # so we can attribute P&L proportionally by edge fraction.
        model_edge_fractions: dict[str, dict[str, float]] = {}  # market_id -> {model: fraction}
        try:
            agg_runs = (
                session.query(ModelRun)
                .filter(ModelRun.model_name == "aggregated")
                .order_by(ModelRun.timestamp.desc())
                .all()
            )
            # Keep only the latest run per market
            latest_by_market: dict[str, ModelRun] = {}
            for run in agg_runs:
                if run.market_id not in latest_by_market:
                    latest_by_market[run.market_id] = run

            for market_id, run in latest_by_market.items():
                if not run.metadata_json:
                    continue
                try:
                    meta = json.loads(run.metadata_json)
                    models_breakdown = meta.get("models", {})
                    if not models_breakdown:
                        continue
                    total_abs_edge = sum(
                        abs(m.get("edge", 0)) for m in models_breakdown.values()
                    )
                    if total_abs_edge > 0:
                        model_edge_fractions[market_id] = {
                            name: abs(m.get("edge", 0)) / total_abs_edge
                            for name, m in models_breakdown.items()
                        }
                except (json.JSONDecodeError, TypeError):
                    continue
        except Exception:
            pass

        # Count orders per market for trade counts
        orders_per_market: dict[str, int] = defaultdict(int)
        for o in orders:
            orders_per_market[f"kalshi:{o.ticker}"] += 1

        for pos in positions:
            market_key = pos.market_id
            mkt = market_by_id.get(market_key)
            total_pnl = pos.realized_pnl + pos.unrealized_pnl
            trade_count = orders_per_market.get(market_key, 0)
            trade_pnls.append(total_pnl)

            # Per-market attribution
            pnl_by_market[market_key]["pnl"] = total_pnl
            pnl_by_market[market_key]["trades"] = trade_count
            pnl_by_market[market_key]["title"] = mkt.title if mkt else ""

            # Per-model attribution: distribute P&L by edge fraction
            fractions = model_edge_fractions.get(market_key, {})
            if fractions:
                for model_name, frac in fractions.items():
                    model_pnl = total_pnl * frac
                    pnl_by_model[model_name]["pnl"] += model_pnl
                    pnl_by_model[model_name]["trades"] += 1
                    if model_pnl > 0:
                        pnl_by_model[model_name]["wins"] += 1
            else:
                # Fallback: attribute to "aggregated" if no breakdown available
                pnl_by_model["aggregated"]["pnl"] += total_pnl
                pnl_by_model["aggregated"]["trades"] += 1
                if total_pnl > 0:
                    pnl_by_model["aggregated"]["wins"] += 1

        # ── Risk metrics ──────────────────────────────────────────
        total_trades = len(trade_pnls)
        winning_trades = sum(1 for p in trade_pnls if p > 0)
        losing_trades = sum(1 for p in trade_pnls if p < 0)
        win_rate = _safe_div(winning_trades, total_trades)

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
            cost = (o.price_cents / 100.0) * o.count
            if action.upper() == "SELL":
                daily_pnl[day_key] += cost  # sell proceeds
            else:
                daily_pnl[day_key] -= cost  # buy cost

        daily_returns = list(daily_pnl.values()) if daily_pnl else []

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
        peak = 0.0
        max_drawdown = 0.0
        max_drawdown_pct = 0.0
        for pnl in trade_pnls:
            cumulative += pnl
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_pct = _safe_div(dd, peak) if peak > 0 else 0.0

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
                "win_rate": round(_safe_div(data["wins"], data["trades"]), 4),
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

        return {
            "sharpe_ratio": sharpe_ratio,
            "max_drawdown": round(max_drawdown, 4),
            "max_drawdown_pct": round(max_drawdown_pct, 4),
            "volatility": volatility,
            "sortino_ratio": sortino_ratio,
            "profit_factor": round(profit_factor, 4),
            "win_rate": round(win_rate, 4),
            "avg_win": round(avg_win, 4),
            "avg_loss": round(avg_loss, 4),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "pnl_by_model": formatted_pnl_by_model,
            "pnl_by_market": formatted_pnl_by_market,
            "today_pnl": round(today_pnl, 4),
            "total_exposure": round(total_exposure, 4),
        }


# ── GET /analytics/model-calibration ────────────────────────────


@app.get("/analytics/model-calibration")
def get_model_calibration(
    model_name: str | None = Query(None, description="Filter by model name"),
    bins: int = Query(10, ge=2, le=50, description="Number of calibration bins"),
) -> dict[str, Any]:
    """Model calibration analysis: compare predicted probabilities with outcomes.

    For resolved markets (expired with last_price of 0 or 1), compares
    model p_yes predictions against actual outcomes.
    """
    engine = get_db()
    with get_session(engine) as session:
        # Get resolved markets: expired and last_price is 0 or 1
        resolved_markets = (
            session.query(TradingMarket)
            .filter(
                TradingMarket.expiration < datetime.now(UTC),
                TradingMarket.last_price.in_([0.0, 1.0]),
            )
            .all()
        )
        resolved_map: dict[str, float] = {
            m.market_id: m.last_price for m in resolved_markets
        }

        if not resolved_map:
            return {
                "calibration": [],
                "brier_score": 0.0,
                "models": [],
                "by_model": {},
            }

        # Get predictions for resolved markets
        pred_query = session.query(BettingPrediction).filter(
            BettingPrediction.market_id.in_(list(resolved_map.keys()))
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
            "models": all_models,
            "by_model": by_model,
        }


# ── GET /alerts ──────────────────────────────────────────────────


@app.get("/alerts")
def get_alerts() -> dict[str, Any]:
    """Active system alerts: stale workers, exposure limits, model divergences, errors."""
    engine = get_db()
    alerts: list[dict[str, Any]] = []
    now = datetime.now(UTC)

    with get_session(engine) as session:
        # 1. Stale worker check: heartbeat > 30 min
        last_heartbeat = (
            session.query(SystemLog)
            .filter(SystemLog.level == "HEARTBEAT", SystemLog.component == "worker")
            .order_by(SystemLog.created_at.desc())
            .first()
        )
        if last_heartbeat:
            age_sec = (now - last_heartbeat.created_at.replace(tzinfo=UTC)).total_seconds()
            if age_sec > 1800:
                minutes_ago = int(age_sec / 60)
                alerts.append({
                    "type": "stale_worker",
                    "severity": "error",
                    "message": f"Worker heartbeat is {minutes_ago} minutes old. Last seen: {last_heartbeat.created_at.isoformat()}",
                    "timestamp": now.isoformat(),
                })
        else:
            alerts.append({
                "type": "stale_worker",
                "severity": "error",
                "message": "No worker heartbeat found. Worker may have never started.",
                "timestamp": now.isoformat(),
            })

        # 2. Total exposure check
        positions = session.query(TradingPosition).all()
        total_exposure = sum(
            p.quantity * p.avg_price for p in positions if p.quantity > 0
        )
        exposure_threshold = float(os.getenv("ALERT_EXPOSURE_THRESHOLD", "500"))
        if total_exposure > exposure_threshold:
            alerts.append({
                "type": "exposure",
                "severity": "warning",
                "message": f"Total exposure ${total_exposure:.2f} exceeds threshold ${exposure_threshold:.2f}",
                "timestamp": now.isoformat(),
            })

        # 3. Model-market divergence > 20pp
        markets = session.query(TradingMarket).all()
        for mkt in markets:
            if mkt.yes_ask is None:
                continue
            # Get latest prediction for this market
            latest_pred = (
                session.query(BettingPrediction)
                .filter(BettingPrediction.market_id == mkt.market_id)
                .order_by(BettingPrediction.created_at.desc())
                .first()
            )
            if latest_pred:
                divergence = abs(latest_pred.p_yes - mkt.yes_ask)
                if divergence > 0.20:
                    alerts.append({
                        "type": "divergence",
                        "severity": "warning",
                        "message": (
                            f"Model-market divergence of {divergence:.0%} on "
                            f"'{mkt.title}': model={latest_pred.p_yes:.2f}, "
                            f"market={mkt.yes_ask:.2f}"
                        ),
                        "market_id": mkt.market_id,
                        "timestamp": now.isoformat(),
                    })

        # 4. Recent ERROR logs (last 1 hour)
        one_hour_ago = now - timedelta(hours=1)
        error_logs = (
            session.query(SystemLog)
            .filter(
                SystemLog.level == "ERROR",
                SystemLog.created_at >= one_hour_ago,
            )
            .order_by(SystemLog.created_at.desc())
            .limit(10)
            .all()
        )
        for log in error_logs:
            alerts.append({
                "type": "system_error",
                "severity": "error",
                "message": f"[{log.component}] {log.message}",
                "timestamp": log.created_at.isoformat(),
            })

    return {"alerts": alerts}


# ── GET /predictions/{market_id} ─────────────────────────────────


@app.get("/predictions/{market_id}")
def get_predictions(
    market_id: str,
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    """Time series of predictions and prices for a specific market."""
    engine = get_db()
    with get_session(engine) as session:
        # Get predictions for this market
        predictions = (
            session.query(BettingPrediction)
            .filter(BettingPrediction.market_id == market_id)
            .order_by(BettingPrediction.created_at.asc())
            .limit(limit)
            .all()
        )

        # Get price snapshots for this market
        snapshots = (
            session.query(MarketPriceSnapshot)
            .filter(MarketPriceSnapshot.market_id == market_id)
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
) -> dict[str, Any]:
    """Time series of price snapshots from MarketPriceSnapshot table."""
    engine = get_db()
    with get_session(engine) as session:
        snapshots = (
            session.query(MarketPriceSnapshot)
            .filter(MarketPriceSnapshot.market_id == market_id)
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
) -> list[dict[str, Any]]:
    """Recent model decisions with prediction data."""
    engine = get_db()
    with get_session(engine) as session:
        query = session.query(ModelRun).order_by(ModelRun.timestamp.desc())
        if model_name:
            query = query.filter(ModelRun.model_name == model_name)
        if market_id:
            query = query.filter(ModelRun.market_id == market_id)
        rows = query.limit(limit).all()
        results = []
        for row in rows:
            p_yes = None
            reasoning = None
            models_breakdown = None
            if row.metadata_json:
                try:
                    meta = json.loads(row.metadata_json)
                    p_yes = meta.get("p_yes")
                    reasoning = meta.get("reasoning")
                    models_breakdown = meta.get("models")
                except (json.JSONDecodeError, TypeError):
                    pass
            entry = {
                "id": row.id,
                "model_name": row.model_name,
                "timestamp": row.timestamp.isoformat(),
                "decision": row.decision,
                "confidence": row.confidence,
                "market_id": row.market_id,
                "p_yes": p_yes,
                "reasoning": reasoning,
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
) -> list[dict[str, Any]]:
    """Recent system logs."""
    engine = get_db()
    with get_session(engine) as session:
        query = session.query(SystemLog).order_by(SystemLog.created_at.desc())
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
def get_kalshi_balance() -> dict[str, Any]:
    """Fetch Kalshi account balance (works in both live and dry-run modes).

    In dry-run mode, computes a virtual balance:
      kalshi_balance - capital_deployed + realized_pnl
    so the balance decreases when capital is deployed and increases
    when gains are realized.
    """
    try:
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter

        dry_run = os.getenv("LIVE_BETTING_DRY_RUN", "true").lower() in ("true", "1", "yes")
        adapter = KalshiAdapter(dry_run=dry_run)
        real_balance = float(adapter.get_balance())
        adapter.close()

        if dry_run:
            db_engine = get_db()
            with get_session(db_engine) as session:
                positions = session.query(TradingPosition).all()
                capital_deployed = sum(p.avg_price * p.quantity for p in positions)
                realized_pnl = sum(p.realized_pnl for p in positions)
            balance = real_balance - capital_deployed + realized_pnl
        else:
            balance = real_balance

        return {
            "balance": balance,
            "dry_run": dry_run,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error("Failed to fetch Kalshi balance: %s", e)
        return {
            "balance": 0.0,
            "dry_run": True,
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat(),
        }


# ── GET /kalshi/positions ────────────────────────────────────────


@app.get("/kalshi/positions")
def get_kalshi_positions() -> dict[str, Any]:
    """Fetch live Kalshi positions (works in both live and dry-run modes)."""
    try:
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter

        dry_run = os.getenv("LIVE_BETTING_DRY_RUN", "true").lower() in ("true", "1", "yes")
        adapter = KalshiAdapter(dry_run=dry_run)
        positions = adapter.get_positions()
        adapter.close()

        return {
            "positions": positions,
            "dry_run": dry_run,
            "timestamp": datetime.now(UTC).isoformat(),
        }
    except Exception as e:
        logger.error("Failed to fetch Kalshi positions: %s", e)
        return {
            "positions": [],
            "dry_run": True,
            "error": str(e),
            "timestamp": datetime.now(UTC).isoformat(),
        }


# ── DELETE /data/clear ───────────────────────────────────────────


@app.delete("/data/clear")
def clear_all_data() -> dict[str, Any]:
    """Clear all trading data from the database."""
    engine = get_db()
    deleted = {}
    with get_session(engine) as session:
        deleted["betting_orders"] = session.query(BettingOrder).delete()
        deleted["betting_signals"] = session.query(BettingSignal).delete()
        deleted["betting_predictions"] = session.query(BettingPrediction).delete()
        deleted["trading_positions"] = session.query(TradingPosition).delete()
        deleted["trading_markets"] = session.query(TradingMarket).delete()
        deleted["model_runs"] = session.query(ModelRun).delete()
        deleted["system_logs"] = session.query(SystemLog).delete()
        session.commit()

    return {
        "status": "cleared",
        "deleted": deleted,
        "timestamp": datetime.now(UTC).isoformat(),
    }
