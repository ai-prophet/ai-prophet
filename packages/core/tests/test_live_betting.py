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
    PortfolioSnapshot,
    RebalancingStrategy,
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
    # With the +/-1pp buffer, yes_ask=0.60, no_ask=0.45 -> [0.54, 0.61]
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
            return BetSignal(side="yes", shares=0.1, price=yes_ask, cost=0.1 * yes_ask)

    strategy = AlwaysBetYes()
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.50, yes_ask=0.55, no_ask=0.45)
    assert signal is not None
    assert signal.side == "yes"
    assert signal.shares == 0.1


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
            return BetSignal(side="yes", shares=0.1, price=yes_ask, cost=0.1 * yes_ask)

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
        filled_shares=Decimal("10"),
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


# ── New tests: order status fix ────────────────────────────────────


def test_parse_order_resting_returns_pending():
    """Resting orders should map to PENDING, not FILLED."""
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    data = {"order": {"status": "resting", "order_id": "ex-123"}}
    result = adapter._parse_order_response(_make_order(), data)
    assert result.status == OrderStatus.PENDING
    assert result.exchange_order_id == "ex-123"
    assert result.filled_shares == Decimal("0")


def test_parse_order_pending_returns_pending():
    """Pending orders should map to PENDING, not FILLED."""
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    data = {"order": {"status": "pending", "order_id": "ex-456"}}
    result = adapter._parse_order_response(_make_order(), data)
    assert result.status == OrderStatus.PENDING
    assert result.filled_shares == Decimal("0")


def test_parse_order_executed_uses_fill_count():
    """Executed orders should use Kalshi fill_count, not place_count."""
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    data = {
        "order": {
            "status": "executed",
            "order_id": "ex-789",
            "fill_count": 6,
            "avg_price": 94,
        }
    }
    result = adapter._parse_order_response(_make_order(), data)
    assert result.status == OrderStatus.FILLED
    assert result.filled_shares == Decimal("6")
    assert result.fill_price == Decimal("0.94")


def test_parse_order_cancelled_preserves_partial_fills():
    """Cancelled orders can still carry real fills that must be replayed."""
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    data = {
        "order": {
            "status": "canceled",
            "order_id": "ex-987",
            "fill_count": 2,
            "avg_price": 61,
        }
    }
    result = adapter._parse_order_response(_make_order(), data)
    assert result.status == OrderStatus.CANCELLED
    assert result.filled_shares == Decimal("2")
    assert result.fill_price == Decimal("0.61")


def test_get_order_uses_fallback_request_when_poll_response_omits_fill_fields(monkeypatch):
    """Polling should not collapse executed orders to the stub 1 @ 50c fallback."""
    adapter = KalshiAdapter(api_key_id="id", private_key_base64="key", dry_run=False)
    monkeypatch.setattr(adapter, "_sign_request", lambda *_args, **_kwargs: {})

    response = Mock()
    response.raise_for_status.return_value = None
    response.json.return_value = {"order": {"status": "executed", "order_id": "ex-123"}}
    monkeypatch.setattr(adapter._session, "get", lambda *_args, **_kwargs: response)

    fallback_request = OrderRequest(
        order_id="db-order",
        intent_id="db-order",
        market_id="kalshi:TEST",
        exchange_ticker="TEST",
        action="BUY",
        side="NO",
        shares=Decimal("52"),
        limit_price=Decimal("0.44"),
    )
    result = adapter.get_order("ex-123", fallback_request=fallback_request)

    assert result is not None
    assert result.status == OrderStatus.FILLED
    assert result.filled_shares == Decimal("52")
    assert result.fill_price == Decimal("0.44")


def test_poll_order_fills_after_retries(monkeypatch):
    """Engine should poll pending orders and detect fill."""
    engine = BettingEngine(db_engine=None, dry_run=False, enabled=True)

    # submit_order returns PENDING
    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.PENDING,
        filled_shares=Decimal("0"),
        fill_price=Decimal("0"),
        exchange_order_id="ex-789",
        rejection_reason=None,
    )
    # get_order returns PENDING once, then FILLED
    poll_results = [
        Mock(
            status=OrderStatus.PENDING,
            filled_shares=Decimal("0"),
            fill_price=Decimal("0"),
            exchange_order_id="ex-789",
            order_id="poll",
            intent_id="poll",
        ),
        Mock(
            status=OrderStatus.FILLED,
            filled_shares=Decimal("3"),
            fill_price=Decimal("0.55"),
            exchange_order_id="ex-789",
            order_id="poll",
            intent_id="poll",
            rejection_reason=None,
        ),
    ]
    mock_adapter.get_order.side_effect = poll_results
    mock_adapter.get_market.return_value = None
    engine._adapter = mock_adapter

    # Patch sleep to avoid real delays
    monkeypatch.setattr("ai_prophet_core.betting.engine.time.sleep", lambda _: None)

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.72},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="test-model",
    )

    assert len(results) == 1
    assert results[0].status == "FILLED"
    assert mock_adapter.get_order.call_count == 2


# ── New tests: portfolio context ───────────────────────────────────


def test_strategy_receives_portfolio():
    """Custom strategy can access portfolio context via self.portfolio."""
    captured = {}

    class PortfolioAwareStrategy(BettingStrategy):
        name = "portfolio-aware"

        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            captured["portfolio"] = self.portfolio
            return BetSignal(side="yes", shares=0.1, price=yes_ask, cost=0.1 * yes_ask)

    engine = BettingEngine(
        strategy=PortfolioAwareStrategy(),
        db_engine=None,
        dry_run=True,
        enabled=True,
    )
    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("10"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    portfolio = PortfolioSnapshot(
        cash=Decimal("500"),
        equity=Decimal("1000"),
        total_pnl=Decimal("50"),
        position_count=3,
    )

    engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MKT": 0.72},
        market_prices={"kalshi:TEST-MKT": (0.55, 0.45)},
        source="test-model",
        portfolio=portfolio,
    )

    assert captured["portfolio"] is not None
    assert captured["portfolio"].cash == Decimal("500")
    assert captured["portfolio"].position_count == 3


