#!/usr/bin/env python3
"""
Manually sync Kalshi positions to the database.

This script fetches your current Kalshi portfolio via the API
and populates the database with your existing positions.

Usage:
    python scripts/sync_kalshi_positions.py --instance Haifeng
    python scripts/sync_kalshi_positions.py --instance Jibang
"""

import argparse
import os
import sys
import requests

API_URL = os.getenv("API_URL", "https://ai-prophet-api-shared-441548451945.us-west1.run.app")


def sync_from_kalshi(instance_name: str):
    """
    Trigger the API to sync positions from Kalshi.

    Note: This requires the worker to be running. The worker automatically
    syncs positions on every cycle, so this script just waits for the next cycle.
    """
    print(f"Syncing positions for {instance_name}...")

    # Check current balance and positions
    try:
        balance_resp = requests.get(f"{API_URL}/kalshi/balance?instance_name={instance_name}")
        balance_resp.raise_for_status()
        balance_data = balance_resp.json()

        print(f"\nKalshi Account Status:")
        print(f"  Balance: ${balance_data['balance']:.2f}")
        print(f"  Mode: {'DRY RUN' if balance_data['dry_run'] else 'LIVE'}")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching balance: {e}")
        return False

    # The worker automatically syncs positions on every cycle
    # Check when the last cycle was
    try:
        health_resp = requests.get(f"{API_URL}/health?instance_name={instance_name}")
        health_resp.raise_for_status()
        health = health_resp.json()

        print(f"\nWorker Status:")
        print(f"  Last cycle: {health['last_cycle_end']}")
        print(f"  Worker status: {health['worker']}")
        print(f"  Mode: {health['mode']}")

        if health['worker'] == 'stale':
            print(f"\n⚠️  Worker is stale - may not be running")
            print(f"   Next cycle should sync positions automatically")
        else:
            print(f"\n✅ Worker is healthy")
            print(f"   Positions will sync on next cycle (every hour)")

    except requests.exceptions.RequestException as e:
        print(f"❌ Error checking worker status: {e}")
        return False

    print(f"\nℹ️  Note: The worker automatically syncs Kalshi positions on every cycle.")
    print(f"   Wait for the next cycle to complete, or manually trigger one by restarting the worker.")

    return True


def main():
    parser = argparse.ArgumentParser(description="Check Kalshi sync status")
    parser.add_argument("--instance", required=True, choices=["Haifeng", "Jibang"],
                        help="Instance name (Haifeng or Jibang)")

    args = parser.parse_args()

    try:
        sync_from_kalshi(args.instance)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
