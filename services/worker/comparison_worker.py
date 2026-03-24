"""Comparison trading worker — dry-run single model via predictor service.

Mirrors the Haifeng/Jibang pattern but reads markets from the source instance
(no Kalshi API calls) and always runs in strict DRY RUN mode.

One process per model — deploy as separate Render workers:
  GPT5:   TRADING_INSTANCE_NAME=GPT5   COMPARISON_MODEL=openai:gpt-5.4:market
  Grok4:  TRADING_INSTANCE_NAME=Grok4  COMPARISON_MODEL=grok:grok-4:market
  Opus46: TRADING_INSTANCE_NAME=Opus46 COMPARISON_MODEL=anthropic:claude-opus-4-6:market

Usage:
    python services/worker/comparison_worker.py
    python services/worker/comparison_worker.py --once
    python services/worker/comparison_worker.py -v

Environment variables:
    DATABASE_URL                 — PostgreSQL connection string (required)
    TRADING_INSTANCE_NAME        — Instance name for this worker (GPT5 / Grok4 / Opus46)
    COMPARISON_MODEL             — Model spec (e.g. openai:gpt-5.4:market)
    COMPARISON_SOURCE_INSTANCE   — Instance to mirror markets from (default: Haifeng)
    COMPARISON_STARTING_CASH     — Starting virtual balance (default: WORKER_STARTING_CASH or 10000)
    COMPARISON_POLL_INTERVAL_SEC — (Deprecated) Workers now run at the top of each hour
    PREDICTOR_SERVICE_URL        — Cloud Run predictor URL (required)
    PREDICTOR_API_KEY            — Predictor service API key
    KALSHI_API_KEY_ID            — Kalshi credentials (needed by BettingEngine even in dry-run)
    KALSHI_PRIVATE_KEY_B64       — Kalshi credentials
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
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from position_replay import replay_orders_by_ticker, summarize_replayed_positions

load_dotenv()

logger = logging.getLogger("comparison_worker")

# ── Configuration ────────────────────────────────────────────────

INSTANCE_NAME = os.getenv("TRADING_INSTANCE_NAME", "GPT5")
COMPARISON_MODEL = os.getenv("COMPARISON_MODEL", "openai:gpt-5.4:market")
SOURCE_INSTANCE = os.getenv("COMPARISON_SOURCE_INSTANCE", "Haifeng")
STARTING_CASH = float(
    os.getenv("COMPARISON_STARTING_CASH", os.getenv("WORKER_STARTING_CASH", "10000"))
)
POLL_INTERVAL = int(
    os.getenv("COMPARISON_POLL_INTERVAL_SEC", os.getenv("WORKER_POLL_INTERVAL_SEC", "3600"))
)
PREDICTOR_SERVICE_URL = os.getenv("PREDICTOR_SERVICE_URL", "").rstrip("/")
PREDICTOR_API_KEY = os.getenv("PREDICTOR_API_KEY", "")
PREDICTOR_TIMEOUT_SEC = float(os.getenv("PREDICTOR_TIMEOUT_SEC", "180"))
REMOTE_PREDICT_TIMEOUT_SEC = float(
    os.getenv("REMOTE_PREDICT_TIMEOUT_SEC", str(PREDICTOR_TIMEOUT_SEC + 10))
)

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


# ── Remote predictor call (same as main.py) ───────────────────────

def _remote_predict(model_spec: str, market_info: dict) -> dict:
    resp = requests.post(
        f"{PREDICTOR_SERVICE_URL}/predict",
        json={
            "model_spec": model_spec,
            "market_info": market_info,
            "instance_name": INSTANCE_NAME,
        },
        headers={"X-API-Key": PREDICTOR_API_KEY} if PREDICTOR_API_KEY else {},
        timeout=REMOTE_PREDICT_TIMEOUT_SEC,
    )
    resp.raise_for_status()
    return resp.json()


def _remote_predict_with_retry(model_spec: str, market_info: dict, max_retries: int = 2) -> dict:
    for attempt in range(max_retries + 1):
        try:
            return _remote_predict(model_spec, market_info)
        except Exception as e:
            if attempt < max_retries:
                logger.warning("Remote predict attempt %d failed, retrying in 5s: %s", attempt + 1, e)
                time.sleep(5)
            else:
                raise


# ── DB helpers ────────────────────────────────────────────────────

def _get_source_markets(db_engine, source_instance: str) -> list[dict]:
    """Read current active markets from the source instance."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        with get_session(db_engine) as session:
            rows = (
                session.query(TradingMarket)
                .filter(TradingMarket.instance_name == source_instance)
                .order_by(TradingMarket.updated_at.desc())
                .limit(200)
                .all()
            )
            markets = []
            for row in rows:
                if row.yes_ask is None:
                    continue
                markets.append({
                    "ticker": row.ticker,
                    "event_ticker": row.event_ticker,
                    "market_id": row.market_id,
                    "title": row.title,
                    "subtitle": "",
                    "category": row.category,
                    "yes_ask": row.yes_ask,
                    "no_ask": row.no_ask,
                    "yes_bid": row.yes_bid,
                    "no_bid": row.no_bid,
                    "volume_24h": row.volume_24h,
                    "expiration": row.expiration,
                })
            return markets
    except Exception as e:
        logger.error("Failed to read source markets from '%s': %s", source_instance, e)
        return []


