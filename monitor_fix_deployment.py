#!/usr/bin/env python3
"""Monitor deployment of the signal_id fix for NET position management."""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.path.append("services")
sys.path.append("packages/core")

from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv("services/.env")


def check_deployment_status():
    """Check if the fix has been deployed by looking for NULL signal_ids."""
    engine = create_engine(os.getenv("DATABASE_URL"))

    with engine.connect() as conn:
        # Check for NET sells with NULL signal_id (indicates fix is deployed)
        result = conn.execute(text("""
            SELECT COUNT(*)
            FROM betting_orders
            WHERE action = 'SELL'
            AND signal_id IS NULL
            AND created_at > NOW() - INTERVAL '1 hour'
        """))
        null_count = result.fetchone()[0]

        # Check for signal mismatches (indicates bug still present)
        result = conn.execute(text("""
            SELECT COUNT(DISTINCT bo.ticker)
            FROM betting_orders bo
            LEFT JOIN betting_signals bs ON bs.id = bo.signal_id
            LEFT JOIN betting_predictions bp ON bp.id = bs.prediction_id
            WHERE bo.created_at > NOW() - INTERVAL '30 minutes'
            AND bo.action = 'SELL'
            AND bo.signal_id IS NOT NULL
            AND bo.ticker != REPLACE(bp.market_id, 'kalshi:', '')
        """))
        mismatch_count = result.fetchone()[0]

        # Get last order time
        result = conn.execute(text("""
            SELECT MAX(created_at)
            FROM betting_orders
            WHERE instance_name IN ('Haifeng', 'Jibang')
        """))
        last_order = result.fetchone()[0]

        return null_count, mismatch_count, last_order


def main():
    """Monitor the deployment status."""
    print("Monitoring deployment of signal_id fix...")
    print("=" * 60)
    print("The fix will:")
    print("1. Set signal_id=NULL for NET position sells")
    print("2. Prevent signal mismatches across different markets")
    print("=" * 60)

    while True:
        null_count, mismatch_count, last_order = check_deployment_status()

        now = datetime.now(timezone.utc)
        if last_order:
            mins_since = int((now - last_order).total_seconds() / 60)
            last_str = f"{mins_since} min ago"
        else:
            last_str = "Never"

        print(f"\n[{now.strftime('%H:%M:%S')}] Status Check:")
        print(f"  • NET sells with NULL signal_id: {null_count}")
        print(f"  • Markets with signal mismatches: {mismatch_count}")
        print(f"  • Last order: {last_str}")

        if null_count > 0:
            print("\n✅ FIX DEPLOYED! NET sells now have NULL signal_id")
            print(f"   {null_count} NET sells processed correctly since deployment")
            break
        elif mismatch_count > 0:
            print("\n⚠️  FIX NOT YET DEPLOYED - Signal mismatches still occurring")
        else:
            print("\n⏳ Waiting for new orders to verify deployment...")

        time.sleep(60)  # Check every minute


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nMonitoring stopped.")