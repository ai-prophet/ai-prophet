"""Create demo orders directly via SQLAlchemy to visualize the order monitoring panel."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, text

# Database connection
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set")
    exit(1)

engine = create_engine(DATABASE_URL)
INSTANCE_NAME = "Demo"

def create_demo_orders():
    """Create various order states for demonstration."""

    now = datetime.now(timezone.utc)

    # Clear old demo data
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM betting_orders WHERE instance_name = :inst"), {"inst": INSTANCE_NAME})
        conn.execute(text("DELETE FROM system_logs WHERE instance_name = :inst"), {"inst": INSTANCE_NAME})
        conn.commit()
        print(f"Cleared old demo data for {INSTANCE_NAME}")

    demo_orders = [
        # 1. Fresh pending order (15 minutes old)
        (str(uuid4()), INSTANCE_NAME, "KXDHSFUND-26APR01", "yes", "BUY", 25, 45, "PENDING", 0.0, 0.0,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=15)),

        # 2. Partially filled order (20 minutes old) - 10/30 filled
        (str(uuid4()), INSTANCE_NAME, "KXMARKET-26MAR25", "no", "BUY", 30, 52, "PENDING", 10.0, 0.52,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=20)),

        # 3. STALE pending order (75 minutes old - will be cancelled!)
        (str(uuid4()), INSTANCE_NAME, "KXDEREMEROUT-26-APR01", "yes", "BUY", 50, 38, "PENDING", 0.0, 0.0,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=75)),

        # 4. Another STALE order (90 minutes old)
        (str(uuid4()), INSTANCE_NAME, "KXTRUMP-26APR", "no", "BUY", 40, 61, "PENDING", 0.0, 0.0,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=90)),

        # 5. Recently filled order
        (str(uuid4()), INSTANCE_NAME, "KXBTC-26MAR30", "yes", "BUY", 20, 55, "FILLED", 20.0, 0.55,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=10)),

        # 6. Recently cancelled order
        (str(uuid4()), INSTANCE_NAME, "KXOLD-26MAR20", "yes", "BUY", 35, 42, "CANCELLED", 0.0, 0.0,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=65)),

        # 7. Old cancelled order
        (str(uuid4()), INSTANCE_NAME, "KXSTALE-26MAR18", "no", "BUY", 15, 48, "CANCELLED", 0.0, 0.0,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(minutes=95)),

        # 8. Error order
        (str(uuid4()), INSTANCE_NAME, "KXFAILED-26MAR22", "yes", "BUY", 10, 50, "ERROR", 0.0, 0.0,
         None, False, now - timedelta(minutes=5)),

        # 9. More filled orders
        (str(uuid4()), INSTANCE_NAME, "KXETH-26APR05", "no", "BUY", 45, 59, "FILLED", 45.0, 0.59,
         f"kalshi_{uuid4().hex[:16]}", False, now - timedelta(hours=2)),

        # 10. DRY_RUN order
        (str(uuid4()), INSTANCE_NAME, "KXTEST-26MAR25", "yes", "BUY", 100, 50, "DRY_RUN", 100.0, 0.50,
         None, True, now - timedelta(hours=1)),
    ]

    demo_logs = [
        ("ALERT", "Position drift detected: KXMARKET-26MAR25 (DB: 30, Kalshi: 25)", "worker",
         INSTANCE_NAME, now - timedelta(minutes=25)),

        ("ALERT", "Cancelled 2 stale orders (>60 min pending)", "order_management",
         INSTANCE_NAME, now - timedelta(minutes=5)),

        ("ERROR", "Order submission failed: Insufficient balance for KXFAILED-26MAR22", "engine",
         INSTANCE_NAME, now - timedelta(minutes=5)),

        ("INFO", "Cancelling partially filled order: 10/30 filled, cancelling 20 unfilled for KXMARKET-26MAR25",
         "order_management", INSTANCE_NAME, now - timedelta(minutes=20)),
    ]

    with engine.connect() as conn:
        # Insert orders
        for order in demo_orders:
            conn.execute(text("""
                INSERT INTO betting_orders
                (order_id, instance_name, ticker, side, action, count, price_cents, status,
                 filled_shares, fill_price, exchange_order_id, dry_run, created_at)
                VALUES (:order_id, :instance, :ticker, :side, :action, :count, :price_cents,
                        :status, :filled_shares, :fill_price, :exchange_order_id, :dry_run, :created_at)
            """), {
                "order_id": order[0],
                "instance": order[1],
                "ticker": order[2],
                "side": order[3],
                "action": order[4],
                "count": order[5],
                "price_cents": order[6],
                "status": order[7],
                "filled_shares": order[8],
                "fill_price": order[9],
                "exchange_order_id": order[10],
                "dry_run": order[11],
                "created_at": order[12],
            })

        # Insert logs
        for log in demo_logs:
            conn.execute(text("""
                INSERT INTO system_logs (level, message, component, instance_name, created_at)
                VALUES (:level, :message, :component, :instance, :created_at)
            """), {
                "level": log[0],
                "message": log[1],
                "component": log[2],
                "instance": log[3],
                "created_at": log[4],
            })

        conn.commit()
        print(f"✅ Created {len(demo_orders)} demo orders and {len(demo_logs)} system logs")

    # Print summary
    print("\n" + "="*70)
    print("DEMO ORDER MONITORING - FRONTEND PREVIEW")
    print("="*70)
    print(f"\n📊 Order Status Breakdown:")
    print(f"   • Filled:            2 orders")
    print(f"   • Pending:           4 orders (2 fresh, 2 stale)")
    print(f"   • Cancelled:         2 orders")
    print(f"   • Error:             1 order")
    print(f"   • Dry Run:           1 order")

    print(f"\n⚠️  Critical Indicators:")
    print(f"   • Stale orders:      2  🔴 (>60 min, will be cancelled & reordered)")
    print(f"   • Partial fills:     1  (KXMARKET-26MAR25: 10/30 filled)")
    print(f"   • System alerts:     2  🔴")
    print(f"   • System errors:     1  🟡")

    print(f"\n🎯 What You'll See in Dashboard:")
    print(f"   1. 🔴 Red Alert Banner at top:")
    print(f"      '2 stale order(s) detected (>60min). Will be cancelled and reordered next cycle.'")
    print(f"")
    print(f"   2. Stats Grid:")
    print(f"      ┌─────────────┬─────────────┬─────────────┬─────────────┐")
    print(f"      │ Pending: 4  │ Stale: 2 🔴 │ Alerts: 2🔴 │ Errors: 1🟡 │")
    print(f"      └─────────────┴─────────────┴─────────────┴─────────────┘")
    print(f"")
    print(f"   3. Order Status:")
    print(f"      FILLED: 2  PENDING: 4  CANCELLED: 2  ERROR: 1  DRY_RUN: 1")
    print(f"")
    print(f"   4. Pending Orders Table (stale orders highlighted in red):")
    print(f"      • KXDEREMEROUT-26-APR01  [50 YES]  🟠 WILL REORDER  @ 38¢")
    print(f"        75 minutes ago  [STALE]")
    print(f"")
    print(f"      • KXTRUMP-26APR  [40 NO]  🟠 WILL REORDER  @ 61¢")
    print(f"        90 minutes ago  [STALE]")
    print(f"")
    print(f"      • KXMARKET-26MAR25  [30 NO]  @ 52¢  (10/30 filled)")
    print(f"        20 minutes ago")
    print(f"")
    print(f"      • KXDHSFUND-26APR01  [25 YES]  @ 45¢")
    print(f"        15 minutes ago")
    print(f"")
    print(f"   5. Recent Cancellations:")
    print(f"      • KXOLD-26MAR20: Cancelled stale order")
    print(f"      • KXSTALE-26MAR18: Cancelled stale order")
    print(f"")
    print(f"   6. System Alerts:")
    print(f"      🔴 ALERT | Position drift detected: KXMARKET-26MAR25")
    print(f"      🔴 ALERT | Cancelled 2 stale orders")
    print(f"      🔴 ERROR | Order submission failed: Insufficient balance")
    print("="*70)
    print(f"\n✨ To view in dashboard:")
    print(f"   1. Navigate to your dashboard")
    print(f"   2. Select instance: '{INSTANCE_NAME}'")
    print(f"   3. Click 'Order Monitoring' tab in the right panel")
    print(f"   4. Panel auto-refreshes every 30 seconds")
    print()


if __name__ == "__main__":
    create_demo_orders()
