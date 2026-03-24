#!/usr/bin/env python3
"""Check oversell issues in the database."""

import os
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse
from datetime import datetime, timedelta

db_url = os.getenv('DATABASE_URL')
result = urlparse(db_url)
conn = psycopg2.connect(
    database=result.path[1:],
    user=result.username,
    password=result.password,
    host=result.hostname,
    port=result.port
)

cur = conn.cursor()

# Get tickers with oversell warnings from recent logs
print("Checking for oversell issues...")
print("=" * 80)

# Find problematic markets
problem_tickers = [
    'KXALBUMRELEASEDATEUZI-APR01-26',
    'KXDHSFUND-26APR01',
    'KXMEDIARELEASEICEMAN-26-APR01'
]

for ticker in problem_tickers:
    print(f"\n📊 Market: {ticker}")
    print("-" * 40)

    # Check current position
    cur.execute("""
        SELECT quantity, avg_price, updated_at
        FROM trading_positions
        WHERE ticker = %s AND instance_name = 'Haifeng'
    """, (ticker,))

    pos_result = cur.fetchone()
    if pos_result:
        qty, price, updated = pos_result
        print(f"  Current Position: {qty:.1f} shares @ ${price:.2f}")
        print(f"  Last updated: {updated}")
    else:
        print("  No position found")

    # Check recent orders
    cur.execute("""
        SELECT action, side, count, status, created_at
        FROM betting_orders
        WHERE ticker = %s AND instance_name = 'Haifeng'
        AND created_at > NOW() - INTERVAL '24 hours'
        ORDER BY created_at DESC
        LIMIT 10
    """, (ticker,))

    orders = cur.fetchall()
    if orders:
        print(f"\n  Recent orders (last 24h):")
        net_position = 0
        for action, side, count, status, created in orders:
            time_str = created.strftime("%H:%M")
            print(f"    {time_str} - {action} {count} {side} ({status})")

            # Calculate net position
            if status in ['FILLED', 'DRY_RUN']:
                if action == 'BUY' and side == 'YES':
                    net_position += count
                elif action == 'SELL' and side == 'YES':
                    net_position -= count
                elif action == 'BUY' and side == 'NO':
                    net_position -= count
                elif action == 'SELL' and side == 'NO':
                    net_position += count

        print(f"\n  Net position from orders: {net_position}")
    else:
        print("  No recent orders")

# Check for general oversell patterns
print("\n" + "=" * 80)
print("OVERSELL ANALYSIS:")
print("=" * 80)

cur.execute("""
    SELECT
        bo.ticker,
        bo.action,
        bo.side,
        bo.count,
        tp.quantity as position_qty
    FROM betting_orders bo
    LEFT JOIN trading_positions tp ON tp.ticker = bo.ticker AND tp.instance_name = bo.instance_name
    WHERE bo.action = 'SELL'
    AND bo.instance_name = 'Haifeng'
    AND bo.created_at > NOW() - INTERVAL '24 hours'
    AND bo.status IN ('FILLED', 'DRY_RUN')
    ORDER BY bo.created_at DESC
    LIMIT 20
""")

print("\nRecent SELL orders vs positions:")
for ticker, action, side, count, pos_qty in cur.fetchall():
    pos_qty = pos_qty or 0
    issue = "⚠️ OVERSELL" if count > abs(pos_qty) else "✅ OK"
    print(f"  {ticker[:30]}: Sold {count} {side}, Position: {pos_qty:.1f} - {issue}")

cur.close()
conn.close()

print("\n" + "=" * 80)
print("SUMMARY:")
print("The oversell warnings occur when:")
print("1. System tries to sell more shares than it owns")
print("2. Position tracking is out of sync between orders and positions table")
print("3. Multiple orders are placed before position updates")
print("\nThis is a known issue that the position_replay module detects and prevents.")