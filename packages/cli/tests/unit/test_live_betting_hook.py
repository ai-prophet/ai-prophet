from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

from sqlalchemy import create_engine

from ai_prophet_core.betting.adapters.base import OrderStatus
from ai_prophet_core.betting.engine import BettingEngine
from ai_prophet_core.betting.strategy import BetSignal, BettingStrategy


def test_engine_processes_forecasts_and_places_orders():
    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        db_engine=db_engine,
        paper=True,
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

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MARKET": 0.72},
        market_prices={"kalshi:TEST-MARKET": (0.55, 0.45)},
        source="model-a",
    )

    assert len(results) == 1
    assert results[0].order_placed is True
    assert results[0].signal is not None
    assert results[0].signal.side == "yes"
    mock_adapter.submit_order.assert_called_once()


def test_engine_disabled_skips():
    engine = BettingEngine(enabled=False)
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MARKET": 0.72},
        market_prices={"kalshi:TEST-MARKET": (0.55, 0.45)},
        source="model-a",
    )
    assert results == []


def test_engine_with_custom_strategy():
    class FixedBetStrategy(BettingStrategy):
        name = "fixed"

        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            return BetSignal(side="yes", shares=0.5, price=yes_ask, cost=0.5 * yes_ask)

    db_engine = create_engine("sqlite:///:memory:")
    engine = BettingEngine(
        strategy=FixedBetStrategy(),
        db_engine=db_engine,
        paper=True,
        enabled=True,
    )

    mock_adapter = Mock()
    mock_adapter.submit_order.return_value = Mock(
        status=OrderStatus.DRY_RUN,
        filled_shares=Decimal("50"),
        fill_price=Decimal("0.55"),
        exchange_order_id="dry-run-123",
        rejection_reason=None,
    )
    engine._adapter = mock_adapter

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MARKET": 0.50},
        market_prices={"kalshi:TEST-MARKET": (0.55, 0.45)},
        source="custom-model",
    )

    assert len(results) == 1
    assert results[0].order_placed is True
    assert results[0].signal.shares == 0.5
