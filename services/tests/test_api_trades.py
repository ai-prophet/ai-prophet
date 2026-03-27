from __future__ import annotations

import json
import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import (
    Base,
    BettingDeferredFlip,
    BettingOrder,
    BettingPrediction,
    BettingSignal,
)
from db_models import ModelRun, TradingMarket

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class _DummyFastAPI:
        def __init__(self, *args, **kwargs):
            pass

        def add_middleware(self, *args, **kwargs):
            return None

        def exception_handler(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def get(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def post(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

        def delete(self, *args, **kwargs):
            def decorator(fn):
                return fn
            return decorator

    fastapi.FastAPI = _DummyFastAPI
    fastapi.Query = lambda default=None, **kwargs: default
    fastapi.Request = object
    sys.modules["fastapi"] = fastapi

    fastapi_middleware = types.ModuleType("fastapi.middleware")
    sys.modules["fastapi.middleware"] = fastapi_middleware

    fastapi_cors = types.ModuleType("fastapi.middleware.cors")
    fastapi_cors.CORSMiddleware = object
    sys.modules["fastapi.middleware.cors"] = fastapi_cors

    fastapi_responses = types.ModuleType("fastapi.responses")
    fastapi_responses.JSONResponse = object
    sys.modules["fastapi.responses"] = fastapi_responses

from services.api.main import get_markets, get_trades


def test_get_trades_recovers_prediction_for_signal_less_net_sell():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    tick_ts = datetime(2026, 3, 27, 16, 0, tzinfo=UTC)
    signal_ts = tick_ts + timedelta(minutes=7)
    sell_ts = tick_ts + timedelta(minutes=9)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Haifeng",
                market_id="kalshi:TEST",
                ticker="TEST",
                event_ticker="TEST-EVENT",
                title="Test Market",
                category="Sports",
                updated_at=tick_ts,
            )
        )

        pred = BettingPrediction(
            instance_name="Haifeng",
            tick_ts=tick_ts,
            market_id="kalshi:TEST",
            source="gemini:gemini-3.1-pro-preview",
            p_yes=0.82,
            yes_ask=0.57,
            no_ask=0.44,
            created_at=signal_ts,
        )
        session.add(pred)
        session.flush()

        sig = BettingSignal(
            instance_name="Haifeng",
            prediction_id=pred.id,
            strategy_name="rebalancing",
            side="no",
            shares=0.25,
            price=0.44,
            cost=0.11,
            metadata_json=json.dumps({
                "target": 0.25,
                "current_pos": 0.50,
                "delta": -0.25,
                "sell_portion": 0.25,
                "buy_portion": 0.0,
            }),
            created_at=signal_ts,
        )
        session.add(sig)
        session.flush()

        session.add(
            ModelRun(
                instance_name="Haifeng",
                market_id="kalshi:TEST",
                model_name="gemini:gemini-3.1-pro-preview",
                timestamp=tick_ts,
                decision="BUY_NO",
                confidence=None,
                metadata_json=json.dumps({"p_yes": 0.82}),
            )
        )

        session.add(
            BettingOrder(
                instance_name="Haifeng",
                signal_id=None,
                order_id="sell-1",
                ticker="TEST",
                action="SELL",
                side="YES",
                count=25,
                price_cents=56,
                status="FILLED",
                filled_shares=25,
                fill_price=0.44,
                fee_paid=0.04,
                exchange_order_id="ex-sell-1",
                dry_run=False,
                created_at=sell_ts,
            )
        )

    with patch("services.api.main.get_db", return_value=engine):
        result = get_trades(limit=10, offset=0, instance_name="Haifeng")

    assert result["total"] == 1
    trade = result["trades"][0]
    assert trade["action"] == "SELL"
    assert trade["prediction"] is not None
    assert trade["prediction"]["p_yes"] == 0.82
    assert trade["prediction"]["yes_ask"] == 0.57
    assert trade["prediction"]["no_ask"] == 0.44


def test_get_trades_includes_deferred_flip_buy_as_pending_intent():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    tick_ts = datetime(2026, 3, 27, 16, 9, tzinfo=UTC)
    signal_ts = tick_ts
    sell_ts = tick_ts + timedelta(seconds=2)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Haifeng",
                market_id="kalshi:BEEF",
                ticker="BEEF",
                event_ticker="BEEF-EVENT",
                title="Beef Market",
                category="Economics",
                updated_at=tick_ts,
            )
        )

        pred = BettingPrediction(
            instance_name="Haifeng",
            tick_ts=tick_ts,
            market_id="kalshi:BEEF",
            source="gemini:gemini-3.1-pro-preview",
            p_yes=0.31,
            yes_ask=0.32,
            no_ask=0.69,
            created_at=signal_ts,
        )
        session.add(pred)
        session.flush()

        sig = BettingSignal(
            instance_name="Haifeng",
            prediction_id=pred.id,
            strategy_name="rebalancing",
            side="no",
            shares=0.01,
            price=0.69,
            cost=0.0069,
            metadata_json=json.dumps({
                "target": -0.01,
                "current_pos": 0.03,
                "delta": -0.04,
                "sell_portion": 0.03,
                "buy_portion": 0.01,
            }),
            created_at=signal_ts,
        )
        session.add(sig)
        session.flush()

        session.add(
            BettingOrder(
                instance_name="Haifeng",
                signal_id=sig.id,
                order_id="sell-1",
                ticker="BEEF",
                action="SELL",
                side="YES",
                count=3,
                price_cents=69,
                status="PENDING",
                filled_shares=1,
                fill_price=0.69,
                fee_paid=0.02,
                exchange_order_id="ex-sell-1",
                dry_run=False,
                created_at=sell_ts,
            )
        )
        session.add(
            BettingDeferredFlip(
                instance_name="Haifeng",
                signal_id=sig.id,
                market_id="kalshi:BEEF",
                ticker="BEEF",
                sell_order_id="sell-1",
                buy_side="NO",
                buy_count=1,
                buy_price_cents=69,
                status="WAITING_SELL",
                buy_order_id=None,
                last_error=None,
                created_at=sell_ts,
                updated_at=sell_ts,
            )
        )

    with patch("services.api.main.get_db", return_value=engine):
        result = get_trades(limit=10, offset=0, instance_name="Haifeng")

    assert result["total"] == 2
    synthetic = next(trade for trade in result["trades"] if trade.get("synthetic_kind") == "DEFERRED_FLIP")
    assert synthetic["action"] == "BUY"
    assert synthetic["side"] == "NO"
    assert synthetic["count"] == 1
    assert synthetic["status"] == "PENDING"
    assert synthetic["prediction"] is not None
    assert synthetic["prediction"]["p_yes"] == 0.31
    assert synthetic["prediction"]["yes_ask"] == 0.32
    assert synthetic["pending_reason"] == "Queued after the sell leg finishes."


