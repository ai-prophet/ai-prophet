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
from ai_prophet_core.betting.engine import BettingEngine
from ai_prophet_core.betting.strategy import (
    BetSignal,
    BettingStrategy,
    DefaultBettingStrategy,
)


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


# ── Settings tests ──────────────────────────────────────────────────


def test_live_betting_settings_from_env_prefers_explicit_private_key_name(monkeypatch):
    monkeypatch.setenv("LIVE_BETTING_ENABLED", "true")
    monkeypatch.setenv("LIVE_BETTING_DRY_RUN", "false")
    monkeypatch.setenv("KALSHI_API_KEY_ID", "key-id")
    monkeypatch.setenv("KALSHI_PRIVATE_KEY_B64", "new-key")

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


# ── Strategy tests ──────────────────────────────────────────────────


def test_default_strategy_buy_yes():
    strategy = DefaultBettingStrategy()
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.72, yes_ask=0.55, no_ask=0.45)
    assert signal is not None
    assert signal.side == "yes"
    assert signal.shares > 0


def test_default_strategy_buy_no():
    strategy = DefaultBettingStrategy()
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.30, yes_ask=0.55, no_ask=0.45)
    assert signal is not None
    assert signal.side == "no"
    assert signal.shares > 0


def test_default_strategy_skip_within_spread():
    strategy = DefaultBettingStrategy()
    # With yes_ask=0.60, no_ask=0.45: lower=0.55, upper=0.60 → p_yes=0.57 is inside
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.57, yes_ask=0.60, no_ask=0.45)
    assert signal is None


def test_default_strategy_skip_wide_spread():
    strategy = DefaultBettingStrategy(max_spread=1.0)
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.72, yes_ask=0.55, no_ask=0.50)
    assert signal is None


def test_custom_strategy():
    class AlwaysBetYes(BettingStrategy):
        name = "always-yes"

        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            return BetSignal(side="yes", shares=1.0, price=yes_ask, cost=yes_ask)

    strategy = AlwaysBetYes()
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.50, yes_ask=0.55, no_ask=0.45)
    assert signal is not None
    assert signal.side == "yes"
    assert signal.shares == 1.0


# ── Engine tests ────────────────────────────────────────────────────


def test_engine_disabled_returns_empty():
    engine = BettingEngine(enabled=False)
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST": 0.72},
        market_prices={"kalshi:TEST": (0.55, 0.45)},
        source="test-model",
    )
    assert results == []


def test_engine_on_forecast_disabled_returns_none():
    engine = BettingEngine(enabled=False)
    result = engine.on_forecast(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        market_id="kalshi:TEST-MARKET",
        p_yes=0.72,
        yes_ask=0.55,
        no_ask=0.45,
        question="Test market?",
    )
    assert result is None


def test_engine_processes_forecast_and_places_order(monkeypatch):
    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        db_engine=db_engine,
        dry_run=True,
        enabled=True,
    )

    # Mock the adapter to avoid real API calls
    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("17"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.72},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="test-model",
    )

    assert len(results) == 1
    result = results[0]
    assert result.market_id == "kalshi:TEST-MKT"
    assert result.order_placed is True
    assert result.signal is not None
    assert result.signal.side == "yes"
    mock_adapter.submit_order.assert_called_once()


def test_engine_skips_when_strategy_returns_none(monkeypatch):
    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        db_engine=db_engine,
        dry_run=True,
        enabled=True,
    )

    # p_yes=0.57 within bid-ask band [0.55, 0.60] → strategy skips
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.57},
        market_prices={"kalshi:TEST-MKT": (0.60, 0.45)},
        source="test-model",
    )

    assert len(results) == 1
    result = results[0]
    assert result.signal is None
    assert result.order_placed is False


def test_engine_with_custom_strategy():
    class AlwaysBetYes(BettingStrategy):
        name = "always-yes"

        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            return BetSignal(side="yes", shares=1.0, price=yes_ask, cost=yes_ask)

    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        strategy=AlwaysBetYes(),
        db_engine=db_engine,
        dry_run=True,
        enabled=True,
    )

    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("100"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    # Even with p_yes=0.50 (default strategy would skip), custom strategy bets
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.50},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="custom-model",
    )

    assert len(results) == 1
    assert results[0].order_placed is True
    assert results[0].signal is not None
    assert results[0].signal.side == "yes"


def test_engine_logs_to_db():
    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        db_engine=db_engine,
        dry_run=True,
        enabled=True,
    )

    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("17"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.72},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="test-model",
    )

    # Verify predictions logged
    predictions = engine.get_recent_predictions(limit=10)
    assert len(predictions) == 1
    assert predictions[0]["market_id"] == "kalshi:TEST-MKT"
    assert predictions[0]["source"] == "test-model"
    assert predictions[0]["p_yes"] == 0.72

    # Verify orders logged
    orders = engine.get_recent_orders(limit=10)
    assert len(orders) == 1
    assert orders[0]["status"] == "DRY_RUN"


def test_engine_no_db_works():
    """Engine works fine without a DB engine (no persistence)."""
    engine = BettingEngine(db_engine=None, dry_run=True, enabled=True)

    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("17"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.72},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="test-model",
    )
    assert len(results) == 1
    assert results[0].order_placed is True


# ── Kalshi adapter tests ────────────────────────────────────────────


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
