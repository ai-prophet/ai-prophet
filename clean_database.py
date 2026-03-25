#!/usr/bin/env python3
"""
Complete database cleanup script for AI Prophet.
WARNING: This will DELETE ALL DATA from the database!
"""

import os
import sys
import psycopg2
from urllib.parse import urlparse
from datetime import datetime


def clean_database():
    """Completely clean all data from the database."""

    # Get database URL from environment
    database_url = os.environ.get('DATABASE_URL')
    if not database_url:
        print("ERROR: DATABASE_URL environment variable not set")
        sys.exit(1)

    # Parse database URL
    parsed = urlparse(database_url)

    # Confirm with user
    print("=" * 60)
    print("WARNING: COMPLETE DATABASE CLEANUP")
    print("=" * 60)
    print(f"Database: {parsed.hostname}")
    print(f"Database name: {parsed.path[1:]}")
    print()
    print("This will DELETE ALL DATA from ALL tables including:")
    print("  - All predictions")
    print("  - All signals")
    print("  - All orders")
    print("  - All fills")
    print("  - All positions")
    print("  - All markets")
    print("  - All model runs")
    print("  - All snapshots")
    print("  - All alerts")
    print()

    response = input("Type 'DELETE EVERYTHING' to proceed: ")
    if response != "DELETE EVERYTHING":
        print("Cleanup cancelled.")
        return

    try:
        # Connect to database
        conn = psycopg2.connect(database_url)
        cur = conn.cursor()

        print(f"\n[{datetime.now().isoformat()}] Starting complete database cleanup...")

        # Tables to clean in dependency order
        tables = [
            'betting_fills',
            'betting_orders',
            'betting_signals',
            'betting_predictions',
            'model_runs',
            'market_snapshots',
            'price_snapshots',
            'system_alerts',
            'alert_history',
            'positions',
            'markets',
            'comparison_results',
            'model_calibration',
        ]

        # Delete from each table
        for table in tables:
            try:
                cur.execute(f"DELETE FROM {table}")
                count = cur.rowcount
                print(f"  - Deleted {count:,} records from {table}")
            except psycopg2.ProgrammingError as e:
                if "does not exist" in str(e):
                    print(f"  - Table {table} does not exist (skipping)")
                    conn.rollback()
                else:
                    raise

        # Reset sequences
        sequences = [
            'betting_predictions_id_seq',
            'betting_signals_id_seq',
            'betting_orders_id_seq',
            'betting_fills_id_seq',
            'model_runs_id_seq',
            'market_snapshots_id_seq',
            'price_snapshots_id_seq',
            'positions_id_seq',
            'markets_id_seq',
            'system_alerts_id_seq',
            'alert_history_id_seq',
            'comparison_results_id_seq',
        ]

        print("\nResetting sequences...")
        for seq in sequences:
            try:
                cur.execute(f"ALTER SEQUENCE {seq} RESTART WITH 1")
                print(f"  - Reset {seq}")
            except psycopg2.ProgrammingError as e:
                if "does not exist" in str(e):
                    print(f"  - Sequence {seq} does not exist (skipping)")
                    conn.rollback()
                else:
                    raise

        # Commit changes
        conn.commit()

        # Vacuum to reclaim space
        print("\nVacuuming database to reclaim space...")
        conn.set_isolation_level(0)  # VACUUM requires autocommit mode
        cur.execute("VACUUM ANALYZE")

        # Verify cleanup
        print("\nVerifying cleanup...")
        conn.set_isolation_level(1)  # Back to normal mode

        total_records = 0
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                if count > 0:
                    print(f"  WARNING: {table} still has {count} records!")
                    total_records += count
            except psycopg2.ProgrammingError:
                pass  # Table doesn't exist

        if total_records == 0:
            print("\n✓ SUCCESS: All tables are completely empty!")
        else:
            print(f"\n⚠ WARNING: {total_records} records still remain in database")

        # Close connection
        cur.close()
        conn.close()

        print(f"\n[{datetime.now().isoformat()}] Database cleanup complete.")

    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)


if __name__ == "__main__":
    clean_database()