def test_get_markets_prefers_latest_non_skip_probability():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    latest_skip_ts = datetime(2026, 3, 27, 16, 0, tzinfo=UTC)
    actionable_ts = latest_skip_ts - timedelta(hours=8)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Haifeng",
                market_id="kalshi:PROB",
                ticker="PROB",
                event_ticker="PROB-EVENT",
                title="Probability Test",
                category="Politics",
                yes_ask=0.02,
                no_ask=0.99,
                updated_at=latest_skip_ts,
            )
        )
        session.add_all([
            ModelRun(
                instance_name="Haifeng",
                market_id="kalshi:PROB",
                model_name="gpt-5",
                timestamp=actionable_ts,
                decision="BUY_NO",
                confidence=None,
                metadata_json=json.dumps({"p_yes": 0.15}),
            ),
            ModelRun(
                instance_name="Haifeng",
                market_id="kalshi:PROB",
                model_name="gpt-5",
                timestamp=latest_skip_ts,
                decision="SKIP",
                confidence=None,
                metadata_json=json.dumps({"skip_reason": "spread"}),
            ),
        ])

    with (
        patch("services.api.main.get_db", return_value=engine),
        patch("services.api.main._display_visible_market_activity", return_value=({"PROB"}, {"kalshi:PROB"})),
    ):
        rows = get_markets(limit=10, instance_name="Haifeng")

    assert len(rows) == 1
    row = rows[0]
    assert row["latest_non_skip_p_yes"] == 0.15
    assert row["aggregated_p_yes"] == 0.15