def _upsert_market(db_engine, market: dict) -> None:
    """Upsert market data for this instance so positions can reference live prices."""
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import TradingMarket

        now = datetime.now(UTC)
        with get_session(db_engine) as session:
            existing = session.query(TradingMarket).filter_by(
                instance_name=INSTANCE_NAME,
                market_id=market["market_id"],
            ).first()
            if existing:
                existing.yes_ask = market["yes_ask"]
                existing.no_ask = market["no_ask"]
                existing.yes_bid = market.get("yes_bid")
                existing.no_bid = market.get("no_bid")
                existing.volume_24h = market.get("volume_24h", 0)
                existing.updated_at = now
            else:
                session.add(TradingMarket(
                    instance_name=INSTANCE_NAME,
                    market_id=market["market_id"],
                    ticker=market.get("ticker", ""),
                    event_ticker=market.get("event_ticker", ""),
                    title=market.get("title", ""),
                    category=market.get("category", "unknown"),
                    last_price=market.get("yes_ask"),
                    yes_ask=market.get("yes_ask"),
                    no_ask=market.get("no_ask"),
                    yes_bid=market.get("yes_bid"),
                    no_bid=market.get("no_bid"),
                    volume_24h=market.get("volume_24h", 0),
                    expiration=market.get("expiration"),
                    updated_at=now,
                ))
    except Exception as e:
        logger.warning("Failed to upsert market %s: %s", market.get("market_id"), e)


def _save_model_run(db_engine, market_id: str, decision: str,
                    confidence: float | None, metadata: dict) -> None:
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import ModelRun

        with get_session(db_engine) as session:
            session.add(ModelRun(
                instance_name=INSTANCE_NAME,
                model_name=COMPARISON_MODEL,
                timestamp=datetime.now(UTC),
                decision=decision,
                confidence=confidence,
                market_id=market_id,
                metadata_json=json.dumps(metadata),
            ))
    except Exception as e:
        logger.warning("Failed to save model run: %s", e)


def _log_heartbeat(db_engine, message: str = "alive") -> None:
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                instance_name=INSTANCE_NAME,
                level="HEARTBEAT",
                message=message,
                component="worker",
                created_at=datetime.now(UTC),
            ))
    except Exception as e:
        logger.warning("Failed to log heartbeat: %s", e)


def _log_event(db_engine, level: str, message: str) -> None:
    try:
        from ai_prophet_core.betting.db import get_session
        from db_models import SystemLog

        with get_session(db_engine) as session:
            session.add(SystemLog(
                instance_name=INSTANCE_NAME,
                level=level,
                message=message[:2000],
                component="comparison_worker",
                created_at=datetime.now(UTC),
            ))
    except Exception:
        pass


