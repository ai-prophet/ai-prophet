#!/usr/bin/env python3
"""
Deployment validation script for enhanced Kalshi sync.

Run this after deployment to verify everything is working correctly.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

# Add paths
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packages", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

UTC = timezone.utc

def test_imports():
    """Test that all modules import correctly."""
    print("Testing imports...")
    try:
        from ai_prophet_core.betting.engine import BettingEngine
        print("✓ BettingEngine imports")

        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
        print("✓ KalshiAdapter imports")

        from kalshi_sync_service import sync_with_kalshi, _poll_pending_orders_realtime
        print("✓ kalshi_sync_service imports")

        from order_management import reconcile_positions_with_kalshi
        print("✓ order_management imports")

        from kalshi_state import record_kalshi_state
        print("✓ kalshi_state imports")

        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def test_kalshi_connection():
    """Test connection to Kalshi API."""
    print("\nTesting Kalshi connection...")
    try:
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
        from instance_config import get_current_instance_name, get_instance_env

        instance_name = get_current_instance_name()
        print(f"Instance: {instance_name}")

        adapter = KalshiAdapter(
            api_key_id=get_instance_env("KALSHI_API_KEY_ID", instance_name, default=""),
            private_key_base64=get_instance_env("KALSHI_PRIVATE_KEY_B64", instance_name, default=""),
        )

        # Test balance fetch
        balance = adapter.get_balance()
        print(f"✓ Balance: ${balance}")

        # Test positions fetch
        positions = adapter.get_positions()
        print(f"✓ Positions: {len(positions)} markets")

        # Test orders fetch
        orders = adapter.get_orders(status="resting")
        print(f"✓ Pending orders: {len(orders)}")

        adapter.close()
        return True
    except Exception as e:
        print(f"✗ Kalshi connection failed: {e}")
        return False


def test_position_verification():
    """Test position verification feature."""
    print("\nTesting position verification...")
    try:
        from ai_prophet_core.betting.engine import BettingEngine
        from ai_prophet_core.betting.db import create_db_engine

        db_engine = create_db_engine()
        engine = BettingEngine(
            db_engine=db_engine,
            dry_run=False,  # Test in live mode
            instance_name=get_current_instance_name()
        )

        # Test verification for a sample ticker
        test_ticker = "TEST_MARKET"  # Change to a real ticker if needed

        print(f"Testing position verification for {test_ticker}")
        kalshi_pos = engine._verify_position_with_kalshi(test_ticker)

        if kalshi_pos is not None:
            print(f"✓ Kalshi position: {kalshi_pos}")
        else:
            print(f"✓ No position or verification working")

        return True
    except Exception as e:
        print(f"✗ Position verification failed: {e}")
        return False


def test_realtime_polling():
    """Test real-time polling functionality."""
    print("\nTesting real-time polling...")
    try:
        from kalshi_sync_service import _poll_pending_orders_realtime
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
        from ai_prophet_core.betting.db import create_db_engine
        from instance_config import get_current_instance_name, get_instance_env

        instance_name = get_current_instance_name()
        db_engine = create_db_engine()

        adapter = KalshiAdapter(
            api_key_id=get_instance_env("KALSHI_API_KEY_ID", instance_name, default=""),
            private_key_base64=get_instance_env("KALSHI_PRIVATE_KEY_B64", instance_name, default=""),
        )

        updated = _poll_pending_orders_realtime(db_engine, adapter, instance_name)
        print(f"✓ Polled pending orders: {updated} updated")

        adapter.close()
        return True
    except Exception as e:
        print(f"✗ Real-time polling failed: {e}")
        return False


def check_recent_errors():
    """Check for recent critical errors in the database."""
    print("\nChecking for recent errors...")
    try:
        from ai_prophet_core.betting.db import create_db_engine, get_session
        from db_models import SystemLog

        db_engine = create_db_engine()
        cutoff = datetime.now(UTC) - timedelta(hours=1)

        with get_session(db_engine) as session:
            critical_logs = session.query(SystemLog).filter(
                SystemLog.level.in_(['CRITICAL', 'ERROR', 'EMERGENCY']),
                SystemLog.created_at >= cutoff
            ).order_by(SystemLog.created_at.desc()).limit(10).all()

            if critical_logs:
                print(f"⚠ Found {len(critical_logs)} critical events in last hour:")
                for log in critical_logs[:5]:
                    print(f"  - [{log.level}] {log.message[:100]}")
            else:
                print("✓ No critical errors in last hour")

        return len(critical_logs) == 0
    except Exception as e:
        print(f"✗ Error checking database: {e}")
        return False


def check_position_consistency():
    """Check if positions are consistent between DB and Kalshi."""
    print("\nChecking position consistency...")
    try:
        from order_management import reconcile_positions_with_kalshi
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
        from ai_prophet_core.betting.db import create_db_engine
        from instance_config import get_current_instance_name, get_instance_env

        instance_name = get_current_instance_name()
        db_engine = create_db_engine()

        adapter = KalshiAdapter(
            api_key_id=get_instance_env("KALSHI_API_KEY_ID", instance_name, default=""),
            private_key_base64=get_instance_env("KALSHI_PRIVATE_KEY_B64", instance_name, default=""),
        )

        drifts = reconcile_positions_with_kalshi(
            db_engine,
            adapter,
            instance_name,
            tolerance_contracts=0,  # Zero tolerance for validation
            sync_pending_orders=False
        )

        if drifts:
            print(f"⚠ Found {len(drifts)} position drifts:")
            for ticker, (db_qty, kalshi_qty) in list(drifts.items())[:5]:
                print(f"  - {ticker}: DB={db_qty}, Kalshi={kalshi_qty}")
        else:
            print("✓ All positions consistent")

        adapter.close()
        return len(drifts) == 0
    except Exception as e:
        print(f"✗ Position consistency check failed: {e}")
        return False


def run_full_sync_test():
    """Run a full sync cycle to test all components."""
    print("\nRunning full sync test...")
    try:
        from kalshi_sync_service import sync_with_kalshi
        from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
        from ai_prophet_core.betting.db import create_db_engine
        from instance_config import get_current_instance_name, get_instance_env

        instance_name = get_current_instance_name()
        db_engine = create_db_engine()

        adapter = KalshiAdapter(
            api_key_id=get_instance_env("KALSHI_API_KEY_ID", instance_name, default=""),
            private_key_base64=get_instance_env("KALSHI_PRIVATE_KEY_B64", instance_name, default=""),
            dry_run=True  # Use dry-run for safety
        )

        print("Starting sync...")
        results = sync_with_kalshi(db_engine, adapter, instance_name, dry_run=True)

        print(f"✓ Sync completed:")
        print(f"  - Realtime polls: {results.get('realtime_polls', 0)}")
        print(f"  - Orders updated: {results.get('pending_orders_updated', 0)}")
        print(f"  - Stale cancelled: {results.get('stale_orders_cancelled', 0)}")
        print(f"  - Position drifts: {len(results.get('position_drifts', {}))}")

        adapter.close()
        return len(results.get('errors', [])) == 0
    except Exception as e:
        print(f"✗ Full sync test failed: {e}")
        return False


def main():
    """Run all validation tests."""
    print("=" * 60)
    print("DEPLOYMENT VALIDATION SCRIPT")
    print("=" * 60)

    # Check if we're in production
    is_production = os.getenv("ENVIRONMENT") == "production"
    if is_production:
        print("⚠️  RUNNING IN PRODUCTION MODE")
        response = input("Continue with validation? (yes/no): ")
        if response.lower() != "yes":
            print("Validation cancelled")
            return 1

    results = {}

    # Run all tests
    results['imports'] = test_imports()
    results['kalshi'] = test_kalshi_connection()
    results['verification'] = test_position_verification()
    results['polling'] = test_realtime_polling()
    results['errors'] = check_recent_errors()
    results['consistency'] = check_position_consistency()
    results['sync'] = run_full_sync_test()

    # Summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)

    all_passed = True
    for test, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"{test:20} {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("\n✅ ALL TESTS PASSED - Deployment validated successfully!")
        print("\nRecommended next steps:")
        print("1. Monitor logs for next hour")
        print("2. Check system_logs table periodically")
        print("3. Verify trades execute correctly")
        return 0
    else:
        print("\n❌ VALIDATION FAILED - Review errors above")
        print("\nTroubleshooting:")
        print("1. Check environment variables are set")
        print("2. Verify database connection")
        print("3. Ensure Kalshi credentials are correct")
        print("4. Review error logs for details")
        return 1


if __name__ == "__main__":
    sys.exit(main())