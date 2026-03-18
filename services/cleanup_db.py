"""One-shot DB cleanup script.

Deletes all rows from betting and dashboard tables to start fresh.
Run once, then delete this file.

Usage:
    cd /Users/anrigu/Projects/ai-prophet
    source .venv/bin/activate
    python services/cleanup_db.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text
from ai_prophet_core.betting.db import create_db_engine, get_session

# Tables in deletion order (respecting FK constraints)
TABLES = [
    "betting_orders",
    "betting_signals",
    "betting_predictions",
    "trading_positions",
    "market_price_snapshots",
    "trading_markets",
    "model_runs",
    "system_logs",
]


def main():
    engine = create_db_engine()

    print("=== DB Cleanup ===\n")

    # Show current row counts
    with get_session(engine) as session:
        for table in TABLES:
            try:
                count = session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
                print(f"  {table}: {count} rows")
            except Exception as e:
                print(f"  {table}: (not found: {e})")

    print()
    confirm = input("Delete all rows from the above tables? [y/N] ")
    if confirm.strip().lower() != "y":
        print("Aborted.")
        return

    # Delete in FK order
    with get_session(engine) as session:
        for table in TABLES:
            try:
                result = session.execute(text(f"DELETE FROM {table}"))
                print(f"  Deleted from {table}: {result.rowcount} rows")
            except Exception as e:
                print(f"  Error on {table}: {e}")

    print("\n✅ Cleanup complete.")


if __name__ == "__main__":
    main()
