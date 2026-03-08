from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import requests

from ai_prophet.core.tick_context import CandidateMarket
from ai_prophet.live_betting.adapters.base import OrderRequest, OrderStatus
from ai_prophet.live_betting.adapters.kalshi import KalshiAdapter
from ai_prophet.live_betting_adapter import execute_live_betting_strategy


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
    adapter = KalshiAdapter(api_key_id="id", private_key_b64="key", dry_run=False)
    monkeypatch.setattr(adapter, "_sign_request", lambda *_args, **_kwargs: {})

    def raise_network(*_args, **_kwargs):
        raise requests.exceptions.ConnectionError("network down")

    monkeypatch.setattr(adapter._session, "post", raise_network)

    result = adapter.submit_order(_make_order())
    assert result.status == OrderStatus.REJECTED
    assert "Network error" in (result.rejection_reason or "")


def test_kalshi_adapter_http_error_returns_rejected(monkeypatch):
    adapter = KalshiAdapter(api_key_id="id", private_key_b64="key", dry_run=False)
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


def test_live_betting_strategy_closes_llm_client(monkeypatch):
    class _FakeLLMClient:
        def __init__(self):
            self.closed = False

        def generate(self, _request):
            return SimpleNamespace(
                content='{"rationale":"test","probabilities":{"m1":0.70}}'
            )

        def close(self):
            self.closed = True

    fake_llm = _FakeLLMClient()
    monkeypatch.setattr(
        "ai_prophet.live_betting.config.get_pipeline_config",
        lambda _model_spec: {"provider": "openai", "api_model": "gpt-5.2"},
    )
    monkeypatch.setattr("ai_prophet.llm.create_llm_client", lambda **_kwargs: fake_llm)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    markets = [
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
        )
    ]

    execute_live_betting_strategy(
        model_spec="openai:gpt-5.2",
        tick_ts=datetime(2026, 2, 20, 6, 0, tzinfo=UTC),
        candidate_markets=markets,
        experiment_id="exp-1",
    )

    assert fake_llm.closed is True

