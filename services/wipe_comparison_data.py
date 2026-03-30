#!/usr/bin/env python3
"""
Wipe all comparison worker data from the database.

This script removes all trading data for the comparison workers (GPT5, Grok4, Opus46)
including orders, positions, predictions, and balance snapshots.

Usage:
    python services/wipe_comparison_data.py

    # Or wipe specific instance:
    python services/wipe_comparison_data.py --instance GPT5

    # Dry run (show what would be deleted):
    python services/wipe_comparison_data.py --dry-run
"""

import os
import sys
import argparse
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


COMPARISON_INSTANCES = ["GPT5", "Grok4", "Opus46"]


def wipe_comparison_data(
    db_url: str,
    instance: str = None,
    dry_run: bool = False
) -> dict:
    """
    Wipe all data for comparison workers.

    Args:
        db_url: Database connection string
        instance: Specific instance to wipe, or None for all comparison workers
        dry_run: If True, only show what would be deleted

    Returns:
        Dictionary with deletion counts
    """
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    instances_to_wipe = [instance] if instance else COMPARISON_INSTANCES

    print(f"\n{'='*60}")
    print(f"🗑️  COMPARISON WORKER DATA WIPE")
    print(f"{'='*60}")
    print(f"Instances: {', '.join(instances_to_wipe)}")
    print(f"Mode: {'DRY RUN' if dry_run else 'LIVE DELETION'}")
    print(f"Time: {datetime.now(UTC).isoformat()}")
    print(f"{'='*60}\n")

    deletion_counts = {}

    with Session() as session:
        for inst in instances_to_wipe:
            print(f"\n📊 Processing {inst}...")
            counts = {}

            # Tables to clean (in order to respect foreign keys)
            tables = [
                ("betting_orders", "Orders"),
                ("betting_deferred_flips", "Deferred flips"),
                ("betting_signals", "Signals"),
                ("betting_predictions", "Predictions"),
                ("kalshi_order_snapshots", "Order snapshots"),
                ("kalshi_position_snapshots", "Position snapshots"),
                ("kalshi_balance_snapshots", "Balance snapshots"),
                ("trading_positions", "Trading positions"),
                ("trading_markets", "Trading markets"),
                ("model_runs", "Model runs"),
                ("system_logs", "System logs"),
                ("strategy_markers", "Strategy markers"),
            ]

            for table, display_name in tables:
                # Count records
                try:
                    count_sql = text(f"""
                        SELECT COUNT(*)
                        FROM {table}
                        WHERE instance_name = :instance
                    """)

                    count = session.execute(count_sql, {"instance": inst}).scalar()
                    counts[table] = count
                except Exception as e:
                    # Table might not exist (e.g., strategy_markers)
                    if "does not exist" in str(e):
                        counts[table] = 0
                        session.rollback()  # Reset transaction after error
                        continue
                    else:
                        raise

                if count > 0:
                    print(f"  - {display_name}: {count:,} records")

                    if not dry_run:
                        # Delete records
                        delete_sql = text(f"""
                            DELETE FROM {table}
                            WHERE instance_name = :instance
                        """)
                        session.execute(delete_sql, {"instance": inst})

            deletion_counts[inst] = counts

            # Get current balance before wipe
            balance_sql = text("""
                SELECT balance
                FROM kalshi_balance_snapshots
                WHERE instance_name = :instance
                ORDER BY snapshot_ts DESC
                LIMIT 1
            """)
            current_balance = session.execute(
                balance_sql, {"instance": inst}
            ).scalar()

            if current_balance is not None:
                print(f"  💰 Last balance: ${current_balance:,.2f}")

        if not dry_run:
            session.commit()
            print(f"\n✅ Data wiped successfully!")
        else:
            print(f"\n⚠️  DRY RUN - No data was actually deleted")

    # Summary
    print(f"\n{'='*60}")
    print(f"📋 SUMMARY")
    print(f"{'='*60}")

    total_deleted = 0
    for inst, counts in deletion_counts.items():
        inst_total = sum(counts.values())
        total_deleted += inst_total
        if inst_total > 0:
            print(f"{inst}: {inst_total:,} records {'would be deleted' if dry_run else 'deleted'}")

    print(f"{'='*60}")
    print(f"Total: {total_deleted:,} records {'would be deleted' if dry_run else 'deleted'}")
    print(f"{'='*60}\n")

    return deletion_counts


def main():
    parser = argparse.ArgumentParser(
        description="Wipe comparison worker data from database"
    )
    parser.add_argument(
        "--instance",
        choices=COMPARISON_INSTANCES + [None],
        default=None,
        help="Specific instance to wipe (default: all comparison workers)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Skip confirmation prompt"
    )

    args = parser.parse_args()

    # Get database URL
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable not set")
        print("Please set it or run: source .env")
        return 1

    # Confirmation prompt
    if not args.dry_run and not args.confirm:
        instances = [args.instance] if args.instance else COMPARISON_INSTANCES
        print(f"\n⚠️  WARNING: This will DELETE all data for: {', '.join(instances)}")
        print("This action cannot be undone!")
        response = input("\nType 'yes' to continue: ")
        if response.lower() != 'yes':
            print("Cancelled.")
            return 0

    try:
        wipe_comparison_data(
            db_url,
            instance=args.instance,
            dry_run=args.dry_run
        )
        return 0
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return 1


if __name__ == "__main__":
    exit(main())