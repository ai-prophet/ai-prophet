from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import requests
from sqlalchemy import create_engine

from ai_prophet_core.betting.adapters.base import OrderRequest, OrderStatus
from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
from ai_prophet_core.betting.config import (
    DEFAULT_KALSHI_BASE_URL,
    KalshiConfig,
    LiveBettingSettings,
)
from ai_prophet_core.betting.hook import LiveBettingHook


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


def test_live_betting_settings_from_env_prefers_explicit_private_key_name(monkeypatch):
    monkeypatch.setenv("LIVE_BETTING_ENABLED", "true")
    monkeypatch.setenv("LIVE_BETTING_DRY_RUN", "false")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_B64", "new-key")
    monkeypatch.delenv("KALSHI_API_KEY", raising=False)

    settings = LiveBettingSettings.from_env()

    assert settings == LiveBettingSettings(
        enabled=True,
        dry_run=False,
        kalshi=KalshiConfig(
            api_key_id="key-id",
            private_key_base64="new-key",
            base_url=DEFAULT_KALSHI_BASE_URL,
        ),
    )


def test_live_betting_settings_from_env_supports_legacy_private_key_name(monkeypatch):
    monkeypatch.delenv("KALSHI_PRIVATE_KEY_B64", raising=False)
    monkeypatch.setenv("KALSHI_API_KEY", "legacy-key")

    settings = LiveBettingSettings.from_env()

    assert settings.kalshi.private_key_base64 == "legacy-key"


def test_hook_disabled_noops_without_hidden_env_state():
    engine = create_engine("sqlite:///:memory:")
    hook = LiveBettingHook(
        betting_model_names=["model-a"],
        db_engine=engine,
        enabled=False,
        dry_run=True,
    )

    result = hook.on_forecast(
        model_name="model-a",
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        market_id="kalshi:TEST-MARKET",
        p_yes=0.72,
        yes_ask=0.55,
        no_ask=0.45,
        question="Test market?",
    )

    assert result is None


def test_hook_dedupes_duplicate_model_forecasts(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    hook = LiveBettingHook(
        betting_model_names=["model-a", "model-b"],
        db_engine=engine,
        enabled=True,
        dry_run=True,
    )

    saved_decisions: list[dict] = []
    placed_orders: list[tuple[str, str, int, float]] = []

    monkeypatch.setattr(
        hook,
        "_save_bet_decision",
        lambda **kwargs: saved_decisions.append(kwargs),
    )
    monkeypatch.setattr(
        hook,
        "_place_kalshi_order",
        lambda ticker, side, count, price: placed_orders.append((ticker, side, count, price))
        or {
            "order_id": "order-1",
            "status": "DRY_RUN",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": "dry-run-order-1",
        },
    )
    monkeypatch.setattr(hook, "_save_kalshi_order", lambda **_kwargs: None)

    tick_ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    kwargs = {
        "tick_ts": tick_ts,
        "market_id": "kalshi:TEST-MARKET",
        "p_yes": 0.72,
        "yes_ask": 0.55,
        "no_ask": 0.45,
        "question": "Test market?",
    }

    assert hook.on_forecast(model_name="model-a", **kwargs) is None
    assert hook.on_forecast(model_name="model-a", **kwargs) is None

    result = hook.on_forecast(model_name="model-b", **kwargs)
    assert result is not None
    assert hook.on_forecast(model_name="model-a", **kwargs) is None

    assert len(saved_decisions) == 2
    assert len(placed_orders) == 1


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
