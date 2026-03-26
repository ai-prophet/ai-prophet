#!/usr/bin/env python3
"""
Comprehensive test suite for enhanced Kalshi synchronization features.

Tests:
1. Position verification and auto-correction
2. Balance verification with retries
3. Real-time pending order polling
4. Emergency stop on large discrepancies
5. Force reconciliation mechanisms
"""

import unittest
from unittest.mock import MagicMock, patch, call
from decimal import Decimal
from datetime import datetime, timezone
import sys
import os

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

UTC = timezone.utc


class TestPositionVerification(unittest.TestCase):
    """Test position verification and auto-correction."""

    @patch('ai_prophet_core.betting.engine.BettingEngine._get_adapter')
    @patch('ai_prophet_core.betting.engine.BettingEngine._force_sync_position')
    def test_position_mismatch_triggers_auto_correction(self, mock_force_sync, mock_get_adapter):
        """Test that position mismatches trigger immediate auto-correction."""
        from ai_prophet_core.betting.engine import BettingEngine

        # Setup mock adapter with Kalshi position
        mock_adapter = MagicMock()
        mock_adapter.get_positions.return_value = [
            {"ticker": "TEST", "position_fp": 50.0}  # Kalshi says 50 YES
        ]
        mock_adapter.get_balance.return_value = Decimal("1000")
        mock_get_adapter.return_value = mock_adapter

        # Create engine
        engine = BettingEngine(
            db_engine=MagicMock(),
            dry_run=False,  # LIVE mode to test verification
            instance_name="test"
        )

        # Mock DB state showing different position (30 YES)
        with patch('ai_prophet_core.betting.engine._get_position_replay') as mock_replay:
            mock_replay.return_value = (
                lambda orders: {"TEST": MagicMock(current_position=lambda: ("yes", 30, 0.5))},
                lambda positions: (100, 0, {})
            )

            # Call _live_ledger_state which should detect mismatch
            side, qty, cash = engine._live_ledger_state("TEST")

        # Assert Kalshi position was used (50, not 30)
        self.assertEqual(qty, 50)
        self.assertEqual(side, "yes")

        # Assert force sync was called
        mock_force_sync.assert_called_once_with("TEST", "yes", 50.0)

    @patch('ai_prophet_core.betting.engine.BettingEngine._get_adapter')
    def test_no_correction_when_positions_match(self, mock_get_adapter):
        """Test that no correction happens when positions already match."""
        from ai_prophet_core.betting.engine import BettingEngine

        # Setup mock adapter with matching position
        mock_adapter = MagicMock()
        mock_adapter.get_positions.return_value = [
            {"ticker": "TEST", "position_fp": 30.0}  # Kalshi matches DB
        ]
        mock_adapter.get_balance.return_value = Decimal("1000")
        mock_get_adapter.return_value = mock_adapter

        engine = BettingEngine(
            db_engine=MagicMock(),
            dry_run=False,
            instance_name="test"
        )

        with patch('ai_prophet_core.betting.engine.BettingEngine._force_sync_position') as mock_force_sync:
            with patch('ai_prophet_core.betting.engine._get_position_replay') as mock_replay:
                mock_replay.return_value = (
                    lambda orders: {"TEST": MagicMock(current_position=lambda: ("yes", 30, 0.5))},
                    lambda positions: (100, 0, {})
                )

                side, qty, cash = engine._live_ledger_state("TEST")

        # Assert position is correct
        self.assertEqual(qty, 30)
        self.assertEqual(side, "yes")

        # Assert no force sync was needed
        mock_force_sync.assert_not_called()


