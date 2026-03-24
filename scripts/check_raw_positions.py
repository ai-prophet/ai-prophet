#!/usr/bin/env python3
"""Check raw Kalshi positions response."""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from instance_config import env_suffix

load_dotenv()


def sign_kalshi_request(method: str, path: str, api_key_id: str, private_key_b64: str):
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    key_bytes = base64.b64decode(private_key_b64)
    private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())

    timestamp_str = str(int(datetime.now().timestamp() * 1000))
    msg_string = timestamp_str + method.upper() + path

    signature = private_key.sign(
        msg_string.encode("utf-8"),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=padding.PSS.DIGEST_LENGTH,
        ),
        hashes.SHA256(),
    )

    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
        "Content-Type": "application/json",
    }


def check_instance(instance_name: str):
    suffix = env_suffix(instance_name)

    if instance_name == "Haifeng":
        api_key_id = os.getenv("KALSHI_API_KEY_ID")
        private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64")
    else:
        api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{suffix}")
        private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}")

    base_url = os.getenv("KALSHI_BASE_URL") or "https://api.elections.kalshi.com"

    print(f"\n{'='*60}")
    print(f"{instance_name} - RAW POSITIONS")
    print(f"{'='*60}")

    # Check positions
    path = "/trade-api/v2/portfolio/positions"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()
    data = response.json()

    print(json.dumps(data, indent=2))

    # Also check balance
    balance_path = "/trade-api/v2/portfolio/balance"
    balance_headers = sign_kalshi_request("GET", balance_path, api_key_id, private_key_b64)

    balance_resp = requests.get(base_url + balance_path, headers=balance_headers, timeout=10)
    balance_resp.raise_for_status()
    balance_data = balance_resp.json()

    print(f"\n{instance_name} Balance: ${balance_data.get('balance', 0) / 100:.2f}")


check_instance("Haifeng")
check_instance("Jibang")
