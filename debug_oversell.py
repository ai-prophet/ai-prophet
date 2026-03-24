#!/usr/bin/env python3
"""Debug the oversell issue by replaying orders for problematic tickers."""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packages", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

from dotenv import load_dotenv
load_dotenv()

from ai_prophet_core.betting.db import create_db_engine, get_session
from ai_prophet_core.betting.db_schema import BettingOrder
from position_replay import InventoryPosition, normalize_order

def debug_ticker_position(ticker: str, instance_name: str = "Haifeng"):
    """Debug position calculation for a specific ticker."""

    engine = create_db_engine()

    with get_session(engine) as session:
        # Get all orders for this ticker
        orders = (
            session.query(BettingOrder)
            .filter(BettingOrder.instance_name == instance_name)
            .filter(BettingOrder.ticker == ticker)
            .filter(BettingOrder.status.in_(["FILLED", "DRY_RUN"]))
            .order_by(BettingOrder.created_at.asc(), BettingOrder.id.asc())
            .all()
        )

        print(f"\n{'='*80}")
        print(f"DEBUGGING: {ticker}")
        print(f"{'='*80}")
        print(f"Found {len(orders)} orders\n")

        # Replay position
        pos = InventoryPosition()

        for i, order in enumerate(orders, 1):
            action, side, shares, price = normalize_order(order)

            print(f"{i}. {order.created_at.strftime('%Y-%m-%d %H:%M')}")
            print(f"   {action} {shares:.1f} {side} @ ${price:.2f}")

            # Show position before
            before_side, before_qty, before_avg = pos.current_position()
            print(f"   Before: {before_qty:.1f} {before_side or 'none'}")

            # Apply order
            pos.apply_order(order, ticker=ticker)

            # Show position after
            after_side, after_qty, after_avg = pos.current_position()
            print(f"   After: {after_qty:.1f} {after_side or 'none'}")

            # Show warnings if any
            if pos.warnings:
                for warning in pos.warnings:
                    if ticker in warning:
                        print(f"   ⚠️  {warning}")
            print()

        # Final position
        final_side, final_qty, final_avg = pos.current_position()
        print(f"FINAL POSITION: {final_qty:.1f} {final_side or 'none'}")

        if pos.warnings:
            print(f"\nTOTAL WARNINGS: {len(pos.warnings)}")
            for warning in pos.warnings:
                if ticker in warning:
                    print(f"  - {warning}")

# Debug problematic tickers
problematic_tickers = [
    'KXDHSFUND-26APR01',
    'KXALBUMRELEASEDATEUZI-APR01-26',
]

for ticker in problematic_tickers:
    debug_ticker_position(ticker)

print("\n" + "="*80)
print("ROOT CAUSE ANALYSIS:")
print("="*80)
print("""
The oversell warnings occur when:
1. The system tries to SELL a position based on replayed order history
2. But the actual position from replaying is smaller than expected
3. This can happen if:
   - Some orders failed or were partially filled
   - The order history is incomplete
   - There's a bug in the position replay logic
   - Orders are being double-counted or skipped
""")