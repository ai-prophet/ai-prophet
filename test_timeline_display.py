#!/usr/bin/env python3
"""Test the timeline display with the test data we just created."""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

def format_timeline():
    """Format the timeline as it will appear in the dashboard."""

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

    # Query exactly as the API endpoint does
    query = """
        SELECT
            bp.id as pred_id,
            bp.market_id,
            bp.p_yes,
            bp.yes_ask,
            bp.no_ask,
            bp.source as model_name,
            bp.created_at as eval_time,
            tm.ticker as market_ticker,
            tm.title as market_title,
            bo.id as order_id,
            bo.action as order_action,
            bo.side as order_side,
            bo.count as order_count,
            bo.status as order_status,
            bo.price_cents as order_price
        FROM betting_predictions bp
        LEFT JOIN trading_markets tm ON tm.market_id = bp.market_id AND tm.instance_name = bp.instance_name
        LEFT JOIN betting_orders bo ON bo.ticker = tm.ticker
            AND bo.instance_name = bp.instance_name
            AND bo.created_at BETWEEN bp.created_at AND bp.created_at + INTERVAL '1 minute'
        WHERE bp.instance_name = 'Haifeng'
        AND tm.ticker = 'KXSPACEIPO-26MAR-27MAR'
        AND bp.source = 'test:demonstration'
        ORDER BY bp.created_at DESC
        LIMIT 15
    """

    cur.execute(query)
    rows = cur.fetchall()

    print("\n" + "="*80)
    print("📊 TIMELINE VIEW: SpaceX IPO Market")
    print("="*80)
    print("\nThis is exactly what will appear in the dashboard timeline:")
    print("(Showing ALL cycle evaluations - holds, buys, and sells)\n")

    evaluations = []

    for row in rows:
        pred_id, market_id, p_yes, yes_ask, no_ask, model_name, eval_time, market_ticker, market_title, order_id, order_action, order_side, order_count, order_status, order_price = row

        # Calculate edge (same logic as API endpoint)
        edge = None
        if p_yes is not None and yes_ask is not None:
            edge = (p_yes - yes_ask) * 100

        # Determine action (same logic as API endpoint)
        if order_id:
            # An order was placed
            if order_status == "FILLED":
                action_taken = f"{order_action} {order_count} {order_side}"
                action_type = order_action.lower()
            elif order_status == "DRY_RUN":
                action_taken = f"{order_action} {order_count} {order_side} (dry run)"
                action_type = "dry_run"
            else:
                action_taken = f"{order_action} {order_count} {order_side} (pending)"
                action_type = "pending"
        else:
            # No order = HOLD decision
            action_taken = "HOLD"
            action_type = "hold"

        # Determine reason (same logic as API endpoint)
        edge_info = None
        if edge is not None:
            if action_type == "hold":
                # Explain why it was held
                if abs(edge) < 3:
                    edge_info = f"Edge {edge:.1f}% < 3% threshold"
                elif p_yes > 0.95 or p_yes < 0.05:
                    edge_info = f"Edge {edge:.1f}% (extreme probability)"
                else:
                    edge_info = f"Edge {edge:.1f}% (position/capital limit)"
            else:
                edge_info = f"Edge {edge:.1f}% → {action_type}"

        evaluations.append({
            'time': eval_time,
            'action_type': action_type,
            'action_taken': action_taken,
            'p_yes': p_yes,
            'yes_ask': yes_ask,
            'edge': edge,
            'reason': edge_info
        })

    # Display timeline in chronological order (oldest first)
    evaluations.reverse()

    # Group by action type for summary
    action_counts = {'hold': 0, 'buy': 0, 'sell': 0, 'dry_run': 0}

    for i, eval_data in enumerate(evaluations, 1):
        time_str = eval_data['time'].strftime('%I:%M %p')
        action_type = eval_data['action_type']

        # Count actions
        if action_type in action_counts:
            action_counts[action_type] += 1

        # Set color and icon
        if action_type == 'buy':
            color = '\033[92m'  # Green
            icon = '🟢 BUY '
        elif action_type == 'sell':
            color = '\033[91m'  # Red
            icon = '🔴 SELL'
        elif action_type == 'hold':
            color = '\033[93m'  # Yellow
            icon = '⏸️  HOLD'
        else:
            color = '\033[96m'  # Cyan
            icon = '🔵 TEST'

        reset = '\033[0m'

        # Format the entry
        print(f"{color}{'─'*70}{reset}")
        print(f"{icon} {time_str} | {color}{eval_data['action_taken']}{reset}")
        print(f"  📈 Model prediction: {eval_data['p_yes']*100:.1f}%")
        print(f"  💹 Market ask price: {eval_data['yes_ask']*100:.1f}%")
        if eval_data['edge'] is not None:
            edge_color = '\033[92m' if eval_data['edge'] > 0 else '\033[91m'
            print(f"  📊 Edge: {edge_color}{eval_data['edge']:+.1f}%{reset}")
        print(f"  💡 Decision: {eval_data['reason']}")

    # Summary statistics
    print(f"\n{'='*80}")
    print("📈 SUMMARY STATISTICS")
    print("="*80)

    total = len(evaluations)
    print(f"\nTotal Cycle Evaluations: {total}")
    print("\nBreakdown by Action:")

    for action_type, count in action_counts.items():
        if count > 0:
            percentage = (count / total * 100) if total > 0 else 0
            emoji = {'hold': '⏸️', 'buy': '🟢', 'sell': '🔴', 'dry_run': '🔵'}.get(action_type, '❓')
            print(f"  {emoji} {action_type.upper():<8} {count:>3} ({percentage:>5.1f}%)")

    # Show hold reasons breakdown
    hold_reasons = {}
    for eval_data in evaluations:
        if eval_data['action_type'] == 'hold' and eval_data['reason']:
            reason = eval_data['reason']
            if '< 3% threshold' in reason:
                key = 'Edge below threshold'
            elif 'extreme probability' in reason:
                key = 'Extreme probability'
            elif 'position/capital limit' in reason:
                key = 'Position/capital limit'
            else:
                key = 'Other'
            hold_reasons[key] = hold_reasons.get(key, 0) + 1

    if hold_reasons:
        print("\nHOLD Decision Reasons:")
        for reason, count in sorted(hold_reasons.items()):
            print(f"  • {reason}: {count}")

    print(f"\n{'='*80}")
    print("✅ KEY INSIGHTS FROM THIS TIMELINE")
    print("="*80)
    print("1. ⏸️  HOLDS are clearly shown with specific reasons")
    print("2. 📊 Edge calculation is visible for every evaluation")
    print("3. 🎯 System behavior is transparent at each cycle")
    print("4. 💡 Decision reasoning helps understand why actions were taken/not taken")

    cur.close()
    conn.close()

if __name__ == "__main__":
    format_timeline()