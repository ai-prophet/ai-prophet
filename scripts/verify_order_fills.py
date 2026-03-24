#!/usr/bin/env python3
"""
Verify and fix order fill discrepancies by checking directly with Kalshi.

This script specifically addresses the critical issue where CANCELLED orders
may have incorrect filled_shares values in the database, causing position
calculation errors.

Usage:
    python scripts/verify_order_fills.py --order-id <order_id>
    python scripts/verify_order_fills.py --ticker <ticker> --instance <instance_name>
    python scripts/verify_order_fills.py --fix-all --instance <instance_name>
"""

import argparse
import os
import sys
from datetime import datetime, timezone
UTC = timezone.utc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "core"))

from dotenv import load_dotenv
load_dotenv()


def verify_single_order(order_id: str, fix: bool = False):
    """Verify a single order against Kalshi."""
    from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
    from ai_prophet_core.betting.db import create_db_engine, get_session
    from ai_prophet_core.betting.db_schema import BettingOrder

    print(f"\n=== Verifying Order {order_id[:8]}... ===")

    # Initialize Kalshi adapter
    adapter = KalshiAdapter.from_env()

    # Get order from database
    db_engine = create_db_engine()
    with get_session(db_engine) as session:
        order = session.query(BettingOrder).filter_by(order_id=order_id).first()
        if not order:
            print(f"ERROR: Order {order_id} not found in database")
            return False

        print(f"Database state:")
        print(f"  Ticker: {order.ticker}")
        print(f"  Instance: {order.instance_name}")
        print(f"  Status: {order.status}")
        print(f"  Action: {order.action} {order.side}")
        print(f"  Count: {order.count}")
        print(f"  Filled: {order.filled_shares}")
        print(f"  Fill Price: {order.fill_price}")

        # Get order from Kalshi
        try:
            kalshi_order = adapter.get_order(order_id)
            if kalshi_order:
                print(f"\nKalshi state:")
                print(f"  Status: {kalshi_order.status.value}")
                print(f"  Filled: {kalshi_order.filled_shares}")
                if hasattr(kalshi_order, 'avg_fill_price'):
                    print(f"  Avg Fill Price: {kalshi_order.avg_fill_price}")

                # Check for discrepancies
                db_filled = order.filled_shares or 0
                kalshi_filled = float(kalshi_order.filled_shares) if kalshi_order.filled_shares else 0

                if abs(db_filled - kalshi_filled) > 0.01:
                    print(f"\n⚠️  DISCREPANCY FOUND!")
                    print(f"  DB shows {db_filled} filled but Kalshi shows {kalshi_filled}")

                    if fix:
                        print(f"  Fixing...")
                        order.filled_shares = kalshi_filled
                        order.status = kalshi_order.status.value
                        if kalshi_filled > 0 and hasattr(kalshi_order, 'avg_fill_price'):
                            order.fill_price = float(kalshi_order.avg_fill_price)
                        session.commit()
                        print(f"  ✅ Fixed! Updated to {kalshi_filled} filled shares")
                        return True
                    else:
                        print(f"  Run with --fix to update the database")
                        return False
                else:
                    print(f"\n✅ No discrepancy - DB and Kalshi both show {db_filled} filled")
                    return True
            else:
                print(f"\n⚠️  Could not fetch order from Kalshi")
                return False

        except Exception as e:
            print(f"\nERROR fetching from Kalshi: {e}")
            return False


def verify_instance_orders(instance_name: str, ticker: str = None, fix_all: bool = False):
    """Verify all problematic orders for an instance."""
    from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter
    from ai_prophet_core.betting.db import create_db_engine, get_session
    from ai_prophet_core.betting.db_schema import BettingOrder
    from sqlalchemy import and_, or_

    print(f"\n=== Verifying Orders for {instance_name} ===")
    if ticker:
        print(f"Filtering to ticker: {ticker}")

    # Get orders to check
    db_engine = create_db_engine()
    with get_session(db_engine) as session:
        query = session.query(BettingOrder).filter(
            BettingOrder.instance_name == instance_name
        )

        if ticker:
            query = query.filter(BettingOrder.ticker == ticker)

        # Focus on problematic orders:
        # 1. CANCELLED with non-zero filled_shares
        # 2. Orders with filled_shares but no fill_price
        problematic_orders = query.filter(
            or_(
                and_(
                    BettingOrder.status == "CANCELLED",
                    BettingOrder.filled_shares > 0
                ),
                and_(
                    BettingOrder.filled_shares > 0,
                    or_(
                        BettingOrder.fill_price == None,
                        BettingOrder.fill_price == 0
                    )
                )
            )
        ).all()

        print(f"Found {len(problematic_orders)} potentially problematic orders")

        if not problematic_orders:
            print("No problematic orders found!")
            return

        # Initialize Kalshi adapter
        adapter = KalshiAdapter.from_env()

        fixed_count = 0
        error_count = 0

        for order in problematic_orders:
            print(f"\n--- Order {order.order_id[:8]} ({order.ticker}) ---")
            print(f"  DB: {order.status}, {order.filled_shares} filled @ {order.fill_price}")

            try:
                kalshi_order = adapter.get_order(order.order_id)
                if kalshi_order:
                    kalshi_filled = float(kalshi_order.filled_shares) if kalshi_order.filled_shares else 0

                    if abs((order.filled_shares or 0) - kalshi_filled) > 0.01:
                        print(f"  ⚠️  Kalshi: {kalshi_order.status.value}, {kalshi_filled} filled")

                        if fix_all:
                            order.filled_shares = kalshi_filled
                            order.status = kalshi_order.status.value
                            if kalshi_filled > 0 and hasattr(kalshi_order, 'avg_fill_price'):
                                order.fill_price = float(kalshi_order.avg_fill_price)
                            print(f"  ✅ Fixed!")
                            fixed_count += 1
                    else:
                        print(f"  ✅ Match: {kalshi_filled} filled")
                else:
                    print(f"  ❌ Could not fetch from Kalshi")
                    error_count += 1

            except Exception as e:
                print(f"  ❌ Error: {e}")
                error_count += 1

        if fix_all and fixed_count > 0:
            session.commit()
            print(f"\n✅ Fixed {fixed_count} orders")
        elif fixed_count > 0:
            print(f"\n⚠️  Found {fixed_count} discrepancies. Run with --fix-all to update")

        if error_count > 0:
            print(f"❌ {error_count} orders could not be verified")


def main():
    parser = argparse.ArgumentParser(description="Verify order fills against Kalshi")
    parser.add_argument("--order-id", help="Specific order ID to verify")
    parser.add_argument("--ticker", help="Filter to specific ticker")
    parser.add_argument("--instance", help="Instance name (Haifeng/Jibang)")
    parser.add_argument("--fix", action="store_true", help="Fix discrepancies for single order")
    parser.add_argument("--fix-all", action="store_true", help="Fix all discrepancies found")

    args = parser.parse_args()

    if args.order_id:
        verify_single_order(args.order_id, fix=args.fix)
    elif args.instance:
        verify_instance_orders(args.instance, ticker=args.ticker, fix_all=args.fix_all)
    else:
        print("Please specify --order-id or --instance")
        sys.exit(1)


if __name__ == "__main__":
    main()