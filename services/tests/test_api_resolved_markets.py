from __future__ import annotations

import sys
import types
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from sqlalchemy import create_engine

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder
from db_models import TradingMarket

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

from services.api.main import DISPLAY_CUTOFF_UTC, get_resolved_markets


def test_get_resolved_markets_uses_live_outcome_for_visible_expired_market():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Haifeng",
                market_id="kalshi:RESOLVED-TICKER",
                ticker="RESOLVED-TICKER",
                event_ticker="EV-RESOLVED",
                title="Resolved Test Market",
                category="Politics",
                expiration=datetime.now(UTC) - timedelta(days=1),
                last_price=0.04,
                yes_bid=0.04,
                yes_ask=0.04,
                no_bid=0.96,
                no_ask=0.96,
                volume_24h=100.0,
                updated_at=datetime.now(UTC),
            )
        )
        session.add(
            BettingOrder(
                instance_name="Haifeng",
                signal_id=None,
                order_id="resolved-buy-1",
                ticker="RESOLVED-TICKER",
                action="BUY",
                side="YES",
                count=4,
                price_cents=20,
                status="FILLED",
                filled_shares=4,
                fill_price=0.20,
                fee_paid=0.0,
                exchange_order_id="ex-resolved-buy-1",
                dry_run=False,
                created_at=DISPLAY_CUTOFF_UTC + timedelta(hours=1),
            )
        )

    with patch("services.api.main.get_db", return_value=engine):
        with patch("services.api.main._build_kalshi_adapter", return_value=object()):
            with patch("services.api.main._fetch_market_resolution_outcome", return_value=0.0):
                result = get_resolved_markets(instance_name="Haifeng")

    assert result["summary"]["total_markets"] == 1
    assert result["summary"]["markets_with_position"] == 1
    assert result["summary"]["total_pnl"] == -0.8

    row = result["markets"][0]
    assert row["ticker"] == "RESOLVED-TICKER"
    assert row["outcome"] == "NO"
    assert row["position_side"] == "YES"
    assert row["quantity"] == 4.0
    assert row["avg_price"] == 0.2
    assert row["capital"] == 0.8
    assert row["pnl"] == -0.8
    assert row["return_pct"] == -100.0
    assert row["correct"] is False
