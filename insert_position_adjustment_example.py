#!/usr/bin/env python3
"""
Insert sample position adjustment data to demonstrate SELL+BUY in same timestep.
Example: Have 3 YES shares, want to change to 6 NO shares.
So we SELL 3 YES and BUY 6 NO at the same timestamp.
"""

import psycopg2
from datetime import datetime, timedelta
import os

# Database connection
conn = psycopg2.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    port=os.getenv('DB_PORT', '5432'),
    database=os.getenv('DB_NAME', 'trading'),
    user=os.getenv('DB_USER', 'postgres'),
    password=os.getenv('DB_PASS', '')
)

try:
    with conn.cursor() as cur:
        # Market details
        MARKET_ID = "ADJUSTMENT-TEST-001"
        TICKER = "ADJTEST"
        INSTANCE = "Haifeng"

        # Create a test market
        cur.execute("""
            INSERT INTO trading_markets (
                market_id, ticker, event_ticker, title, subtitle, yes_subtitle, no_subtitle,
                category, rules, image_url, last_price, yes_bid, yes_ask, no_bid, no_ask,
                yes_price_24h_ago, no_price_24h_ago, volume_24h, volume, open_interest,
                expected_date, instance_name, last_traded_ts, sync_ts
            ) VALUES (
                %s, %s, %s, 'Position Adjustment Test Market',
                'Testing SELL+BUY adjustment', 'Yes wins', 'No wins',
                'TEST', 'Test rules', NULL, 0.65, 0.64, 0.66, 0.34, 0.36,
                0.50, 0.50, 1000, 5000, 2000,
                %s, %s, %s, %s
            )
            ON CONFLICT (market_id, instance_name) DO UPDATE
            SET title = EXCLUDED.title
        """, (
            MARKET_ID, TICKER, TICKER,
            datetime.now() + timedelta(days=7),
            INSTANCE,
            datetime.now(),
            datetime.now()
        ))

        # Position adjustment timestamp (same for both SELL and BUY)
        adjustment_time = datetime.now() - timedelta(minutes=15)

        # 1. Create prediction for SELL action (selling 3 YES)
        cur.execute("""
            INSERT INTO betting_predictions (
                tick_ts, market_id, instance_name, model_name,
                p_yes, yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (
                %s, %s, %s, 'gemini-3.1-pro',
                0.35, 0.64, 0.66, 0.34, 0.36
            ) RETURNING id
        """, (adjustment_time, MARKET_ID, INSTANCE))
        sell_prediction_id = cur.fetchone()[0]

        # Create SELL signal
        cur.execute("""
            INSERT INTO betting_signals (
                prediction_id, instance_name, strategy_name,
                side, price, shares, cost, created_at
            ) VALUES (
                %s, %s, 'default', 'sell', 65, 3, -195, %s
            ) RETURNING id
        """, (sell_prediction_id, INSTANCE, adjustment_time))
        sell_signal_id = cur.fetchone()[0]

        # Create SELL order (fully filled)
        cur.execute("""
            INSERT INTO betting_orders (
                signal_id, market_id, instance_name, ticker, event_ticker,
                client_order_id, order_id, action, side, count, filled_shares,
                price_cents, status, created_at, updated_at, last_fill_time
            ) VALUES (
                %s, %s, %s, %s, %s,
                'sell-adj-001', 'kalshi-sell-001', 'sell', 'yes', 3, 3,
                65, 'FILLED', %s, %s, %s
            )
        """, (
            sell_signal_id, MARKET_ID, INSTANCE, TICKER, TICKER,
            adjustment_time, adjustment_time + timedelta(seconds=2),
            adjustment_time + timedelta(seconds=2)
        ))

        # 2. Create prediction for BUY action (buying 6 NO) - same timestamp
        cur.execute("""
            INSERT INTO betting_predictions (
                tick_ts, market_id, instance_name, model_name,
                p_yes, yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (
                %s, %s, %s, 'gemini-3.1-pro',
                0.35, 0.64, 0.66, 0.34, 0.36
            ) RETURNING id
        """, (adjustment_time, MARKET_ID, INSTANCE))
        buy_prediction_id = cur.fetchone()[0]

        # Create BUY signal
        cur.execute("""
            INSERT INTO betting_signals (
                prediction_id, instance_name, strategy_name,
                side, price, shares, cost, created_at
            ) VALUES (
                %s, %s, 'default', 'no', 36, 6, 216, %s
            ) RETURNING id
        """, (buy_prediction_id, INSTANCE, adjustment_time))
        buy_signal_id = cur.fetchone()[0]

        # Create BUY order (partially filled - only 4 out of 6)
        cur.execute("""
            INSERT INTO betting_orders (
                signal_id, market_id, instance_name, ticker, event_ticker,
                client_order_id, order_id, action, side, count, filled_shares,
                price_cents, status, created_at, updated_at, last_fill_time
            ) VALUES (
                %s, %s, %s, %s, %s,
                'buy-adj-001', 'kalshi-buy-001', 'buy', 'no', 6, 4,
                36, 'PENDING', %s, %s, %s
            )
        """, (
            buy_signal_id, MARKET_ID, INSTANCE, TICKER, TICKER,
            adjustment_time, adjustment_time + timedelta(seconds=3),
            adjustment_time + timedelta(seconds=3)
        ))

        # Add some other events for context

        # Earlier BUY event
        earlier_time = datetime.now() - timedelta(hours=1)
        cur.execute("""
            INSERT INTO betting_predictions (
                tick_ts, market_id, instance_name, model_name,
                p_yes, yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (
                %s, %s, %s, 'gemini-3.1-pro',
                0.70, 0.59, 0.61, 0.39, 0.41
            ) RETURNING id
        """, (earlier_time, MARKET_ID, INSTANCE))
        earlier_pred_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO betting_signals (
                prediction_id, instance_name, strategy_name,
                side, price, shares, cost, created_at
            ) VALUES (
                %s, %s, 'default', 'yes', 61, 3, 183, %s
            ) RETURNING id
        """, (earlier_pred_id, INSTANCE, earlier_time))
        earlier_signal_id = cur.fetchone()[0]

        cur.execute("""
            INSERT INTO betting_orders (
                signal_id, market_id, instance_name, ticker, event_ticker,
                client_order_id, order_id, action, side, count, filled_shares,
                price_cents, status, created_at, updated_at, last_fill_time
            ) VALUES (
                %s, %s, %s, %s, %s,
                'buy-early-001', 'kalshi-early-001', 'buy', 'yes', 3, 3,
                61, 'FILLED', %s, %s, %s
            )
        """, (
            earlier_signal_id, MARKET_ID, INSTANCE, TICKER, TICKER,
            earlier_time, earlier_time + timedelta(seconds=1),
            earlier_time + timedelta(seconds=1)
        ))

        # Recent HOLD event
        hold_time = datetime.now() - timedelta(minutes=5)
        cur.execute("""
            INSERT INTO betting_predictions (
                tick_ts, market_id, instance_name, model_name,
                p_yes, yes_bid, yes_ask, no_bid, no_ask
            ) VALUES (
                %s, %s, %s, 'gemini-3.1-pro',
                0.67, 0.64, 0.66, 0.34, 0.36
            ) RETURNING id
        """, (hold_time, MARKET_ID, INSTANCE))
        hold_pred_id = cur.fetchone()[0]

        # No signal for HOLD

        conn.commit()
        print(f"✓ Created position adjustment example:")
        print(f"  Market: {TICKER}")
        print(f"  Earlier: BUY 3 YES @ 61c (filled)")
        print(f"  Hold: Model 67%, Market 66c, Edge +1%")
        print(f"  ADJUSTMENT at {adjustment_time.strftime('%H:%M')}:")
        print(f"    - SELL 3 YES @ 65c (3/3 filled) ✓")
        print(f"    - BUY 6 NO @ 36c (4/6 filled)")
        print(f"  Net result: From 3 YES → 4 NO")

except Exception as e:
    print(f"Error: {e}")
    conn.rollback()
finally:
    conn.close()