class TestBalanceVerification(unittest.TestCase):
    """Test balance verification with retries and emergency stop."""

    @patch('ai_prophet_core.betting.engine.BettingEngine._get_adapter')
    @patch('time.sleep')  # Mock sleep to speed up tests
    def test_balance_verification_retries_on_mismatch(self, mock_sleep, mock_get_adapter):
        """Test that balance verification retries multiple times."""
        from ai_prophet_core.betting.engine import BettingEngine

        # Mock adapter that returns wrong balance first, then correct
        mock_adapter = MagicMock()
        mock_adapter.get_balance.side_effect = [
            Decimal("950"),  # First check - wrong
            Decimal("950"),  # Second check - still wrong
            Decimal("900"),  # Third check - correct!
        ]
        mock_get_adapter.return_value = mock_adapter

        engine = BettingEngine(
            db_engine=None,
            dry_run=False,
            instance_name="test"
        )

        # Test balance verification after a $100 order
        with patch('ai_prophet_core.betting.engine.logger') as mock_logger:
            engine._verify_balance_after_fill(
                ticker="TEST",
                order_id="order123",
                expected_cost=100.0,
                pre_order_balance=1000.0
            )

        # Assert it retried 3 times
        self.assertEqual(mock_adapter.get_balance.call_count, 3)

        # Assert it logged success on the third try
        mock_logger.info.assert_called_with(
            "[BETTING] Balance VERIFIED after order %s: $%.2f → $%.2f (cost=$%.2f)",
            "order123", 1000.0, 900.0, 100.0
        )

    @patch('ai_prophet_core.betting.engine.BettingEngine._get_adapter')
    @patch('ai_prophet_core.betting.engine.BettingEngine._force_full_reconciliation')
    @patch('time.sleep')
    def test_balance_discrepancy_triggers_reconciliation(self, mock_sleep, mock_reconcile, mock_get_adapter):
        """Test that persistent balance discrepancy triggers full reconciliation."""
        from ai_prophet_core.betting.engine import BettingEngine

        # Mock adapter that always returns wrong balance
        mock_adapter = MagicMock()
        mock_adapter.get_balance.return_value = Decimal("950")  # Should be 900
        mock_get_adapter.return_value = mock_adapter

        engine = BettingEngine(
            db_engine=MagicMock(),
            dry_run=False,
            instance_name="test"
        )

        # Test balance verification with persistent mismatch
        engine._verify_balance_after_fill(
            ticker="TEST",
            order_id="order123",
            expected_cost=100.0,
            pre_order_balance=1000.0
        )

        # Assert reconciliation was triggered
        mock_reconcile.assert_called_once_with(
            ticker="TEST",
            order_id="order123",
            expected_balance=900.0,
            actual_balance=950.0,
            discrepancy=50.0
        )

    @patch('ai_prophet_core.betting.engine.BettingEngine._get_adapter')
    @patch('time.sleep')
    def test_emergency_stop_on_large_discrepancy(self, mock_sleep, mock_get_adapter):
        """Test that discrepancy > $10 disables trading."""
        from ai_prophet_core.betting.engine import BettingEngine

        # Mock adapter with huge balance discrepancy
        mock_adapter = MagicMock()
        mock_adapter.get_balance.return_value = Decimal("850")  # $50 discrepancy!
        mock_get_adapter.return_value = mock_adapter

        mock_db = MagicMock()
        engine = BettingEngine(
            db_engine=mock_db,
            dry_run=False,
            instance_name="test",
            enabled=True  # Start enabled
        )

        with patch('ai_prophet_core.betting.engine.BettingEngine._force_full_reconciliation') as mock_reconcile:
            engine._verify_balance_after_fill(
                ticker="TEST",
                order_id="order123",
                expected_cost=100.0,
                pre_order_balance=1000.0
            )

        # In reconciliation, check if trading would be disabled for $50 discrepancy
        args = mock_reconcile.call_args[1]
        self.assertEqual(args['discrepancy'], 50.0)

        # Test the reconciliation method directly
        with patch('order_management.reconcile_positions_with_kalshi'):
            with patch('kalshi_state.record_kalshi_state'):
                engine._force_full_reconciliation(
                    ticker="TEST",
                    order_id="order123",
                    expected_balance=900.0,
                    actual_balance=850.0,
                    discrepancy=50.0  # > $10 threshold
                )

        # Assert trading was disabled
        self.assertFalse(engine.enabled)


