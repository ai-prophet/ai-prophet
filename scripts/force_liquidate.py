#!/usr/bin/env python3
"""Force liquidate all positions using market orders at ask price (instant fill)."""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from instance_config import env_suffix

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


def get_credentials(instance_name):
    suffix = env_suffix(instance_name)

    if instance_name == "Haifeng":
        api_key_id = os.getenv("KALSHI_API_KEY_ID")
        private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64")
    else:
        api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{suffix}")
        private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}")

    base_url = os.getenv("KALSHI_BASE_URL") or "https://api.elections.kalshi.com"

    return api_key_id, private_key_b64, base_url


def liquidate_all(instance_name):
    api_key_id, private_key_b64, base_url = get_credentials(instance_name)

    # Get positions
    path = "/trade-api/v2/portfolio/positions"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()
    positions = response.json().get("market_positions", [])

    print(f"\n{'='*60}")
    print(f"{instance_name}: Found {len(positions)} market positions")
    print(f"{'='*60}\n")

    success = 0
    failed = 0

    for pos in positions:
        ticker = pos.get("ticker")
        position_fp = float(pos.get("position_fp", 0))

        if position_fp == 0:
            print(f"[{instance_name}] ✓ {ticker}: Already flat")
            continue

        # Determine side
        if position_fp > 0:
            side = "yes"
            count = int(abs(position_fp))
        else:
            side = "no"
            count = int(abs(position_fp))

        # Get market data
        market_path = f"/trade-api/v2/markets/{ticker}"
        market_headers = sign_kalshi_request("GET", market_path, api_key_id, private_key_b64)

        try:
            market_resp = requests.get(base_url + market_path, headers=market_headers, timeout=10)
            market_resp.raise_for_status()
            market = market_resp.json().get("market", {})

            status = market.get("status", "unknown")
            can_close_early = market.get("can_close_early", False)

            # Try to sell at 1¢ (market maker will fill if market is active)
            sell_price = 1  # 1 cent

            order_body = {
                "ticker": ticker,
                "action": "sell",
                "side": side.lower(),
                "count": count,
                "type": "limit",
            }

            if side.lower() == "yes":
                order_body["yes_price"] = sell_price
            else:
                order_body["no_price"] = sell_price

            order_path = "/trade-api/v2/portfolio/orders"
            order_headers = sign_kalshi_request("POST", order_path, api_key_id, private_key_b64)

            print(f"[{instance_name}] Selling {count}x {ticker} {side.upper()} @ {sell_price}¢ (status={status})")

            order_resp = requests.post(base_url + order_path, headers=order_headers, json=order_body, timeout=30)

            if order_resp.status_code == 200:
                result = order_resp.json()
                order_id = result.get("order", {}).get("order_id", "unknown")
                print(f"[{instance_name}] ✓ SOLD {ticker} {side.upper()} - Order ID: {order_id}")
                success += 1
            else:
                print(f"[{instance_name}] ✗ FAILED {ticker}: {order_resp.status_code} - {order_resp.text[:200]}")
                failed += 1

            time.sleep(0.3)

        except Exception as e:
            print(f"[{instance_name}] ✗ ERROR {ticker}: {e}")
            failed += 1

    print(f"\n[{instance_name}] Complete: {success} liquidated, {failed} failed\n")


print("FORCE LIQUIDATING ALL POSITIONS")
print("================================\n")

liquidate_all("Haifeng")
liquidate_all("Jibang")

print("\n✓ Done!")
