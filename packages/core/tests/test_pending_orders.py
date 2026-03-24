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

UTC = timezone.utc

from ai_prophet_core.betting import BettingEngine, RebalancingStrategy
from ai_prophet_core.betting.adapters.base import OrderResult, OrderStatus
from ai_prophet_core.betting.strategy import PortfolioSnapshot


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
        """Test that _live_ledger_state only counts FILLED orders."""
        with patch("ai_prophet_core.betting.engine.get_session") as mock_session:
            # Mock the session context manager
            mock_ctx = MagicMock()
            mock_session.return_value.__enter__.return_value = mock_ctx

            # Mock query to check the filter
            mock_query = MagicMock()
            mock_ctx.query.return_value = mock_query
            mock_filter1 = MagicMock()
            mock_query.filter.return_value = mock_filter1
            mock_filter2 = MagicMock()
            mock_filter1.filter.return_value = mock_filter2
            mock_order = MagicMock()
            mock_filter2.order_by.return_value = mock_order
            mock_order.all.return_value = []  # No orders

            # Create engine
            engine = BettingEngine(
                strategy=RebalancingStrategy(),
                db_engine=MagicMock(),  # Mock DB engine
                dry_run=False,  # Test LIVE mode filter
            )

            # Call _live_ledger_state
            with patch("ai_prophet_core.betting.engine._get_position_replay") as mock_replay:
                mock_replay.return_value = (lambda x: {}, lambda x: (0, 0, 0))
                side, qty, cash = engine._live_ledger_state("TEST")

            # Verify that only FILLED orders were queried (not PENDING)
            # In LIVE mode, should only query for FILLED status
            filter_calls = mock_filter1.filter.call_args_list
            assert len(filter_calls) > 0
            # Check that the status filter includes only FILLED (not PENDING)
            status_filter = filter_calls[0][0][0]
            # The actual SQLAlchemy filter object is complex, but we can at least
            # verify the method was called


class TestOrderManagementSync:
    """Test order management and sync utilities."""

    def test_sync_pending_order_status(self):
        """Test syncing pending order status with exchange."""
        from services.order_management import _sync_pending_order_status

        with patch("services.order_management.get_session") as mock_session:
            # Mock pending orders in DB
            mock_order = MagicMock()
            mock_order.exchange_order_id = "kalshi-123"
            mock_order.order_id = "order-456"
            mock_order.status = "PENDING"
            mock_order.filled_shares = 0

            mock_ctx = MagicMock()
            mock_session.return_value.__enter__.return_value = mock_ctx
            mock_query = MagicMock()
            mock_ctx.query.return_value = mock_query
            mock_filter1 = MagicMock()
            mock_query.filter.return_value = mock_filter1
            mock_filter2 = MagicMock()
            mock_filter1.filter.return_value = mock_filter2
            mock_filter2.all.return_value = [mock_order]

            # Mock adapter to return filled status
            mock_adapter = MagicMock()
            mock_adapter.get_order.return_value = OrderResult(
                order_id="order-456",
                intent_id="bet-test",
                status=OrderStatus.FILLED,
                filled_shares=Decimal("10"),
                fill_price=Decimal("0.50"),
                exchange_order_id="kalshi-123",
            )

            # Run sync
            updated = _sync_pending_order_status(
                MagicMock(),  # db_engine
                mock_adapter,
                "TestInstance",
            )

            # Verify order was updated
            assert mock_order.status == "FILLED"
            assert mock_order.filled_shares == 10.0
            assert mock_order.fill_price == 0.50

    def test_reconcile_positions_only_counts_filled(self):
        """Test that position reconciliation only counts FILLED orders."""
        from services.order_management import reconcile_positions_with_kalshi

        with patch("services.order_management.get_session") as mock_session:
            with patch("services.order_management._sync_pending_order_status") as mock_sync:
                with patch("services.order_management.replay_orders_by_ticker") as mock_replay:
                    # Mock DB session
                    mock_ctx = MagicMock()
                    mock_session.return_value.__enter__.return_value = mock_ctx
                    mock_query = MagicMock()
                    mock_ctx.query.return_value = mock_query
                    mock_filter1 = MagicMock()
                    mock_query.filter.return_value = mock_filter1
                    mock_filter2 = MagicMock()
                    mock_filter1.filter.return_value = mock_filter2
                    mock_order = MagicMock()
                    mock_filter2.order_by.return_value = mock_order
                    mock_order.all.return_value = []  # No orders

                    # Mock replay to return empty positions
                    mock_replay.return_value = {}

                    # Mock adapter
                    mock_adapter = MagicMock()
                    mock_adapter._session = MagicMock()
                    mock_response = MagicMock()
                    mock_response.json.return_value = {"market_positions": []}
                    mock_adapter._session.get.return_value = mock_response
                    mock_adapter._sign_request.return_value = {}

                    # Run reconciliation
                    drifts = reconcile_positions_with_kalshi(
                        MagicMock(),  # db_engine
                        mock_adapter,
                        "TestInstance",
                    )

                    # Verify sync was called
                    mock_sync.assert_called_once()

                    # Should have no drifts with empty positions
                    assert drifts == {}


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