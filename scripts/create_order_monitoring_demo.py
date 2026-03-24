"""Create demo orders to visualize the order monitoring panel."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from typing import Optional

# Add packages to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../packages/core"))

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import BettingOrder, SystemLog
from sqlalchemy import create_engine

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

engine = create_engine(DATABASE_URL)
INSTANCE_NAME = "Demo"

def create_demo_orders():
    """Create various order states for demonstration."""

    now = datetime.now(timezone.utc)

    demo_orders = [
        # 1. Fresh pending order (15 minutes old)
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXDHSFUND-26APR01",
            "side": "yes",
            "action": "BUY",
            "count": 25,
            "price_cents": 45,
            "status": "PENDING",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=15),
        },

        # 2. Partially filled order (20 minutes old) - 10/30 filled
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXMARKET-26MAR25",
            "side": "no",
            "action": "BUY",
            "count": 30,
            "price_cents": 52,
            "status": "PENDING",
            "filled_shares": 10.0,  # Partially filled!
            "fill_price": 0.52,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=20),
        },

        # 3. STALE pending order (75 minutes old - will be cancelled!)
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXDEREMEROUT-26-APR01",
            "side": "yes",
            "action": "BUY",
            "count": 50,
            "price_cents": 38,
            "status": "PENDING",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=75),  # STALE!
        },

        # 4. Another STALE order (90 minutes old)
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXTRUMP-26APR",
            "side": "no",
            "action": "BUY",
            "count": 40,
            "price_cents": 61,
            "status": "PENDING",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=90),  # STALE!
        },

        # 5. Recently filled order
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXBTC-26MAR30",
            "side": "yes",
            "action": "BUY",
            "count": 20,
            "price_cents": 55,
            "status": "FILLED",
            "filled_shares": 20.0,
            "fill_price": 0.55,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=10),
        },

        # 6. Recently cancelled order (for "recent cancellations" section)
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXOLD-26MAR20",
            "side": "yes",
            "action": "BUY",
            "count": 35,
            "price_cents": 42,
            "status": "CANCELLED",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=65),
        },

        # 7. Old cancelled order
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXSTALE-26MAR18",
            "side": "no",
            "action": "BUY",
            "count": 15,
            "price_cents": 48,
            "status": "CANCELLED",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(minutes=95),
        },

        # 8. Error order
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXFAILED-26MAR22",
            "side": "yes",
            "action": "BUY",
            "count": 10,
            "price_cents": 50,
            "status": "ERROR",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": None,
            "dry_run": False,
            "created_at": now - timedelta(minutes=5),
        },

        # 9. More filled orders for status breakdown
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXETH-26APR05",
            "side": "no",
            "action": "BUY",
            "count": 45,
            "price_cents": 59,
            "status": "FILLED",
            "filled_shares": 45.0,
            "fill_price": 0.59,
            "exchange_order_id": f"kalshi_{uuid4().hex[:16]}",
            "dry_run": False,
            "created_at": now - timedelta(hours=2),
        },

        # 10. DRY_RUN order
        {
            "order_id": str(uuid4()),
            "instance_name": INSTANCE_NAME,
            "ticker": "KXTEST-26MAR25",
            "side": "yes",
            "action": "BUY",
            "count": 100,
            "price_cents": 50,
            "status": "DRY_RUN",
            "filled_shares": 100.0,
            "fill_price": 0.50,
            "exchange_order_id": None,
            "dry_run": True,
            "created_at": now - timedelta(hours=1),
        },
    ]

    # Create system alerts/errors for demo
    demo_logs = [
        # Position drift alert
        {
            "level": "ALERT",
            "message": "Position drift detected: KXMARKET-26MAR25 (DB: 30, Kalshi: 25)",
            "component": "worker",
            "instance_name": INSTANCE_NAME,
            "created_at": now - timedelta(minutes=25),
        },

        # Stale order alert
        {
            "level": "ALERT",
            "message": f"Cancelled 2 stale orders (>60 min pending)",
            "component": "order_management",
            "instance_name": INSTANCE_NAME,
            "created_at": now - timedelta(minutes=5),
        },

        # Order error
        {
            "level": "ERROR",
            "message": "Order submission failed: Insufficient balance for KXFAILED-26MAR22",
            "component": "engine",
            "instance_name": INSTANCE_NAME,
            "created_at": now - timedelta(minutes=5),
        },

        # Partial fill info
        {
            "level": "INFO",
            "message": "Cancelling partially filled order: 10/30 filled, cancelling 20 unfilled for KXMARKET-26MAR25",
            "component": "order_management",
            "instance_name": INSTANCE_NAME,
            "created_at": now - timedelta(minutes=20),
        },
    ]

    with get_session(engine) as session:
        print(f"Creating {len(demo_orders)} demo orders...")
        for order_data in demo_orders:
            order = BettingOrder(**order_data)
            session.add(order)

        print(f"Creating {len(demo_logs)} demo system logs...")
        for log_data in demo_logs:
            log = SystemLog(**log_data)
            session.add(log)

        session.commit()
        print("✅ Demo data created successfully!")

    # Print summary
    print("\n" + "="*60)
    print("DEMO ORDER SUMMARY")
    print("="*60)
    print(f"Fresh pending orders:     2")
    print(f"Stale orders (>60min):    2  🔴 WILL REORDER")
    print(f"Partially filled:         1  (10/30 filled)")
    print(f"Filled orders:            2")
    print(f"Cancelled orders:         2")
    print(f"Error orders:             1")
    print(f"Dry run orders:           1")
    print(f"\nSystem alerts:            2  🔴")
    print(f"System errors:            1  🟡")
    print("="*60)
    print("\n✨ Navigate to the dashboard and click 'Order Monitoring' tab")
    print(f"   Instance: {INSTANCE_NAME}")
    print("\nYou should see:")
    print("  • Alert banner warning about 2 stale orders")
    print("  • Stats showing 4 pending, 2 stale, 2 alerts, 1 error")
    print("  • Order status breakdown: 2 FILLED, 4 PENDING, 2 CANCELLED, 1 ERROR, 1 DRY_RUN")
    print("  • Pending orders table with STALE flags and WILL REORDER chips")
    print("  • Recent cancellations list")
    print("  • System alerts showing position drift and errors")
    print()


if __name__ == "__main__":
    create_demo_orders()
