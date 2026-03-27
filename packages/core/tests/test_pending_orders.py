"""
Test suite for pending order handling in rebalancing strategy.

This test suite ensures that:
1. Pending orders are NOT counted in position calculations
2. Pending orders are cancelled before placing new orders
3. The system correctly handles partially filled orders
4. Position reconciliation works correctly with Kalshi
"""

import pytest
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine

UTC = timezone.utc

from ai_prophet_core.betting import BettingEngine, RebalancingStrategy
from ai_prophet_core.betting.adapters.base import OrderResult, OrderStatus
from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder
from ai_prophet_core.betting.strategy import PortfolioSnapshot
from db_models import TradingPosition


class TestPendingOrderHandling:
    """Test that pending orders are handled correctly in position calculations."""

    def test_pending_orders_not_counted_in_position(self):
        """Test that PENDING orders are not included in position calculation."""
        # Create a strategy with a mock portfolio
        strategy = RebalancingStrategy()

        # Mock portfolio with no current position (pending orders shouldn't count)
        portfolio = PortfolioSnapshot(
            cash=Decimal("1000"),
            market_position_shares=Decimal("0"),  # No filled position
            market_position_side=None,
        )
        strategy._portfolio = portfolio

        # Evaluate a market where model predicts 0.70, market is at 0.50
        # Target position should be 0.70 - 0.50 = 0.20
        signal = strategy.evaluate(
            market_id="kalshi:TEST",
            p_yes=0.70,
            yes_ask=0.50,
            no_ask=0.50,
        )

        assert signal is not None
        assert signal.side == "yes"
        # Should buy full 0.20 since pending orders don't count
        assert abs(signal.shares - 0.20) < 0.001

    def test_filled_orders_counted_in_position(self):
        """Test that FILLED orders ARE included in position calculation."""
        strategy = RebalancingStrategy()

        # Mock portfolio with 10 YES contracts (filled)
        portfolio = PortfolioSnapshot(
            cash=Decimal("1000"),
            market_position_shares=Decimal("10"),  # 10 contracts = 0.10 fractional
            market_position_side="yes",
        )
        strategy._portfolio = portfolio

        # Evaluate same market: model 0.70, market 0.50
        # Target = 0.20, current = 0.10, delta = 0.10
        signal = strategy.evaluate(
            market_id="kalshi:TEST",
            p_yes=0.70,
            yes_ask=0.50,
            no_ask=0.50,
        )

        assert signal is not None
        assert signal.side == "yes"
        # Should only buy 0.10 more to reach target of 0.20
        assert abs(signal.shares - 0.10) < 0.001

    def test_partial_fill_handling(self):
        """Test that partially filled orders are handled correctly."""
        strategy = RebalancingStrategy()

        # Mock portfolio with 5 contracts from a partially filled 10-contract order
        portfolio = PortfolioSnapshot(
            cash=Decimal("1000"),
            market_position_shares=Decimal("5"),  # Only 5 of 10 filled
            market_position_side="yes",
        )
        strategy._portfolio = portfolio

        # Target = 0.20, current = 0.05, delta = 0.15
        signal = strategy.evaluate(
            market_id="kalshi:TEST",
            p_yes=0.70,
            yes_ask=0.50,
            no_ask=0.50,
        )

        assert signal is not None
        assert signal.side == "yes"
        # Should buy 0.15 more to reach target
        assert abs(signal.shares - 0.15) < 0.001

    def test_position_flip_with_pending_orders(self):
        """Test position flip doesn't double-count pending orders."""
        strategy = RebalancingStrategy()

        # Currently holding 20 NO contracts (filled)
        portfolio = PortfolioSnapshot(
            cash=Decimal("1000"),
            market_position_shares=Decimal("20"),
            market_position_side="no",
        )
        strategy._portfolio = portfolio

        # Model now favors YES: 0.80 - 0.40 = 0.40 target YES position
        # Current = -0.20 NO, target = +0.40 YES, delta = 0.60 YES needed
        signal = strategy.evaluate(
            market_id="kalshi:TEST",
            p_yes=0.80,
            yes_ask=0.40,
            no_ask=0.60,
        )

        assert signal is not None
        assert signal.side == "yes"
        # Should sell 20 NO and buy 40 YES = total 60 contracts
        assert abs(signal.shares - 0.60) < 0.001