def _update_positions(db_engine) -> None:
    """Replay orders to update trading_positions."""
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder
        from db_models import TradingMarket, TradingPosition

        now = datetime.now(UTC)
        with get_session(db_engine) as session:
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == INSTANCE_NAME)
                .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
                .order_by(BettingOrder.created_at.asc())
                .all()
            )
            markets_by_ticker = {
                m.ticker: m
                for m in session.query(TradingMarket)
                .filter(TradingMarket.instance_name == INSTANCE_NAME)
                .all()
                if m.ticker
            }

        positions = replay_orders_by_ticker(orders)
        active_market_ids = {
            f"kalshi:{ticker}"
            for ticker, pos in positions.items()
            if pos.current_position()[0] is not None and pos.current_position()[1] >= 0.001
        }

        with get_session(db_engine) as session:
            (
                session.query(TradingPosition)
                .filter(TradingPosition.instance_name == INSTANCE_NAME)
                .filter(
                    ~TradingPosition.market_id.in_(active_market_ids)
                    if active_market_ids
                    else True
                )
                .delete(synchronize_session=False)
            )
            for ticker, pos in positions.items():
                side, qty, avg_price = pos.current_position()
                if side is None or qty < 0.001:
                    continue
                market_id = f"kalshi:{ticker}"
                market = markets_by_ticker.get(ticker)
                current_bid = None
                if market:
                    current_bid = (
                        market.yes_bid or (1.0 - market.no_ask if market.no_ask else None)
                        if side == "yes"
                        else market.no_bid or (1.0 - market.yes_ask if market.yes_ask else None)
                    )
                unrealized = 0.0 if current_bid is None else (current_bid - avg_price) * qty
                existing = session.query(TradingPosition).filter_by(
                    instance_name=INSTANCE_NAME, market_id=market_id
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
                        instance_name=INSTANCE_NAME,
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
        logger.info("Updated %d positions", len(positions))
    except Exception as e:
        logger.warning("Failed to update positions: %s", e)


def _build_ledger_state(db_engine) -> dict | None:
    try:
        from ai_prophet_core.betting.db import get_session
        from ai_prophet_core.betting.db_schema import BettingOrder

        with get_session(db_engine) as session:
            # Only count FILLED and DRY_RUN orders, not PENDING
            # This ensures consistency with the main worker's ledger state
            orders = (
                session.query(BettingOrder)
                .filter(BettingOrder.instance_name == INSTANCE_NAME)
                .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
                .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
                .all()
            )
        positions = replay_orders_by_ticker(orders)
        capital_deployed, total_realized, open_position_count = summarize_replayed_positions(positions)
        cash = (
            Decimal(str(STARTING_CASH))
            - Decimal(str(capital_deployed))
            + Decimal(str(total_realized))
        )
        return {
            "positions": positions,
            "cash": cash,
            "total_pnl": Decimal(str(total_realized)),
            "position_count": open_position_count,
        }
    except Exception as e:
        logger.debug("Could not build ledger state: %s", e)
        return None


def _build_engine(db_engine):
    """Create a BettingEngine for this comparison instance (always dry-run)."""
    try:
        from ai_prophet_core.betting import BettingEngine, LiveBettingSettings, RebalancingStrategy

        env = dict(os.environ)
        env["LIVE_BETTING_ENABLED"] = "true"
        env["LIVE_BETTING_DRY_RUN"] = "true"
        env["WORKER_STARTING_CASH"] = str(STARTING_CASH)
        env["WORKER_STRATEGY"] = "rebalancing"

        settings = LiveBettingSettings.from_env(env)
        engine = BettingEngine(
            strategy=RebalancingStrategy(),
            db_engine=db_engine,
            dry_run=True,
            kalshi_config=settings.kalshi,
            enabled=True,
            instance_name=INSTANCE_NAME,
            starting_cash=STARTING_CASH,
        )
        return engine
    except Exception as e:
        logger.error("Failed to create betting engine: %s", e)
        return None


# ── Main cycle ────────────────────────────────────────────────────

def run_cycle(args) -> None:
    from ai_prophet_core.betting.db import create_db_engine
    from ai_prophet_core.betting.db_schema import Base as CoreBase

    if not PREDICTOR_SERVICE_URL:
        logger.error("PREDICTOR_SERVICE_URL is not set — cannot run predictions")
        return

    db_engine = create_db_engine()
    CoreBase.metadata.create_all(db_engine, checkfirst=True)
    _log_heartbeat(db_engine, message="cycle_start")

    # 1. Read markets from source instance
    markets = _get_source_markets(db_engine, SOURCE_INSTANCE)
    if not markets:
        logger.warning("No markets found in '%s', skipping cycle", SOURCE_INSTANCE)
        _log_heartbeat(db_engine, message="cycle_end")
        db_engine.dispose()
        return

    logger.info(
        "Starting cycle: instance=%s model=%s markets=%d",
        INSTANCE_NAME, COMPARISON_MODEL, len(markets),
    )
    _log_event(db_engine, "INFO",
               f"Cycle start: model={COMPARISON_MODEL}, markets={len(markets)}")

    tick_ts = datetime.now(UTC)

    # 2. Sync market data
    for market in markets:
        _upsert_market(db_engine, market)

    # 3. Fan out predictions in parallel (same pattern as Haifeng/Jibang)
    predictions: dict[str, dict] = {}

    logger.info("Fanning out %d predictions via predictor service", len(markets))
    t_fan = time.time()

    with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
        future_to_ticker = {
            executor.submit(
                _remote_predict_with_retry,
                COMPARISON_MODEL,
                {
                    "title": mkt.get("title", ""),
                    "subtitle": mkt.get("subtitle", ""),
                    "category": mkt.get("category", ""),
                    "yes_ask": mkt["yes_ask"],
                    "no_ask": mkt["no_ask"],
                },
            ): mkt["ticker"]
            for mkt in markets
        }
        for future in concurrent.futures.as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                predictions[ticker] = future.result()
                logger.info(
                    "  [%s] %s → p_yes=%.3f",
                    COMPARISON_MODEL, ticker, predictions[ticker]["p_yes"],
                )
            except Exception as e:
                logger.error("  Prediction failed for %s: %s", ticker, e)
                _log_event(db_engine, "ERROR", f"Prediction failed for {ticker}: {e}")

    logger.info(
        "Predictions complete: %d/%d in %.1fs",
        len(predictions), len(markets), time.time() - t_fan,
    )

    # 4. Build engine
    engine = _build_engine(db_engine)

    # 5. Bet on each market sequentially
    placed = 0
    for mkt in markets:
        if _shutdown_requested:
            break

        ticker = mkt["ticker"]
        market_id = mkt["market_id"]
        yes_ask = mkt["yes_ask"]
        no_ask = mkt["no_ask"]
        pred = predictions.get(ticker)
        if not pred:
            continue

        p_yes = pred["p_yes"]
        confidence = pred.get("confidence", 0.5)
        reasoning = pred.get("reasoning", "")

        # Build fresh ledger state for accurate position/cash tracking
        ledger_state = _build_ledger_state(db_engine)

        # Determine decision for model run record
        decision = "HOLD"
        portfolio = None
        if engine is not None and ledger_state is not None:
            try:
                from ai_prophet_core.betting.strategy import PortfolioSnapshot

                ticker_key = market_id[len("kalshi:"):] if market_id.startswith("kalshi:") else market_id
                market_position = ledger_state["positions"].get(ticker_key)
                market_side, market_qty = None, 0.0
                if market_position is not None:
                    market_side, market_qty, _ = market_position.current_position()

                portfolio = PortfolioSnapshot(
                    cash=ledger_state["cash"],
                    total_pnl=ledger_state["total_pnl"],
                    position_count=ledger_state["position_count"],
                    market_position_shares=Decimal(str(market_qty)),
                    market_position_side=market_side,
                )
                engine.strategy._portfolio = portfolio
                signal = engine.strategy.evaluate(
                    market_id=market_id, p_yes=p_yes, yes_ask=yes_ask, no_ask=no_ask,
                )
                decision = f"BUY_{signal.side.upper()}" if signal is not None else "HOLD"
            except Exception:
                pass

        _save_model_run(
            db_engine, market_id, decision, confidence,
            {"p_yes": p_yes, "reasoning": reasoning, "yes_ask": yes_ask, "no_ask": no_ask},
        )

        if engine is not None:
            try:
                result = engine.on_forecast(
                    tick_ts=tick_ts,
                    market_id=market_id,
                    p_yes=p_yes,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    source=COMPARISON_MODEL,
                    portfolio=portfolio,
                )
                if result is not None and result.order_placed:
                    placed += 1
            except Exception as e:
                logger.warning("on_forecast failed for %s: %s", ticker, e)

    logger.info("Cycle complete: %d orders placed across %d markets", placed, len(markets))
    _log_event(db_engine, "INFO",
               f"Cycle complete: model={COMPARISON_MODEL}, placed={placed}, markets={len(markets)}")

    _update_positions(db_engine)

    if engine is not None:
        try:
            engine.close()
        except Exception:
            pass

    _log_heartbeat(db_engine, message="cycle_end")
    db_engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Comparison trading worker")
    parser.add_argument("--once", action="store_true", help="Run one cycle then exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    logger.info(
        "Comparison worker starting: instance=%s model=%s source=%s "
        "starting_cash=$%.0f poll_interval=%ds predictor=%s",
        INSTANCE_NAME, COMPARISON_MODEL, SOURCE_INSTANCE,
        STARTING_CASH, POLL_INTERVAL, PREDICTOR_SERVICE_URL or "(not set)",
    )

    while not _shutdown_requested:
        try:
            run_cycle(args)
        except Exception as e:
            logger.exception("Cycle failed: %s", e)

        if args.once or _shutdown_requested:
            break

        # Calculate time until the next top of the hour
        now = datetime.now(UTC)
        next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        seconds_until_next_hour = int((next_hour - now).total_seconds())

        # Show local time too
        try:
            import zoneinfo
            local_tz = zoneinfo.ZoneInfo('America/Los_Angeles')
            next_hour_local = next_hour.astimezone(local_tz)
            logger.info(
                "Next cycle will run at the top of the hour: %s UTC / %s PST (%d seconds)",
                next_hour.strftime("%H:%M"),
                next_hour_local.strftime("%H:%M"),
                seconds_until_next_hour
            )
        except:
            logger.info(
                "Next cycle will run at the top of the hour: %s UTC (%d seconds)",
                next_hour.strftime("%H:%M"), seconds_until_next_hour
            )

        # Sleep until the next hour, checking for shutdown every second
        for _ in range(seconds_until_next_hour):
            if _shutdown_requested:
                break
            time.sleep(1)

    logger.info("Comparison worker stopped")


if __name__ == "__main__":
    main()
