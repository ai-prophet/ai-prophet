#!/usr/bin/env python3
"""Simple test to verify oversell prevention is working."""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

def test_oversell_prevention():
    """Check if oversells are still happening after the fix."""

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

    print("=" * 80)
    print("OVERSELL PREVENTION TEST")
    print("=" * 80)

    # 1. Check for any oversells in the last hour (after fix was deployed)
    print("\n1. Checking for oversells in last hour (after fix)...")

    cur.execute("""
        WITH position_calc AS (
            SELECT
                ticker,
                SUM(CASE
                    WHEN action = 'BUY' AND side IN ('YES', 'yes') THEN count
                    WHEN action = 'SELL' AND side IN ('YES', 'yes') THEN -count
                    ELSE 0
                END) as yes_net,
                SUM(CASE
                    WHEN action = 'BUY' AND side IN ('NO', 'no') THEN count
                    WHEN action = 'SELL' AND side IN ('NO', 'no') THEN -count
                    ELSE 0
                END) as no_net,
                MAX(created_at) as last_order
            FROM betting_orders
            WHERE instance_name = 'Haifeng'
            AND status IN ('FILLED', 'DRY_RUN')
            GROUP BY ticker
        )
        SELECT ticker, yes_net, no_net, last_order
        FROM position_calc
        WHERE (yes_net < -5 OR no_net < -5)
        AND last_order > NOW() - INTERVAL '1 hour'
        ORDER BY last_order DESC
        LIMIT 10
    """)

    recent_oversells = cur.fetchall()

    if recent_oversells:
        print("   ⚠️  Oversells still occurring:")
        for ticker, yes_net, no_net, last_order in recent_oversells:
            time_ago = datetime.now() - last_order.replace(tzinfo=None)
            minutes = int(time_ago.total_seconds() / 60)
            if yes_net < -5:
                print(f"      {ticker}: YES oversold by {abs(yes_net)} ({minutes} min ago)")
            if no_net < -5:
                print(f"      {ticker}: NO oversold by {abs(no_net)} ({minutes} min ago)")
    else:
        print("   ✅ No new oversells in last hour!")

    # 2. Check system logs for prevention messages
    print("\n2. Checking for oversell prevention logs...")

    cur.execute("""
        SELECT message, created_at
        FROM system_logs
        WHERE (message LIKE '%OVERSELL PREVENTED%'
               OR message LIKE '%Oversell ignored%'
               OR message LIKE '%Position replay warning%')
        AND created_at > NOW() - INTERVAL '30 minutes'
        ORDER BY created_at DESC
        LIMIT 5
    """)

    logs = cur.fetchall()

    if logs:
        print("   Recent prevention logs:")
        for message, created_at in logs:
            time_ago = datetime.now() - created_at.replace(tzinfo=None)
            minutes = int(time_ago.total_seconds() / 60)
            # Truncate long messages
            msg = message[:100] + "..." if len(message) > 100 else message
            print(f"      {minutes}m ago: {msg}")
    else:
        print("   No prevention logs in last 30 minutes")

    # 3. Check specific problematic tickers
    print("\n3. Checking problematic tickers...")

    problem_tickers = ['KXDHSFUND-26APR01', 'KXALBUMRELEASEDATEUZI-APR01-26']

    for ticker in problem_tickers:
        cur.execute("""
            SELECT
                COUNT(*) as order_count,
                MAX(created_at) as last_order,
                SUM(CASE
                    WHEN action = 'SELL' AND status IN ('FILLED', 'DRY_RUN')
                    THEN count ELSE 0
                END) as total_sells
            FROM betting_orders
            WHERE ticker = %s
            AND instance_name = 'Haifeng'
            AND created_at > NOW() - INTERVAL '1 hour'
        """, (ticker,))

        count, last_order, sells = cur.fetchone()

        if count and count > 0:
            time_ago = datetime.now() - last_order.replace(tzinfo=None)
            minutes = int(time_ago.total_seconds() / 60)
            print(f"   {ticker[:30]:30} {count:3} orders, {sells or 0:3} sells ({minutes}m ago)")
        else:
            print(f"   {ticker[:30]:30} No recent activity")

    # 4. Compare before and after fix
    print("\n4. Oversell comparison (before vs after fix)...")

    # Get deployment time (approximately 30 minutes ago)
    fix_time = datetime.now() - timedelta(minutes=30)

    cur.execute("""
        WITH periods AS (
            SELECT
                CASE
                    WHEN created_at < %s THEN 'BEFORE'
                    ELSE 'AFTER'
                END as period,
                ticker
            FROM betting_orders
            WHERE instance_name = 'Haifeng'
            AND status IN ('FILLED', 'DRY_RUN')
            AND created_at > NOW() - INTERVAL '2 hours'
        ),
        oversells AS (
            SELECT
                p.period,
                COUNT(DISTINCT p.ticker) as affected_markets
            FROM periods p
            JOIN (
                SELECT ticker
                FROM betting_orders
                WHERE instance_name = 'Haifeng'
                AND status IN ('FILLED', 'DRY_RUN')
                GROUP BY ticker
                HAVING SUM(CASE
                    WHEN action = 'BUY' AND side IN ('YES', 'yes') THEN count
                    WHEN action = 'SELL' AND side IN ('YES', 'yes') THEN -count
                    ELSE 0
                END) < -5
                OR SUM(CASE
                    WHEN action = 'BUY' AND side IN ('NO', 'no') THEN count
                    WHEN action = 'SELL' AND side IN ('NO', 'no') THEN -count
                    ELSE 0
                END) < -5
            ) o ON p.ticker = o.ticker
            GROUP BY p.period
        )
        SELECT period, affected_markets FROM oversells
    """, (fix_time,))

    comparison = cur.fetchall()

    if comparison:
        for period, markets in comparison:
            print(f"   {period:6}: {markets} markets with oversells")
    else:
        print("   No data for comparison")

    cur.close()
    conn.close()

    print("\n" + "=" * 80)
    print("TEST RESULTS:")
    print("=" * 80)

    if not recent_oversells:
        print("✅ SUCCESS: No new oversells detected after fix!")
        print("   The prevention mechanism is working.")
    else:
        print("⚠️  WARNING: Oversells still occurring.")
        print("   The fix may not be fully deployed yet.")
        print("   Monitor for 'OVERSELL PREVENTED' in logs.")

# Run test
if __name__ == "__main__":
    test_oversell_prevention()