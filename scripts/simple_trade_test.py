#!/usr/bin/env python3
"""Simple trading test - directly call Kalshi API and database."""

from __future__ import annotations

import argparse
import base64
import os
import sys
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

load_dotenv()


def sign_kalshi_request(method, path, api_key_id, private_key_b64):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key_bytes = base64.b64decode(private_key_b64)
    private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())

    timestamp_str = str(int(datetime.now().timestamp() * 1000))
    msg_string = timestamp_str + method.upper() + path

    signature = private_key.sign(
        msg_string.encode("utf-8"),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        "Content-Type": "application/json",
    }


def get_credentials():
    api_key_id = os.getenv("KALSHI_API_KEY_ID")
    private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64")
    base_url = os.getenv("KALSHI_BASE_URL") or "https://api.elections.kalshi.com"
    return api_key_id, private_key_b64, base_url


def get_market(ticker):
    api_key_id, private_key_b64, base_url = get_credentials()
    path = f"/trade-api/v2/markets/{ticker}"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json().get("market", {})


def get_position(ticker):
    api_key_id, private_key_b64, base_url = get_credentials()
    path = "/trade-api/v2/portfolio/positions"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()

    positions = response.json().get("market_positions", [])
    for pos in positions:
        if pos.get("ticker") == ticker:
            position_fp = float(pos.get("position_fp", 0))
            if position_fp > 0:
                return "yes", int(position_fp)
            elif position_fp < 0:
                return "no", int(abs(position_fp))
    return None, 0


def get_cash():
    api_key_id, private_key_b64, base_url = get_credentials()
    path = "/trade-api/v2/portfolio/balance"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=10)
    response.raise_for_status()
    return response.json().get("balance", 0) / 100.0


def calculate_rebalancing_order(p_yes, yes_ask, no_ask, current_side, current_qty):
    """Calculate order based on rebalancing strategy."""
    # Target position: p - q (in fractional units)
    target = p_yes - yes_ask

    # Current position in YES-equivalent fractional units
    if current_side == "yes":
        current_pos = current_qty / 100.0
    elif current_side == "no":
        current_pos = -current_qty / 100.0
    else:
        current_pos = 0.0

    # Delta to reach target
    delta = target - current_pos

    # Skip if too small
    if abs(delta) < 0.005:
        return None, 0, 0.0

    # Determine action
    if delta > 0:
        side = "yes"
        shares = delta
        price = yes_ask
    else:
        side = "no"
        shares = abs(delta)
        price = no_ask

    count = max(1, round(shares * 100))
    return side, count, price


def place_order(ticker, side, count, price, dry_run=False):
    api_key_id, private_key_b64, base_url = get_credentials()

    price_cents = max(1, min(99, round(price * 100)))

    order_body = {
        "ticker": ticker,
        "action": "buy",
        "side": side.lower(),
        "count": count,
        "type": "limit",
    }

    if side.lower() == "yes":
        order_body["yes_price"] = price_cents
    else:
        order_body["no_price"] = price_cents

    print(f"\nOrder Details:")
    print(f"  {order_body['action'].upper()} {count}x {ticker} {side.upper()} @ {price_cents}¢")
    print(f"  Est. cost: ${count * price:.2f}")

    if dry_run:
        print(f"  [DRY-RUN] Order not submitted")
        return None

    path = "/trade-api/v2/portfolio/orders"
    headers = sign_kalshi_request("POST", path, api_key_id, private_key_b64)

    response = requests.post(base_url + path, headers=headers, json=order_body, timeout=30)
    response.raise_for_status()

    result = response.json()
    order = result.get("order", {})
    order_id = order.get("order_id", "unknown")

    print(f"  ✓ Order placed: {order_id}")
    return order_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, required=True, help="Market ticker")
    parser.add_argument("--p-yes", type=float, required=True, help="YES probability (0-1)")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode")
    args = parser.parse_args()

    ticker = args.ticker
    p_yes = args.p_yes

    print("\n" + "="*60)
    print("REBALANCING TRADE TEST")
    print("="*60)
    print(f"Mode: {'DRY-RUN' if args.dry_run else 'LIVE'}")
    print(f"Ticker: {ticker}")
    print(f"="*60)

    # Get market data
    market = get_market(ticker)
    title = market.get("title", "Unknown")
    yes_ask = market.get("yes_ask", 50) / 100.0
    no_ask = market.get("no_ask", 50) / 100.0

    print(f"\nMarket: {title}")
    print(f"  YES ask: {yes_ask:.2f} (${yes_ask*100:.0f}¢)")
    print(f"  NO ask: {no_ask:.2f} (${no_ask*100:.0f}¢)")

    # Get current position
    current_side, current_qty = get_position(ticker)
    print(f"\nCurrent Position on Kalshi:")
    if current_qty > 0:
        print(f"  {current_qty} {current_side.upper()}")
    else:
        print(f"  None")

    # Get cash
    cash = get_cash()
    print(f"\nCash Available: ${cash:.2f}")

    # Calculate order
    print(f"\nPrediction (p_yes): {p_yes:.2%}")
    side, count, price = calculate_rebalancing_order(p_yes, yes_ask, no_ask, current_side, current_qty)

    if not side:
        print("\n✓ No trade needed - already at target position")
        return

    # Place order
    order_id = place_order(ticker, side, count, price, dry_run=args.dry_run)

    if not args.dry_run and order_id:
        # Wait a bit for settlement
        import time
        time.sleep(2)

        # Check new position
        new_side, new_qty = get_position(ticker)
        print(f"\nNew Position on Kalshi:")
        if new_qty > 0:
            print(f"  {new_qty} {new_side.upper()}")
        else:
            print(f"  None")

    print("\n✅ Test complete!")


if __name__ == "__main__":
    main()