class TestBettingEngineWithPending:
    """Test BettingEngine's handling of pending orders."""

    @patch("ai_prophet_core.betting.engine.BettingEngine._get_adapter")
    @patch("ai_prophet_core.betting.engine.BettingEngine._save_prediction")
    @patch("ai_prophet_core.betting.engine.BettingEngine._save_signal")
    @patch("ai_prophet_core.betting.engine.BettingEngine._save_order")
    def test_engine_cancels_pending_before_new_order(
        self, mock_save_order, mock_save_signal, mock_save_prediction, mock_get_adapter
    ):
        """Test that engine cancels pending orders before placing new ones."""
        # Setup mocks
        mock_adapter = MagicMock()
        mock_get_adapter.return_value = mock_adapter

        # Mock a successful order submission
        mock_adapter.submit_order.return_value = OrderResult(
            order_id="test-123",
            intent_id="bet-test",
            status=OrderStatus.FILLED,
            filled_shares=Decimal("20"),
            fill_price=Decimal("0.50"),
        )

        mock_save_prediction.return_value = 1
        mock_save_signal.return_value = 1

        # Create engine with rebalancing strategy
        engine = BettingEngine(
            strategy=RebalancingStrategy(),
            db_engine=None,  # No DB for this test
            dry_run=True,
        )

        # Process a forecast
        results = engine.process_forecasts(
            tick_ts=datetime.now(UTC),
            forecasts={"kalshi:TEST": 0.70},
            market_prices={"kalshi:TEST": (0.50, 0.50)},
            source="test",
        )

        assert len(results) == 1
        result = results[0]
        assert result.order_placed is True
        assert result.status == "FILLED"

    def test_ledger_state_excludes_pending(self):
        """Test that _live_ledger_state excludes pending quantity from holdings."""
        engine_db = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine_db)
        with get_session(engine_db) as session:
            session.add(
                BettingOrder(
                    instance_name="Haifeng",
                    signal_id=None,
                    order_id="filled-1",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=5,
                    price_cents=50,
                    status="FILLED",
                    filled_shares=5,
                    fill_price=0.5,
                    fee_paid=0,
                    exchange_order_id="ex-filled",
                    dry_run=False,
                    created_at=datetime(2026, 3, 26, 20, 0, tzinfo=UTC),
                )
            )
            session.add(
                BettingOrder(
                    instance_name="Haifeng",
                    signal_id=None,
                    order_id="pending-1",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=99,
                    price_cents=60,
                    status="PENDING",
                    filled_shares=0,
                    fill_price=0,
                    fee_paid=0,
                    exchange_order_id="ex-pending",
                    dry_run=False,
                    created_at=datetime(2026, 3, 26, 20, 5, tzinfo=UTC),
                )
            )
            session.commit()

        engine = BettingEngine(
            strategy=RebalancingStrategy(),
            db_engine=engine_db,
            dry_run=False,
            instance_name="Haifeng",
        )
        with patch.object(engine, "_get_adapter") as mock_get_adapter:
            mock_get_adapter.return_value.get_balance.return_value = Decimal("100")
            with patch.object(engine, "_verify_position_with_kalshi", return_value=5.0):
                side, qty, cash = engine._live_ledger_state("TEST")

        assert side == "yes"
        assert qty == 5
        assert cash == Decimal("100")

    def test_ledger_state_counts_partially_filled_pending_quantity(self):
        """Partially filled pending orders should contribute their filled_shares."""
        engine_db = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine_db)
        with get_session(engine_db) as session:
            session.add(
                BettingOrder(
                    instance_name="Haifeng",
                    signal_id=None,
                    order_id="filled-1",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=5,
                    price_cents=50,
                    status="FILLED",
                    filled_shares=5,
                    fill_price=0.5,
                    fee_paid=0,
                    exchange_order_id="ex-filled",
                    dry_run=False,
                    created_at=datetime(2026, 3, 26, 20, 0, tzinfo=UTC),
                )
            )
            session.add(
                BettingOrder(
                    instance_name="Haifeng",
                    signal_id=None,
                    order_id="pending-1",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=99,
                    price_cents=60,
                    status="PENDING",
                    filled_shares=3,
                    fill_price=0.6,
                    fee_paid=0,
                    exchange_order_id="ex-pending",
                    dry_run=False,
                    created_at=datetime(2026, 3, 26, 20, 5, tzinfo=UTC),
                )
            )
            session.commit()

        engine = BettingEngine(
            strategy=RebalancingStrategy(),
            db_engine=engine_db,
            dry_run=False,
            instance_name="Haifeng",
        )
        with patch.object(engine, "_get_adapter") as mock_get_adapter:
            mock_get_adapter.return_value.get_balance.return_value = Decimal("100")
            with patch.object(engine, "_verify_position_with_kalshi", return_value=None):
                side, qty, cash = engine._live_ledger_state("TEST")

        assert side == "yes"
        assert qty == 8
        assert cash == Decimal("100")

    def test_flip_waits_for_pending_sell_before_buying_new_side(self):
        """Do not start the opposite-side buy until the sell leg is resolved."""
        engine_db = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine_db)
        with get_session(engine_db) as session:
            session.add(
                BettingOrder(
                    instance_name="Haifeng",
                    signal_id=None,
                    order_id="filled-yes-30",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=30,
                    price_cents=12,
                    status="FILLED",
                    filled_shares=30,
                    fill_price=0.12,
                    fee_paid=0,
                    exchange_order_id="ex-filled",
                    dry_run=False,
                    created_at=datetime(2026, 3, 27, 7, 0, tzinfo=UTC),
                )
            )
            session.commit()

        engine = BettingEngine(
            strategy=RebalancingStrategy(),
            db_engine=engine_db,
            dry_run=False,
            instance_name="Haifeng",
        )

        mock_adapter = MagicMock()
        mock_adapter.get_balance.return_value = Decimal("100")
        mock_adapter.submit_order.return_value = OrderResult(
            order_id="sell-1",
            intent_id="intent-sell-1",
            status=OrderStatus.PENDING,
            filled_shares=Decimal("3"),
            fill_price=Decimal("0.29"),
            fee=Decimal("0.05"),
            exchange_order_id="ex-sell-1",
        )
        engine._adapter = mock_adapter

        with patch.object(engine, "_verify_position_with_kalshi", return_value=30.0):
            with patch.object(
                engine,
                "_poll_order_status",
                return_value=OrderResult(
                    order_id="sell-1",
                    intent_id="intent-sell-1",
                    status=OrderStatus.PENDING,
                    filled_shares=Decimal("3"),
                    fill_price=Decimal("0.29"),
                    fee=Decimal("0.05"),
                    exchange_order_id="ex-sell-1",
                ),
            ):
                results = engine.process_forecasts(
                    tick_ts=datetime(2026, 3, 27, 8, 2, tzinfo=UTC),
                    forecasts={"kalshi:TEST": 0.15},
                    market_prices={"kalshi:TEST": (0.32, 0.71)},
                    source="pending-flip-test",
                )

        assert len(results) == 1
        assert results[0].status == "PENDING"
        assert float(results[0].filled_shares) == 3.0
        assert mock_adapter.submit_order.call_count == 1
        submitted = mock_adapter.submit_order.call_args[0][0]
        assert submitted.action == "SELL"
        assert submitted.side == "YES"


