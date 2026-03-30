#!/usr/bin/env python3
"""
Script to mark the strategy transition point in the database.
Run this to create a clear demarcation between the old and new strategy.
"""

import os
import sys
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from services.strategy_marker import (
    create_strategy_marker_table,
    mark_all_workers_strategy_transition,
    get_strategy_pnl,
    CURRENT_STRATEGY_VERSION,
    STRATEGY_CONFIG
)

def main():
    # Get database URL from environment
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        print("ERROR: DATABASE_URL environment variable not set")
        return 1

    # Create database connection
    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)

    with Session() as session:
        print(f"\n{'='*70}")
        print(f"📍 STRATEGY TRANSITION MARKER")
        print(f"{'='*70}")
        print(f"Date: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print(f"Version: {CURRENT_STRATEGY_VERSION}")
        print(f"\n🎯 CURRENT STRATEGY RULES:")
        print(f"{'─'*70}")
        print(f"  1. 36-HOUR PRE-RESOLUTION BLOCK")
        print(f"     └─ No trades within 36 hours of market resolution")
        print(f"  2. 4-HOUR MINIMUM BETWEEN TRADES")
        print(f"     └─ Must wait 4+ hours before re-trading same market")
        print(f"  3. 10-CENT PRICE DEVIATION REQUIREMENT")
        print(f"     └─ Only re-trade if price moved ≥$0.10 from last trade")
        print(f"  4. NO SPREAD FILTER (REMOVED)")
        print(f"     └─ Trade ALL markets regardless of bid-ask spread")
        print(f"  5. CAPITAL: $500 STARTING CASH")
        print(f"     └─ All comparison workers start with $500")
        print(f"{'='*70}\n")

        # Create table if it doesn't exist
        print("Creating strategy markers table...")
        create_strategy_marker_table(session)

        # Mark the transition
        print("\nMarking strategy transition for all workers...")
        mark_all_workers_strategy_transition(
            session,
            notes=f"NEW STRATEGY START: {CURRENT_STRATEGY_VERSION} - "
                  f"36hr block, 4hr min gap, 10¢ deviation, NO spread filter"
        )

        # Display current PnL for each worker
        print(f"\n{'='*70}")
        print(f"📊 STRATEGY STARTING POINTS")
        print(f"{'='*70}")

        workers = ["GPT5", "Grok4", "Opus46", "Haifeng", "Jibang"]
        for worker in workers:
            try:
                pnl_data = get_strategy_pnl(session, worker)
                if "error" not in pnl_data:
                    print(f"\n{worker:8} │ Balance: ${pnl_data['current_balance']:,.2f}")
                    print(f"{'':8} │ From: ${pnl_data['starting_balance']:,.2f}")
                    print(f"{'':8} │ PnL: ${pnl_data['total_pnl']:+,.2f} ({pnl_data['return_percentage']:+.1f}%)")
            except Exception as e:
                print(f"\n{worker:8} │ Error: {e}")

        print(f"\n{'='*70}")
        print(f"✅ Strategy transition marked successfully!")
        print(f"   All future trades will be tracked under: {CURRENT_STRATEGY_VERSION}")
        print(f"   Dashboard will show clear separation from this point forward")
        print(f"{'='*70}\n")

    return 0


if __name__ == "__main__":
    exit(main())