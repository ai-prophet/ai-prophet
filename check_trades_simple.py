#!/usr/bin/env python3
"""Simple check for recent trades using psycopg2 directly."""

import os
import psycopg2
from datetime import datetime, timedelta

def check_recent_trades():
    """Check recent trades from the database."""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL not found in environment")
        return

    try:
        conn = psycopg2.connect(database_url)
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
                live_count = sum(1 for t in instance_trades if not t[7])  # dry_run is index 7
                dry_count = sum(1 for t in instance_trades if t[7])

                print(f"\n{instance}:")
                print(f"  🔴 LIVE trades: {live_count}")
                print(f"  🔵 DRY RUN trades: {dry_count}")
                print("-" * 60)

                for trade in instance_trades[:5]:  # Show first 5 per instance
                    _, ticker, side, qty, price, status, created, dry_run = trade
                    dry_run_flag = "🔵 DRY" if dry_run else "🔴 LIVE"
                    time_str = created.strftime('%H:%M:%S')
                    print(f"  {time_str} {dry_run_flag} {side:4} {qty:3} {ticker:30} @ ${price/100:.2f} [{status}]")

                if len(instance_trades) > 5:
                    print(f"  ... and {len(instance_trades) - 5} more trades")

        # Check for most recent LIVE trade
        cursor.execute("""
            SELECT
                instance_name,
                created_at,
                market_ticker
            FROM trades
            WHERE dry_run = false
            ORDER BY created_at DESC
            LIMIT 1
        """)

        latest_live = cursor.fetchone()
        if latest_live:
            instance, created, ticker = latest_live
            time_ago = datetime.utcnow() - created
            minutes_ago = time_ago.total_seconds() / 60
            print(f"\n🔴 Most Recent LIVE Trade: {instance} - {ticker}")
            print(f"   {minutes_ago:.0f} minutes ago ({created.strftime('%Y-%m-%d %H:%M:%S')} UTC)")

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_recent_trades()