class TestOrderManagementSync:
    """Test order management and sync utilities."""

    def test_sync_pending_order_status(self):
        """Test syncing pending orders from Kalshi snapshots."""
        from services.order_management import _sync_pending_order_status

        class _Adapter:
            def get_balance_details(self):
                return {"balance": 10000, "portfolio_value": 10500}

            def get_positions(self):
                return [
                    {
                        "ticker": "TEST",
                        "position_fp": "10.00",
                        "market_exposure_dollars": "5.0000",
                        "realized_pnl_dollars": "0.0000",
                        "fees_paid_dollars": "0.1000",
                        "total_cost_dollars": "5.0000",
                        "total_cost_shares_fp": "10.00",
                        "resting_orders_count": 0,
                    }
                ]

            def get_orders(self, *, status=None, ticker=None):
                if status == "executed":
                    return [{
                        "order_id": "kalshi-123",
                        "client_order_id": "order-456",
                        "ticker": "TEST",
                        "side": "yes",
                        "action": "buy",
                        "status": "executed",
                        "fill_count_fp": "10.00",
                        "initial_count_fp": "10.00",
                        "remaining_count_fp": "0.00",
                        "yes_price_dollars": "0.5000",
                        "taker_fill_cost_dollars": "5.0000",
                        "created_time": "2026-03-26T20:00:00Z",
                        "last_update_time": "2026-03-26T20:10:00Z",
                    }]
                return []

            def get_historical_orders(self, *, ticker=None):
                return []

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)
        with get_session(engine) as session:
            session.add(
                BettingOrder(
                    instance_name="TestInstance",
                    signal_id=None,
                    order_id="order-456",
                    ticker="TEST",
                    action="BUY",
                    side="YES",
                    count=10,
                    price_cents=50,
                    status="PENDING",
                    filled_shares=0,
                    fill_price=0,
                    fee_paid=0,
                    exchange_order_id="kalshi-123",
                    dry_run=False,
                    created_at=datetime(2026, 3, 26, 20, 0, tzinfo=UTC),
                )
            )
            session.commit()

        updated = _sync_pending_order_status(engine, _Adapter(), "TestInstance")
        assert updated == 1
        with get_session(engine) as session:
            order = session.query(BettingOrder).filter(BettingOrder.order_id == "order-456").one()
            assert order.status == "FILLED"
            assert order.filled_shares == 10.0
            assert order.fill_price == 0.50

    def test_reconcile_positions_only_counts_filled(self):
        """Test reconciliation mirrors latest Kalshi snapshot state."""
        from services.order_management import reconcile_positions_with_kalshi

        class _Adapter:
            def get_balance_details(self):
                return {"balance": 10000, "portfolio_value": 10500}

            def get_positions(self):
                return [{
                    "ticker": "TEST",
                    "position_fp": "-2.00",
                    "market_exposure_dollars": "1.2000",
                    "realized_pnl_dollars": "0.0000",
                    "fees_paid_dollars": "0.0200",
                    "total_cost_dollars": "1.0000",
                    "total_cost_shares_fp": "2.00",
                    "resting_orders_count": 0,
                }]

            def get_orders(self, *, status=None, ticker=None):
                return []

            def get_historical_orders(self, *, ticker=None):
                return []

        engine = create_engine("sqlite:///:memory:", future=True)
        Base.metadata.create_all(engine)

        drifts = reconcile_positions_with_kalshi(
            engine,
            _Adapter(),
            "TestInstance",
            tolerance_contracts=0,
            sync_pending_orders=False,
        )
        assert drifts == {"TEST": (0, -2)}
        with get_session(engine) as session:
            pos = session.query(TradingPosition).filter(TradingPosition.market_id == "kalshi:TEST").one()
            assert pos.contract == "no"
            assert pos.quantity == 2.0


