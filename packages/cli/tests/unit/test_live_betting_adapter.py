from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import requests

from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.live_betting_adapter import build_betting_reasoning
from ai_prophet_core.betting.adapters.base import OrderRequest, OrderStatus
from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter


def _make_order() -> OrderRequest:
    return OrderRequest(
        order_id="order-1",
        intent_id="intent-1",
        market_id="kalshi:TEST",
        exchange_ticker="TEST",
        action="BUY",
        side="YES",
        shares=Decimal("3"),
        limit_price=Decimal("0.55"),
    )


def test_kalshi_adapter_network_error_returns_rejected(monkeypatch):
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    monkeypatch.setattr(adapter, "_sign_request", lambda *_args, **_kwargs: {})

    def raise_network(*_args, **_kwargs):
        raise requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(adapter._session, "post", raise_network)

    result = adapter.submit_order(_make_order())
    assert result.status == OrderStatus.REJECTED
    assert "Network error" in (result.rejection_reason or "")


def test_kalshi_adapter_http_error_returns_rejected(monkeypatch):
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    monkeypatch.setattr(adapter, "_sign_request", lambda *_args, **_kwargs: {})

    response = Mock()
    response.status_code = 503
    response.text = "service unavailable"
    http_error = requests.exceptions.HTTPError(response=response)

    failing_response = Mock()
    failing_response.raise_for_status.side_effect = http_error

    monkeypatch.setattr(adapter._session, "post", lambda *_args, **_kwargs: failing_response)

    result = adapter.submit_order(_make_order())
    assert result.status == OrderStatus.REJECTED
    assert "Kalshi API error 503" in (result.rejection_reason or "")


def test_build_betting_reasoning():
    markets = (
        CandidateMarket(
            market_id="m1",
            question="Will X happen?",
            description=None,
            resolution_time=datetime(2026, 3, 1, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 2, 20, 5, 30, tzinfo=UTC),
        ),
    )
    forecasts = {"m1": 0.70}
    intents = [
        {
            "market_id": "m1",
            "action": "BUY",
            "side": "YES",
            "shares": "0.15",
            "rationale": "test",
        }
    ]

    reasoning = build_betting_reasoning(markets, forecasts, intents)
    assert "candidates" in reasoning
    assert "forecasts" in reasoning
    assert "decisions" in reasoning
    assert reasoning["decisions"]["m1"]["recommendation"] == "BUY_YES"
