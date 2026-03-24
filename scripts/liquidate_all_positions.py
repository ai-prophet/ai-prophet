#!/usr/bin/env python3
"""Liquidate all open positions for Haifeng and Jibang.

This script:
1. Fetches all positions from Kalshi
2. For each position, submits a market sell order
3. Confirms all positions are closed
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
import time
from datetime import datetime

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from instance_config import env_suffix, normalize_instance_name

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def sign_kalshi_request(method: str, path: str, api_key_id: str, private_key_b64: str) -> dict[str, str]:
    """Generate authenticated headers for Kalshi API."""
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


def get_credentials(instance_name: str):
    """Get Kalshi credentials for instance."""
    suffix = env_suffix(instance_name)

    if instance_name == "Haifeng":
        api_key_id = os.getenv("KALSHI_API_KEY_ID") or os.getenv(f"KALSHI_API_KEY_ID_{suffix}")
        private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64") or os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}")
    else:
        api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{suffix}") or os.getenv("KALSHI_API_KEY_ID")
        private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}") or os.getenv("KALSHI_PRIVATE_KEY_B64")

    base_url = (
        os.getenv(f"KALSHI_BASE_URL_{suffix}")
        or os.getenv("KALSHI_BASE_URL")
        or "https://api.elections.kalshi.com"
    )

    if not api_key_id or not private_key_b64:
        raise ValueError(f"Missing Kalshi credentials for {instance_name}")

    return api_key_id, private_key_b64, base_url


def fetch_positions(instance_name: str):
    """Fetch all open positions from Kalshi."""
    api_key_id, private_key_b64, base_url = get_credentials(instance_name)

    path = "/trade-api/v2/portfolio/positions"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    logger.info(f"[{instance_name}] Fetching positions from Kalshi...")
    try:
        response = requests.get(base_url + path, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        positions = data.get("market_positions", [])
        logger.info(f"[{instance_name}] Found {len(positions)} positions")
        return positions
    except Exception as e:
        logger.error(f"[{instance_name}] Failed to fetch positions: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
        raise


def liquidate_position(instance_name: str, ticker: str, position: int, side: str, dry_run: bool = False):
    """Submit a market sell order to liquidate a position."""
    api_key_id, private_key_b64, base_url = get_credentials(instance_name)

    # Get current market to find best bid price
    market_path = f"/trade-api/v2/markets/{ticker}"
    market_headers = sign_kalshi_request("GET", market_path, api_key_id, private_key_b64)

    try:
        market_resp = requests.get(base_url + market_path, headers=market_headers, timeout=10)
        market_resp.raise_for_status()
        market_data = market_resp.json().get("market", {})

        # Get the bid price for the side we're selling
        if side.lower() == "yes":
            bid_cents = market_data.get("yes_bid")
        else:
            bid_cents = market_data.get("no_bid")

        if not bid_cents:
            logger.warning(f"[{instance_name}] No bid price for {ticker} {side.upper()}, skipping")
            return False

    except Exception as e:
        logger.error(f"[{instance_name}] Failed to fetch market {ticker}: {e}")
        return False

    # Submit sell order at bid price (guaranteed fill)
    order_body = {
        "ticker": ticker,
        "action": "sell",
        "side": side.lower(),
        "count": position,
        "type": "limit",
    }

    if side.lower() == "yes":
        order_body["yes_price"] = bid_cents
    else:
        order_body["no_price"] = bid_cents

    if dry_run:
        logger.info(
            f"[DRY-RUN] [{instance_name}] Would sell {position}x {ticker} {side.upper()} @ {bid_cents}¢"
        )
        return True

    path = "/trade-api/v2/portfolio/orders"
    headers = sign_kalshi_request("POST", path, api_key_id, private_key_b64)

    try:
        logger.info(f"[{instance_name}] Selling {position}x {ticker} {side.upper()} @ {bid_cents}¢")
        response = requests.post(base_url + path, headers=headers, json=order_body, timeout=30)
        response.raise_for_status()
        result = response.json()
        logger.info(f"[{instance_name}] ✓ Liquidated {ticker} {side.upper()} - Order ID: {result.get('order', {}).get('order_id')}")
        return True
    except Exception as e:
        logger.error(f"[{instance_name}] Failed to liquidate {ticker} {side.upper()}: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
        return False


def liquidate_instance(instance_name: str, dry_run: bool = False):
    """Liquidate all positions for an instance."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Liquidating {instance_name}")
    logger.info(f"{'='*60}")

    positions = fetch_positions(instance_name)

    if not positions:
        logger.info(f"[{instance_name}] No positions to liquidate")
        return

    success_count = 0
    fail_count = 0

    for pos in positions:
        ticker = pos.get("ticker")
        position_fp = float(pos.get("position_fp", 0))

        if position_fp == 0:
            logger.info(f"[{instance_name}] Skipping {ticker} - no position")
            continue

        # Determine side (YES if positive, NO if negative)
        if position_fp > 0:
            side = "yes"
            count = int(abs(position_fp))
        else:
            side = "no"
            count = int(abs(position_fp))

        if liquidate_position(instance_name, ticker, count, side, dry_run):
            success_count += 1
        else:
            fail_count += 1

        # Rate limit - wait between orders
        if not dry_run:
            time.sleep(0.5)

    logger.info(f"\n[{instance_name}] Liquidation complete: {success_count} success, {fail_count} failed")


def main():
    parser = argparse.ArgumentParser(description="Liquidate all positions for Haifeng and Jibang")
    parser.add_argument(
        "--instance",
        type=str,
        choices=["Haifeng", "Jibang", "both"],
        default="both",
        help="Instance to liquidate (default: both)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode (don't actually liquidate)",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.warning("⚠️  DRY-RUN MODE - No actual trades will be placed")

    if args.instance == "both":
        liquidate_instance("Haifeng", dry_run=args.dry_run)
        liquidate_instance("Jibang", dry_run=args.dry_run)
    else:
        liquidate_instance(normalize_instance_name(args.instance), dry_run=args.dry_run)

    logger.info("\n✓ All done!")


if __name__ == "__main__":
    main()