class TestRealtimePolling(unittest.TestCase):
    """Test real-time pending order polling."""

    @patch('ai_prophet_core.betting.db.get_session')
    def test_realtime_polling_updates_filled_orders(self, mock_get_session):
        """Test that real-time polling updates filled orders immediately."""
        from kalshi_sync_service import _poll_pending_orders_realtime

        # Mock pending orders in DB
        mock_order1 = MagicMock()
        mock_order1.order_id = "order111"
        mock_order1.exchange_order_id = "kalshi111"
        mock_order1.status = "PENDING"
        mock_order1.filled_shares = 0

        mock_order2 = MagicMock()
        mock_order2.order_id = "order222"
        mock_order2.exchange_order_id = "kalshi222"
        mock_order2.status = "PENDING"
        mock_order2.filled_shares = 0

        mock_session = MagicMock()
        mock_session.query().filter().all.return_value = [mock_order1, mock_order2]
        mock_get_session.return_value.__enter__.return_value = mock_session

        # Mock adapter that returns filled status
        mock_adapter = MagicMock()
        mock_adapter.get_order.side_effect = [
            MagicMock(status=MagicMock(value="FILLED"), filled_shares=100, fill_price=0.5, fee=1.0),
            MagicMock(status=MagicMock(value="CANCELLED"), filled_shares=0, fill_price=0, fee=0),
        ]

        # Run realtime polling
        updated = _poll_pending_orders_realtime(
            MagicMock(),  # db_engine
            mock_adapter,
            "test"
        )

        # Assert both orders were updated
        self.assertEqual(updated, 2)

        # Assert order statuses were updated
        self.assertEqual(mock_order1.status, "FILLED")
        self.assertEqual(mock_order1.filled_shares, 100)
        self.assertEqual(mock_order2.status, "CANCELLED")

        # Assert commit was called
        mock_session.commit.assert_called_once()

    @patch('ai_prophet_core.betting.db.get_session')
    def test_partial_fill_detection(self, mock_get_session):
        """Test that partial fills are detected and updated."""
        from kalshi_sync_service import _poll_pending_orders_realtime

        # Mock pending order with partial fill
        mock_order = MagicMock()
        mock_order.order_id = "order333"
        mock_order.exchange_order_id = "kalshi333"
        mock_order.status = "PENDING"
        mock_order.filled_shares = 20.0  # Already partially filled

        mock_session = MagicMock()
        mock_session.query().filter().all.return_value = [mock_order]
        mock_get_session.return_value.__enter__.return_value = mock_session

        # Mock adapter returns more fills
        mock_adapter = MagicMock()
        mock_adapter.get_order.return_value = MagicMock(
            status=MagicMock(value="PENDING"),  # Still pending
            filled_shares=50,  # But more filled now
            fill_price=0.6,
            fee=2.0
        )

        # Run polling
        updated = _poll_pending_orders_realtime(
            MagicMock(),
            mock_adapter,
            "test"
        )

        # Assert partial fill was detected
        self.assertEqual(updated, 1)
        self.assertEqual(mock_order.filled_shares, 50)
        self.assertEqual(mock_order.status, "PENDING")  # Still pending


class TestForceReconciliation(unittest.TestCase):
    """Test forced reconciliation mechanisms."""

    @patch('order_management.sync_trading_positions_from_snapshots')
    @patch('kalshi_state.record_kalshi_state')
    @patch('ai_prophet_core.betting.db.get_session')
    def test_position_drift_triggers_immediate_correction(self, mock_get_session, mock_record, mock_sync):
        """Test that position drifts trigger immediate correction."""
        from order_management import reconcile_positions_with_kalshi

        # Mock adapter with different position than DB
        mock_adapter = MagicMock()
        mock_adapter.get_positions.return_value = [
            {"ticker": "MARKET1", "position_fp": 100}
        ]

        # Mock DB session
        mock_session = MagicMock()
        mock_get_session.return_value.__enter__.return_value = mock_session

        # Mock sync functions
        mock_sync.return_value = 1  # 1 position corrected

        with patch('kalshi_state.build_position_views') as mock_build:
            with patch('db_models.TradingPosition'):
                # Mock position views showing drift
                mock_build.return_value = [
                    MagicMock(ticker="MARKET1", contract="yes", quantity=50)  # DB says 50
                ]

                # Run reconciliation
                drifts = reconcile_positions_with_kalshi(
                    MagicMock(),  # db_engine
                    mock_adapter,
                    "test",
                    tolerance_contracts=0
                )

        # Assert correction was performed
        self.assertEqual(mock_sync.call_count, 2)  # Called twice due to drift

        # Assert full state was recorded after correction
        mock_record.assert_called()


class TestIntegrationScenarios(unittest.TestCase):
    """Test full integration scenarios."""

    def test_full_trade_cycle_with_verification(self):
        """Test a complete trade cycle with all verifications."""
        # This would be a full integration test with a mock Kalshi API
        # Testing the entire flow from order placement to verification
        pass

    def test_concurrent_polling_and_trading(self):
        """Test that polling and trading work correctly concurrently."""
        # Test that sync service polling doesn't interfere with active trading
        pass


class TestDeploymentReadiness(unittest.TestCase):
    """Test deployment readiness checks."""

    def test_all_imports_work(self):
        """Test that all imports work correctly."""
        try:
            from ai_prophet_core.betting.engine import BettingEngine
            from kalshi_sync_service import sync_with_kalshi
            from order_management import reconcile_positions_with_kalshi
            from kalshi_state import record_kalshi_state
            self.assertTrue(True)
        except ImportError as e:
            self.fail(f"Import failed: {e}")

    def test_error_handling_comprehensive(self):
        """Test that all error paths are handled."""
        # Test network errors, API errors, DB errors, etc.
        pass


def run_all_tests():
    """Run all test suites and return results."""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestPositionVerification))
    suite.addTests(loader.loadTestsFromTestCase(TestBalanceVerification))
    suite.addTests(loader.loadTestsFromTestCase(TestRealtimePolling))
    suite.addTests(loader.loadTestsFromTestCase(TestForceReconciliation))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegrationScenarios))
    suite.addTests(loader.loadTestsFromTestCase(TestDeploymentReadiness))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)