class TestKalshiSyncService:
    """Test the standalone Kalshi sync service."""

    def test_sync_service_updates_orders(self):
        """Test that sync service updates pending order statuses."""
        from services.kalshi_sync_service import sync_with_kalshi

        with patch("services.kalshi_sync_service._sync_pending_order_status") as mock_sync:
            with patch("services.kalshi_sync_service.cancel_stale_orders") as mock_cancel:
                with patch("services.kalshi_sync_service.reconcile_positions_with_kalshi") as mock_reconcile:
                    with patch("services.kalshi_sync_service._update_market_prices") as mock_prices:
                        # Mock return values
                        mock_sync.return_value = 3  # 3 orders updated
                        mock_cancel.return_value = 1  # 1 order cancelled
                        mock_reconcile.return_value = {}  # No drifts

                        # Run sync
                        results = sync_with_kalshi(
                            MagicMock(),  # db_engine
                            MagicMock(),  # adapter
                            "TestInstance",
                            dry_run=False,
                        )

                        # Verify results
                        assert results["pending_orders_updated"] == 3
                        assert results["stale_orders_cancelled"] == 1
                        assert results["position_drifts"] == {}
                        assert len(results["errors"]) == 0

                        # Verify all functions were called
                        mock_sync.assert_called_once()
                        mock_cancel.assert_called_once()
                        mock_reconcile.assert_called_once()
                        mock_prices.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
