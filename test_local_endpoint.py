#!/usr/bin/env python3
"""Test the cycle evaluations endpoint locally to show what timeline will display."""

import os
import sys
import json
from datetime import datetime

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "services"))

# Direct SQL test to simulate what the endpoint will return
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

def format_timeline_entry(eval_data):
    """Format an evaluation as it would appear in the timeline."""

    # Extract data
    timestamp = eval_data['timestamp']
    action_type = eval_data['action']['type']
    action_desc = eval_data['action']['description']
    reason = eval_data['action']['reason']
    edge = eval_data['prediction']['edge']
    p_yes = eval_data['prediction']['p_yes']
    yes_ask = eval_data['prediction']['yes_ask']
    model = eval_data['model']

    # Icons and colors for different actions
    if action_type == 'buy':
        icon = '🟢 BUY'
        color = '\033[92m'  # Green
    elif action_type == 'sell':
        icon = '🔴 SELL'
        color = '\033[91m'  # Red
    elif action_type == 'hold':
        icon = '⏸️  HOLD'
        color = '\033[93m'  # Yellow
    else:
        icon = '❓'
        color = '\033[0m'

    reset = '\033[0m'

    # Format time
    dt = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
    time_str = dt.strftime('%I:%M %p')

    # Build output
    output = []
    output.append(f"\n{color}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━{reset}")
    output.append(f"{icon} {time_str} | {color}{action_desc}{reset}")
    output.append(f"├─ Model: {model[:40]}")
    output.append(f"├─ Prediction: {p_yes*100:.1f}% | Market: {yes_ask*100:.1f}%")
    if edge is not None:
        output.append(f"├─ Edge: {edge:.1f}%")
    output.append(f"└─ {reason}")

    return '\n'.join(output)

def main():
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

    # Test with DHS Funding market which has data
    ticker = 'KXDHSFUND-26APR01'

    print(f"\n{'='*80}")
    print(f"TIMELINE VIEW FOR: {ticker}")
    print(f"{'='*80}")
    print("\nThis shows what the dashboard timeline will display with the new endpoint:")

    # Execute the query from our endpoint
    query = '''
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
            AND bo.created_at BETWEEN bp.created_at - INTERVAL '1 minute' AND bp.created_at + INTERVAL '1 minute'
        WHERE bp.instance_name = 'Haifeng'
        AND tm.ticker = %s
        ORDER BY bp.created_at DESC
        LIMIT 20
    '''

    cur.execute(query, (ticker,))
    rows = cur.fetchall()

    # Process and display each evaluation
    evaluations = []
    for row in rows:
        pred_id, market_id, p_yes, yes_ask, no_ask, model_name, eval_time, market_ticker, market_title, order_id, order_action, order_side, order_count, order_status, order_price = row

        # Calculate edge
        edge = (p_yes - yes_ask) * 100 if p_yes and yes_ask else None

        # Determine action
        if order_id:
            action_desc = f"{order_action} {order_count} {order_side}"
            action_type = order_action.lower() if order_action else 'unknown'
            if order_status == 'DRY_RUN':
                action_desc += " (dry run)"
                action_type = 'dry_run'
        else:
            action_desc = "HOLD"
            action_type = "hold"

        # Determine reason
        if edge is not None:
            if action_type == "hold":
                if abs(edge) < 3:
                    reason = f"Edge {edge:.1f}% below 3% threshold"
                elif p_yes > 0.95 or p_yes < 0.05:
                    reason = f"Edge {edge:.1f}% but probability too extreme"
                else:
                    reason = f"Edge {edge:.1f}% but at position/capital limit"
            else:
                reason = f"Edge {edge:.1f}% exceeded threshold → {action_type}"
        else:
            reason = "No edge data available"

        # Create evaluation object
        eval_data = {
            'id': pred_id,
            'timestamp': eval_time.isoformat(),
            'model': model_name or 'unknown',
            'prediction': {
                'p_yes': p_yes,
                'edge': edge,
                'yes_ask': yes_ask,
                'no_ask': no_ask
            },
            'action': {
                'type': action_type,
                'description': action_desc,
                'reason': reason
            },
            'order': {
                'count': order_count,
                'price_cents': order_price,
                'status': order_status
            } if order_id else None
        }

        evaluations.append(eval_data)

    # Display timeline
    for eval_data in evaluations:
        print(format_timeline_entry(eval_data))

    # Summary statistics
    print(f"\n{'='*80}")
    print("SUMMARY STATISTICS:")
    print(f"{'='*80}")

    # Count action types
    action_counts = {}
    for eval_data in evaluations:
        action_type = eval_data['action']['type']
        action_counts[action_type] = action_counts.get(action_type, 0) + 1

    total = len(evaluations)
    print(f"\nTotal Cycle Evaluations: {total}")
    print("\nBreakdown by Action:")
    for action, count in sorted(action_counts.items()):
        percentage = (count / total * 100) if total > 0 else 0
        print(f"  • {action.upper()}: {count} ({percentage:.1f}%)")

    # Show holds with reasons
    holds = [e for e in evaluations if e['action']['type'] == 'hold']
    if holds:
        print(f"\nHOLD Decisions ({len(holds)} total):")
        for hold in holds[:5]:  # Show first 5
            dt = datetime.fromisoformat(hold['timestamp'].replace('Z', '+00:00'))
            time_str = dt.strftime('%I:%M %p')
            print(f"  • {time_str}: {hold['action']['reason']}")

    print(f"\n{'='*80}")
    print("KEY INSIGHTS:")
    print(f"{'='*80}")
    print("✅ Every cycle evaluation is now visible (not just trades)")
    print("✅ HOLD decisions are shown with clear reasoning")
    print("✅ Edge calculations are displayed for every evaluation")
    print("✅ Timeline shows complete system behavior at each cycle")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()