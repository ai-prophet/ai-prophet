#!/usr/bin/env python3
"""Show the exact JSON response that the API endpoint will return."""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()

import psycopg2
from urllib.parse import urlparse

def simulate_api_response():
    """Simulate the exact API response for cycle evaluations."""

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
        LIMIT 5
    """

    cur.execute(query)
    rows = cur.fetchall()

    evaluations = []

    for row in rows:
        pred_id, market_id, p_yes, yes_ask, no_ask, model_name, eval_time, market_ticker, market_title, order_id, order_action, order_side, order_count, order_status, order_price = row

        # Calculate edge
        edge = None
        if p_yes is not None and yes_ask is not None:
            edge = (p_yes - yes_ask) * 100

        # Determine action
        if order_id:
            if order_status == "FILLED":
                action_taken = f"{order_action} {order_count} {order_side}"
                action_type = order_action.lower()
            else:
                action_taken = f"{order_action} {order_count} {order_side} ({order_status.lower()})"
                action_type = order_status.lower()
        else:
            action_taken = "HOLD"
            action_type = "hold"

        # Determine reason
        edge_info = None
        if edge is not None:
            if action_type == "hold":
                if abs(edge) < 3:
                    edge_info = f"Edge {edge:.1f}% < 3% threshold"
                elif p_yes > 0.95 or p_yes < 0.05:
                    edge_info = f"Edge {edge:.1f}% (extreme probability)"
                else:
                    edge_info = f"Edge {edge:.1f}% (position/capital limit)"
            else:
                edge_info = f"Edge {edge:.1f}% → {action_type}"

        evaluations.append({
            "id": pred_id,
            "ticker": market_ticker,
            "market_id": market_id,
            "market_title": market_title,
            "timestamp": eval_time.isoformat() if eval_time else None,
            "model": model_name,
            "prediction": {
                "p_yes": float(p_yes) if p_yes else None,
                "edge": edge,
                "yes_ask": float(yes_ask) if yes_ask else None,
                "no_ask": float(no_ask) if no_ask else None,
            },
            "action": {
                "type": action_type,
                "description": action_taken,
                "reason": edge_info,
            },
            "order": {
                "count": order_count,
                "price_cents": order_price,
                "status": order_status,
            } if order_id else None,
        })

    # Build the response
    response = {
        "evaluations": evaluations,
        "total": 10,
        "has_more": True,
        "ticker": "KXSPACEIPO-26MAR-27MAR"
    }

    cur.close()
    conn.close()

    # Display the response
    print("\n" + "="*80)
    print("📡 API RESPONSE: /cycle-evaluations?ticker=KXSPACEIPO-26MAR-27MAR")
    print("="*80)
    print("\nThis is the exact JSON response the API will return:\n")
    print(json.dumps(response, indent=2))

    print("\n" + "="*80)
    print("📊 RESPONSE BREAKDOWN")
    print("="*80)

    print(f"\nShowing {len(evaluations)} most recent evaluations:")
    for eval in evaluations:
        icon = {
            'hold': '⏸️',
            'buy': '🟢',
            'sell': '🔴'
        }.get(eval['action']['type'], '❓')

        print(f"\n{icon} {eval['action']['description']}")
        print(f"   Edge: {eval['prediction']['edge']:.1f}%" if eval['prediction']['edge'] else "   Edge: N/A")
        print(f"   Reason: {eval['action']['reason']}")

if __name__ == "__main__":
    simulate_api_response()