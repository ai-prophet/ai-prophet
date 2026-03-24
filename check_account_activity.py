#!/usr/bin/env python3
"""Check recent trading activity for Jibang and Haifeng accounts."""

import os
import sys
import base64
import json
from datetime import datetime, timedelta

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

def create_rsa_signature(private_key_base64: str, timestamp: str, method: str, endpoint: str) -> str:
    """Create RSA signature for Kalshi API."""
    key_bytes = base64.b64decode(private_key_base64)
    private_key = serialization.load_pem_private_key(
        key_bytes, password=None, backend=default_backend()
    )
    message = f"{timestamp}{method.upper()}{endpoint}"
    signature = private_key.sign(
        message.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")

def check_account(account_name: str):
    """Check balance, positions, and recent fills for an account."""
    # Get credentials
    if account_name == "Jibang":
        api_key_id = os.getenv("KALSHI_API_KEY_ID_JIBANG")
        private_key = os.getenv("KALSHI_PRIVATE_KEY_B64_JIBANG")
    elif account_name == "Haifeng":
        api_key_id = os.getenv("KALSHI_API_KEY_ID_HAIFENG")
        private_key = os.getenv("KALSHI_PRIVATE_KEY_B64_HAIFENG")
    else:
        print(f"Unknown account: {account_name}")
        return

    base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")

    if not api_key_id or not private_key:
        print(f"Error: {account_name}'s credentials not found in environment")
        return

    print(f"\n{'='*60}")
    print(f"  {account_name}'s Account Status")
    print(f"{'='*60}")

    session = requests.Session()
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # 1. Check balance
    endpoint = "/trade-api/v2/portfolio/balance"
    timestamp = str(int(datetime.now().timestamp() * 1000))
    signature = create_rsa_signature(private_key, timestamp, "GET", endpoint)

    headers = {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }

    try:
        response = session.get(f"{base_url}{endpoint}", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        balance = data.get("balance", 0) / 100
        print(f"\n💰 Cash Balance: ${balance:,.2f}")
    except Exception as e:
        print(f"\n❌ Error getting balance: {e}")
        return

    # 2. Check positions
    endpoint = "/trade-api/v2/portfolio/positions"
    timestamp = str(int(datetime.now().timestamp() * 1000))
    signature = create_rsa_signature(private_key, timestamp, "GET", endpoint)

    headers["KALSHI-ACCESS-SIGNATURE"] = signature
    headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp

    try:
        response = session.get(f"{base_url}{endpoint}", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        positions = data.get("positions", [])

        if positions:
            print(f"\n📊 Active Positions: {len(positions)}")
            total_value = 0
            for pos in positions:
                market_ticker = pos.get("market_ticker", "Unknown")
                position = pos.get("position", 0)
                last_price = pos.get("last_price", 0) / 100 if pos.get("last_price") else 0
                current_value = position * last_price
                total_value += current_value
                print(f"   • {market_ticker}: {position:+d} contracts @ ${last_price:.2f} = ${current_value:.2f}")
            print(f"   Total Position Value: ${total_value:,.2f}")
        else:
            print(f"\n📊 Active Positions: None")
    except Exception as e:
        print(f"\n❌ Error getting positions: {e}")

    # 3. Check recent fills (last 24 hours)
    endpoint = "/trade-api/v2/portfolio/fills"
    timestamp = str(int(datetime.now().timestamp() * 1000))
    signature = create_rsa_signature(private_key, timestamp, "GET", endpoint)

    headers["KALSHI-ACCESS-SIGNATURE"] = signature
    headers["KALSHI-ACCESS-TIMESTAMP"] = timestamp

    try:
        response = session.get(f"{base_url}{endpoint}", headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        fills = data.get("fills", [])

        # Filter for recent fills (last 24 hours)
        recent_fills = []
        cutoff_time = datetime.now() - timedelta(hours=24)

        for fill in fills[:20]:  # Check last 20 fills
            # Parse created time (assuming ISO format)
            created_time_str = fill.get("created_time", "")
            if created_time_str:
                try:
                    # Handle various timestamp formats
                    if "T" in created_time_str:
                        created_time = datetime.fromisoformat(created_time_str.replace("Z", "+00:00"))
                    else:
                        created_time = datetime.fromtimestamp(int(created_time_str) / 1000)

                    if created_time > cutoff_time:
                        recent_fills.append(fill)
                except:
                    pass

        if recent_fills:
            print(f"\n📈 Recent Trades (Last 24 hours): {len(recent_fills)}")
            for fill in recent_fills[:5]:  # Show last 5
                ticker = fill.get("ticker", "Unknown")
                side = fill.get("side", "")
                count = fill.get("count", 0)
                price = fill.get("price", 0) / 100
                created = fill.get("created_time", "")
                print(f"   • {side.upper()} {count} {ticker} @ ${price:.2f} - {created}")
        else:
            print(f"\n📈 Recent Trades (Last 24 hours): None")
    except Exception as e:
        print(f"\n❌ Error getting fills: {e}")

    session.close()

if __name__ == "__main__":
    # Check both accounts
    check_account("Jibang")
    check_account("Haifeng")