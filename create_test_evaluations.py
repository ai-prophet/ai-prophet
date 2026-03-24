#!/usr/bin/env python3
"""Create test cycle evaluations in the database to demonstrate timeline display."""

import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "packages", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

def create_test_data():
    """Create test predictions and orders to show different cycle evaluation types."""

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

    # Configuration
    instance_name = 'Haifeng'
    test_ticker = 'KXSPACEIPO-26MAR-27MAR'  # SpaceX IPO market
    model_name = 'test:demonstration'

    # Get market_id for this ticker
    cur.execute("""
        SELECT market_id, title FROM trading_markets
        WHERE ticker = %s AND instance_name = %s
        LIMIT 1
    """, (test_ticker, instance_name))

    result = cur.fetchone()
    if not result:
        print(f"Market {test_ticker} not found. Creating it...")
        market_id = f"test-market-{test_ticker}"
        cur.execute("""
            INSERT INTO trading_markets (market_id, ticker, event_ticker, title, instance_name, yes_ask, no_ask, updated_at)
            VALUES (%s, %s, %s, %s, %s, 0.40, 0.62, NOW())
            ON CONFLICT (market_id, instance_name) DO UPDATE
            SET ticker = EXCLUDED.ticker, title = EXCLUDED.title, updated_at = NOW()
        """, (market_id, test_ticker, 'SPACEIPO', "SpaceX IPO by March 2027", instance_name))
        conn.commit()
    else:
        market_id = result[0]
        print(f"Using existing market: {market_id}")

    # Base timestamp (start 2 hours ago)
    base_time = datetime.now(timezone.utc) - timedelta(hours=2)

    # Test scenarios for cycle evaluations
    scenarios = [
        # HOLD: Edge below threshold
        {
            'time_offset': 0,
            'p_yes': 0.42,
            'yes_ask': 0.40,
            'no_ask': 0.62,
            'action': None,  # No order = HOLD
            'comment': 'HOLD: Edge 2.0% < 3% threshold'
        },

        # HOLD: Edge below threshold (negative)
        {
            'time_offset': 10,
            'p_yes': 0.38,
            'yes_ask': 0.40,
            'no_ask': 0.62,
            'action': None,
            'comment': 'HOLD: Edge -2.0% < 3% threshold'
        },

        # BUY: Good positive edge
        {
            'time_offset': 20,
            'p_yes': 0.48,
            'yes_ask': 0.40,
            'no_ask': 0.62,
            'action': ('BUY', 'YES', 100, 41),  # (action, side, count, price_cents)
            'comment': 'BUY: Edge 8.0% > threshold'
        },

        # HOLD: Small edge
        {
            'time_offset': 30,
            'p_yes': 0.41,
            'yes_ask': 0.39,
            'no_ask': 0.63,
            'action': None,
            'comment': 'HOLD: Edge 2.0% < 3% threshold'
        },

        # SELL: Negative edge (should sell YES)
        {
            'time_offset': 40,
            'p_yes': 0.35,
            'yes_ask': 0.42,
            'no_ask': 0.60,
            'action': ('SELL', 'YES', 50, 42),
            'comment': 'SELL: Edge -7.0% (closing position)'
        },

        # HOLD: Extreme probability
        {
            'time_offset': 50,
            'p_yes': 0.97,
            'yes_ask': 0.94,
            'no_ask': 0.08,
            'action': None,
            'comment': 'HOLD: Edge 3.0% but probability > 95%'
        },

        # HOLD: At position limit
        {
            'time_offset': 60,
            'p_yes': 0.50,
            'yes_ask': 0.42,
            'no_ask': 0.60,
            'action': None,
            'comment': 'HOLD: Edge 8.0% but at position limit'
        },

        # BUY: Strong edge
        {
            'time_offset': 70,
            'p_yes': 0.55,
            'yes_ask': 0.43,
            'no_ask': 0.59,
            'action': ('BUY', 'YES', 200, 44),
            'comment': 'BUY: Edge 12.0% > threshold'
        },

        # HOLD: Zero edge
        {
            'time_offset': 80,
            'p_yes': 0.45,
            'yes_ask': 0.45,
            'no_ask': 0.57,
            'action': None,
            'comment': 'HOLD: Edge 0.0% < 3% threshold'
        },

        # HOLD: Very small positive edge
        {
            'time_offset': 90,
            'p_yes': 0.425,
            'yes_ask': 0.42,
            'no_ask': 0.60,
            'action': None,
            'comment': 'HOLD: Edge 0.5% < 3% threshold'
        },
    ]

    print(f"\nCreating {len(scenarios)} test cycle evaluations...")
    print("=" * 80)

    # Get a valid signal_id for orders (required by foreign key)
    cur.execute("SELECT id FROM betting_signals WHERE instance_name = %s LIMIT 1", (instance_name,))
    signal_result = cur.fetchone()
    if signal_result:
        signal_id = signal_result[0]
    else:
        # Create a dummy signal if none exists
        cur.execute("""
            INSERT INTO betting_signals (prediction_id, strategy_name, side, shares, price, cost, instance_name, created_at)
            VALUES (1, 'test', 'YES', 1, 0.50, 0.50, %s, NOW())
            RETURNING id
        """, (instance_name,))
        signal_id = cur.fetchone()[0]
        conn.commit()

    for i, scenario in enumerate(scenarios):
        timestamp = base_time + timedelta(minutes=scenario['time_offset'])

        # Insert prediction (every cycle has a prediction)
        cur.execute("""
            INSERT INTO betting_predictions (
                tick_ts, market_id, source, p_yes, yes_ask, no_ask,
                created_at, instance_name
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            timestamp, market_id, model_name,
            scenario['p_yes'], scenario['yes_ask'], scenario['no_ask'],
            timestamp, instance_name
        ))
        pred_id = cur.fetchone()[0]

        # If there's an action, create an order
        if scenario['action']:
            action, side, count, price_cents = scenario['action']
            order_id = f"test-order-{i}-{timestamp.timestamp()}"

            # Insert order (within 30 seconds of prediction to ensure correlation)
            order_time = timestamp + timedelta(seconds=15)
            cur.execute("""
                INSERT INTO betting_orders (
                    signal_id, order_id, ticker, action, side, count,
                    price_cents, status, filled_shares, fill_price,
                    exchange_order_id, dry_run, created_at, instance_name
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                signal_id, order_id, test_ticker, action, side, count,
                price_cents, 'FILLED', float(count), price_cents / 100.0,
                f"kalshi-{order_id}", False, order_time, instance_name
            ))

            status = f"✅ {action} {count} {side}"
        else:
            status = "⏸️  HOLD"

        edge = (scenario['p_yes'] - scenario['yes_ask']) * 100

        print(f"\n{i+1}. {timestamp.strftime('%I:%M %p')}: {status}")
        print(f"   P(YES): {scenario['p_yes']:.1%} | Market: {scenario['yes_ask']:.1%} | Edge: {edge:+.1f}%")
        print(f"   → {scenario['comment']}")

    conn.commit()
    print("\n" + "=" * 80)
    print(f"✅ Created {len(scenarios)} test cycle evaluations")

    # Now test the query to see what will be displayed
    print("\n" + "=" * 80)
    print("TESTING TIMELINE DISPLAY")
    print("=" * 80)

    query = """
        SELECT
            bp.p_yes,
            bp.yes_ask,
            bp.created_at as eval_time,
            bo.id as order_id,
            bo.action as order_action,
            bo.side as order_side,
            bo.count as order_count
        FROM betting_predictions bp
        LEFT JOIN trading_markets tm ON tm.market_id = bp.market_id AND tm.instance_name = bp.instance_name
        LEFT JOIN betting_orders bo ON bo.ticker = tm.ticker
            AND bo.instance_name = bp.instance_name
            AND bo.created_at BETWEEN bp.created_at AND bp.created_at + INTERVAL '1 minute'
        WHERE bp.instance_name = %s
        AND tm.ticker = %s
        AND bp.source = %s
        ORDER BY bp.created_at DESC
        LIMIT 15
    """

    cur.execute(query, (instance_name, test_ticker, model_name))
    results = cur.fetchall()

    print(f"\nTimeline will show {len(results)} evaluations:")
    for p_yes, yes_ask, eval_time, order_id, action, side, count in results:
        edge = (p_yes - yes_ask) * 100 if p_yes and yes_ask else 0

        if order_id:
            action_desc = f"{action} {count} {side}"
            if edge < 3 and edge > -3:
                reason = f"Edge {edge:+.1f}% but still traded (test)"
            else:
                reason = f"Edge {edge:+.1f}% → {action.lower()}"
        else:
            if abs(edge) < 3:
                reason = f"Edge {edge:+.1f}% < 3% threshold"
            elif p_yes > 0.95:
                reason = f"Edge {edge:+.1f}% (probability > 95%)"
            elif p_yes < 0.05:
                reason = f"Edge {edge:+.1f}% (probability < 5%)"
            elif edge > 3:
                reason = f"Edge {edge:+.1f}% (position/capital limit)"
            else:
                reason = f"Edge {edge:+.1f}% (hold for other reason)"
            action_desc = "HOLD"

        time_str = eval_time.strftime('%I:%M %p')
        icon = "🟢" if action == "BUY" else "🔴" if action == "SELL" else "⏸️"
        print(f"  {icon} {time_str}: {action_desc:<20} | {reason}")

    cur.close()
    conn.close()

    print("\n" + "=" * 80)
    print("✅ Test data created successfully!")
    print("\nThe timeline will now show:")
    print("- HOLD decisions with specific reasons (edge below threshold, position limits, etc.)")
    print("- BUY/SELL trades when edge exceeds threshold")
    print("- Every cycle evaluation with its edge calculation")

if __name__ == "__main__":
    create_test_data()