# ── New tests: max markets per tick ────────────────────────────────


def test_max_markets_per_tick_caps_orders():
    """When more signals than max, only top-edge ones are placed."""

    class AlwaysBet(BettingStrategy):
        name = "always-bet"

        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            return BetSignal(
                side="yes", shares=0.1, price=yes_ask, cost=0.1 * yes_ask,
            )

    engine = BettingEngine(
        strategy=AlwaysBet(),
        db_engine=None,
        dry_run=True,
        enabled=True,
        max_markets_per_tick=2,
    )
    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("10"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    forecasts = {
        "kalshi:MKT-A": 0.72,  # edge = |0.72 - 0.55| = 0.17
        "kalshi:MKT-B": 0.90,  # edge = |0.90 - 0.55| = 0.35 (biggest)
        "kalshi:MKT-C": 0.60,  # edge = |0.60 - 0.55| = 0.05 (smallest)
    }
    prices = {
        "kalshi:MKT-A": (0.55, 0.45),
        "kalshi:MKT-B": (0.55, 0.45),
        "kalshi:MKT-C": (0.55, 0.45),
    }

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts=forecasts,
        market_prices=prices,
        source="test-model",
    )

    placed = [r for r in results if r.order_placed]
    dropped = [r for r in results if not r.order_placed and r.signal is not None]

    assert len(placed) == 2
    assert len(dropped) == 1
    assert mock_adapter.submit_order.call_count == 2


# ── Rebalancing strategy tests ─────────────────────────────────────


def _rebal(cash=100.0, shares=0, side=None):
    """Create a RebalancingStrategy pre-loaded with a portfolio snapshot."""
    strategy = RebalancingStrategy()
    strategy._portfolio = PortfolioSnapshot(
        cash=Decimal(str(cash)),
        market_position_shares=Decimal(str(shares)),
        market_position_side=side,
    )
    return strategy


def test_rebalancing_sell_down_on_edge_flip():
    """Hold 3 NO, edge flips to +3pp → should sell all 3 NO + buy 3 YES."""
    strategy = _rebal(cash=100.0, shares=3, side="no")
    # p_yes=0.15, yes_ask=0.12 → target=+0.03, current=-0.03, delta=+0.06
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.15, yes_ask=0.12, no_ask=0.88)
    assert signal is not None
    assert signal.side == "yes"
    # delta=0.06 → 6 contracts (3 sell NO + 3 buy YES)
    assert round(signal.shares * 100) == 6
    assert signal.metadata["sell_portion"] > 0


def test_rebalancing_flattens_inside_widened_spread_with_existing_position():
    """Inside the widened no-trade band, an existing position should be flattened."""
    strategy = _rebal(cash=0.0, shares=3, side="no")
    # With the 1% widened band, [0.12, 0.15] is a no-trade region for flat books.
    # Since we already hold 3 NO, the strategy should flatten instead of buying through it.
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.15, yes_ask=0.14, no_ask=0.87)
    assert signal is not None
    assert signal.side == "yes"
    assert round(signal.shares * 100) == 3
    assert signal.metadata["sell_portion"] > 0
    assert signal.metadata["buy_portion"] == 0
    assert signal.metadata["target"] == 0.0
    assert signal.metadata["flatten_reason"] == "WITHIN_SPREAD"


def test_rebalancing_partial_sell_down_same_side():
    """Hold 20 NO, target is 15 NO outside the widened no-trade band."""
    strategy = _rebal(cash=100.0, shares=20, side="no")
    # p_yes=0.15, yes_ask=0.30 → target=-0.15, current=-0.20, delta=+0.05
    # With no-trade band [0.27, 0.32], this remains an actionable sell-down.
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.15, yes_ask=0.30, no_ask=0.71)
    assert signal is not None
    assert signal.side == "yes"
    # delta=+0.05 → 5 contracts; all from selling existing NO (sell_portion=0.05)
    assert round(signal.shares * 100) == 5
    assert signal.metadata["sell_portion"] > 0


def test_rebalancing_pure_buy_no_position():
    """No existing position, edge negative → pure BUY NO, no sell-down."""
    strategy = _rebal(cash=100.0, shares=0, side=None)
    # p_yes=0.12, yes_ask=0.15 → target=-0.03, current=0, delta=-0.03
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.12, yes_ask=0.15, no_ask=0.86)
    assert signal is not None
    assert signal.side == "no"
    assert round(signal.shares * 100) == 3
    assert signal.metadata["sell_portion"] == 0
    assert signal.metadata["buy_portion"] > 0


def test_rebalancing_holds_when_flat_inside_widened_spread():
    strategy = _rebal(cash=100.0, shares=0, side=None)
    # With buffer, market band is [0.54, 0.56], so 0.545 should still HOLD when flat.
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.545, yes_ask=0.55, no_ask=0.45)
    assert signal is None


def test_rebalancing_flattens_existing_position_inside_widened_spread():
    strategy = _rebal(cash=100.0, shares=10, side="yes")
    signal = strategy.evaluate("kalshi:TEST", p_yes=0.545, yes_ask=0.55, no_ask=0.45)
    assert signal is not None
    assert signal.side == "no"
    assert round(signal.shares * 100) == 10
    assert signal.metadata["target"] == 0.0
    assert signal.metadata["flatten_reason"] == "WITHIN_SPREAD"
