#!/usr/bin/env python3
"""Check recent trades from the database for all instances."""

import os
import sys
from datetime import datetime, timedelta

# Add the packages/core directory to the Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'packages/core'))

from ai_prophet_core.db import create_db_connection

def check_recent_trades():
    """Check recent trades from the database."""
    conn = create_db_connection()
    if not conn:
        print("Failed to connect to database")
        return

    try:
        cursor = conn.cursor()

        # Get trades from last 2 hours
        cutoff_time = datetime.utcnow() - timedelta(hours=2)

        print("\n" + "="*80)
        print("  Recent Trading Activity (Last 2 Hours)")
        print("="*80)

        # Query for recent trades
        cursor.execute("""
            SELECT
                instance_name,
                market_ticker,
                side,
                quantity,
                price,
                status,
                created_at,
                exchange_order_id,
                dry_run
            FROM trades
            WHERE created_at > %s
            ORDER BY created_at DESC
            LIMIT 50
        """, (cutoff_time,))

        trades = cursor.fetchall()

        if not trades:
            print("\n✅ NO TRADES in the last 2 hours")
        else:
            print(f"\n⚠️  Found {len(trades)} trades in the last 2 hours:\n")

            # Group by instance
            by_instance = {}
            for trade in trades:
                instance = trade[0]
                if instance not in by_instance:
                    by_instance[instance] = []
                by_instance[instance].append(trade)

            for instance, instance_trades in by_instance.items():
                print(f"\n{instance} ({len(instance_trades)} trades):")
                print("-" * 60)

                for trade in instance_trades[:5]:  # Show first 5 per instance
                    _, ticker, side, qty, price, status, created, order_id, dry_run = trade
                    dry_run_flag = "🔵 DRY" if dry_run else "🔴 LIVE"
                    print(f"  {created.strftime('%H:%M:%S')} {dry_run_flag} {side:4} {qty:3} {ticker:30} @ ${price/100:.2f} [{status}]")

                if len(instance_trades) > 5:
                    print(f"  ... and {len(instance_trades) - 5} more trades")

        # Check for most recent trade overall
        cursor.execute("""
            SELECT
                instance_name,
                created_at,
                dry_run
            FROM trades
            ORDER BY created_at DESC
            LIMIT 1
        """)

        latest = cursor.fetchone()
        if latest:
            instance, created, dry_run = latest
            time_ago = datetime.utcnow() - created
            hours_ago = time_ago.total_seconds() / 3600
            dry_run_status = "DRY RUN" if dry_run else "LIVE"
            print(f"\n📍 Most Recent Trade: {instance} - {hours_ago:.1f} hours ago ({dry_run_status})")

        # Check positions for Haifeng and Jibang
        print("\n" + "="*80)
        print("  Current Database Positions")
        print("="*80)

        for instance in ["Haifeng", "Jibang"]:
            cursor.execute("""
                SELECT
                    market_ticker,
                    position,
                    avg_price,
                    updated_at
                FROM positions
                WHERE instance_name = %s AND position != 0
                ORDER BY abs(position) DESC
            """, (instance,))

            positions = cursor.fetchall()

            if positions:
                print(f"\n{instance}: {len(positions)} active positions")
                total_contracts = sum(abs(p[1]) for p in positions)
                print(f"  Total contracts: {total_contracts}")
                for pos in positions[:3]:
                    ticker, position, avg_price, updated = pos
                    print(f"    • {ticker}: {position:+d} @ ${avg_price/100:.2f}")
                if len(positions) > 3:
                    print(f"    ... and {len(positions) - 3} more positions")
            else:
                print(f"\n{instance}: No active positions")

    except Exception as e:
        print(f"Error querying database: {e}")
    finally:
        cursor.close()
        conn.close()

if __name__ == "__main__":
    check_recent_trades()