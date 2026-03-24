#!/usr/bin/env python3
"""Test the full trading pipeline with real Kalshi orders.

This script will:
1. Fetch a market from Kalshi
2. Get a prediction from the model
3. Submit an order through the betting engine
4. Allow manual prediction changes to test rebalancing
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import requests
from dotenv import load_dotenv

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "core"))

load_dotenv()

# Suppress the Python 3.8 import warnings
import warnings
warnings.filterwarnings("ignore")

from instance_config import env_suffix


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

    if not api_key_id or not private_key_b64:
        raise ValueError("Missing Kalshi credentials")

    return api_key_id, private_key_b64, base_url


def fetch_active_market():
    """Fetch a single active market from Kalshi."""
    api_key_id, private_key_b64, base_url = get_credentials()

    path = "/trade-api/v2/markets?limit=1&status=active"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()

    markets = response.json().get("markets", [])
    if not markets:
        raise ValueError("No active markets found")

    market = markets[0]
    ticker = market.get("ticker")

    print(f"\n{'='*60}")
    print(f"Selected Market: {ticker}")
    print(f"Title: {market.get('title')}")
    print(f"YES bid/ask: {market.get('yes_bid')}¢ / {market.get('yes_ask')}¢")
    print(f"NO bid/ask: {market.get('no_bid')}¢ / {market.get('no_ask')}¢")
    print(f"Volume 24h: {market.get('volume_24h', 0)}")
    print(f"{'='*60}\n")

    return market


def get_kalshi_position(ticker: str):
    """Get current position on Kalshi for a ticker."""
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
            else:
                return None, 0

    return None, 0


def place_order_via_engine(ticker: str, p_yes: float, yes_ask: float, no_ask: float, dry_run: bool = False):
    """Place an order using the BettingEngine."""
    from sqlalchemy import create_engine as sql_create_engine

    # Import after path setup
    from ai_prophet_core.betting.engine import BettingEngine
    from ai_prophet_core.betting.strategy import RebalancingStrategy
    from ai_prophet_core.betting.adapters.kalshi import KalshiAdapter

    # Setup
    db_url = os.getenv("DATABASE_URL")
    api_key_id, private_key_b64, base_url = get_credentials()

    db_engine = sql_create_engine(db_url)
    kalshi_adapter = KalshiAdapter(
        api_key_id=api_key_id,
        private_key_base64=private_key_b64,
        base_url=base_url,
        dry_run=dry_run,
    )

    strategy = RebalancingStrategy(max_spread=1.05, min_trade=0.005)

    betting_engine = BettingEngine(
        db_engine=db_engine,
        exchange_adapter=kalshi_adapter,
        strategy=strategy,
        instance_name="TEST",
    )

    # Get current position for portfolio snapshot
    side, qty = get_kalshi_position(ticker)

    from ai_prophet_core.betting.strategy import PortfolioSnapshot

    # Get cash from Kalshi
    path = "/trade-api/v2/portfolio/balance"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)
    balance_resp = requests.get(base_url + path, headers=headers, timeout=10)
    balance_resp.raise_for_status()
    cash = balance_resp.json().get("balance", 0) / 100.0

    portfolio = PortfolioSnapshot(
        cash=cash,
        total_pnl=0.0,
        position_count=1 if qty > 0 else 0,
        market_position_shares=qty if qty > 0 else 0.0,
        market_position_side=side,
    )

    strategy._portfolio = portfolio

    market_id = f"kalshi:{ticker}"

    print(f"\n{'='*60}")
    print(f"Placing Order via BettingEngine")
    print(f"{'='*60}")
    print(f"Market: {ticker}")
    print(f"Prediction (p_yes): {p_yes:.2%}")
    print(f"YES ask: {yes_ask:.2f}")
    print(f"NO ask: {no_ask:.2f}")
    print(f"Current position: {qty} {side.upper() if side else 'NONE'}")
    print(f"Cash: ${cash:.2f}")
    print(f"Mode: {'DRY-RUN' if dry_run else 'LIVE'}")
    print(f"{'='*60}\n")

    # Place bet
    result = betting_engine.evaluate_and_bet(
        market_id=market_id,
        p_yes=p_yes,
        yes_ask=yes_ask,
        no_ask=no_ask,
        source="test_script",
    )

    if result and result.order_placed:
        print(f"✓ Order placed successfully!")
        print(f"  Order ID: {result.order_id}")
        print(f"  Status: {result.status}")
        return True
    else:
        print(f"✗ Order not placed")
        if result and result.error:
            print(f"  Error: {result.error}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test trading pipeline with Kalshi")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode (no real orders)")
    parser.add_argument("--ticker", type=str, help="Specific ticker to trade (optional)")
    parser.add_argument("--p-yes", type=float, help="Manual YES probability (0-1)")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("TRADING PIPELINE TEST")
    print("="*60)

    if args.dry_run:
        print("⚠️  DRY-RUN MODE - No real orders will be placed")
    else:
        print("🔴 LIVE MODE - Real money will be used!")

    # Step 1: Get market
    if args.ticker:
        # Fetch specific ticker
        api_key_id, private_key_b64, base_url = get_credentials()
        path = f"/trade-api/v2/markets/{args.ticker}"
        headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)
        response = requests.get(base_url + path, headers=headers, timeout=30)
        response.raise_for_status()
        market = response.json().get("market", {})
    else:
        market = fetch_active_market()

    ticker = market.get("ticker")
    title = market.get("title")
    yes_ask = market.get("yes_ask", 50) / 100.0
    no_ask = market.get("no_ask", 50) / 100.0

    # Step 2: Get or use manual prediction
    if args.p_yes is not None:
        p_yes = args.p_yes
        print(f"Using manual prediction: {p_yes:.2%}")
    else:
        # For testing, use a simple prediction based on current price
        p_yes = yes_ask - 0.05  # 5% below ask to trigger a small buy
        print(f"Using test prediction: {p_yes:.2%}")

    # Step 3: Place order
    success = place_order_via_engine(ticker, p_yes, yes_ask, no_ask, dry_run=args.dry_run)

    if success:
        # Step 4: Show position on Kalshi
        print("\nChecking Kalshi position...")
        side, qty = get_kalshi_position(ticker)
        if qty > 0:
            print(f"✓ Kalshi position: {qty} {side.upper()}")
        else:
            print("  No position on Kalshi (order may be pending)")

    print("\n✅ Test complete!\n")
    print("To test rebalancing, run this script again with:")
    print(f"  python3 scripts/test_trading_pipeline.py --ticker {ticker} --p-yes <NEW_PROBABILITY>")
    print(f"\nExample (to increase position):")
    print(f"  python3 scripts/test_trading_pipeline.py --ticker {ticker} --p-yes {p_yes - 0.03:.2f}")
    print(f"\nExample (to decrease position):")
    print(f"  python3 scripts/test_trading_pipeline.py --ticker {ticker} --p-yes {p_yes + 0.03:.2f}")


if __name__ == "__main__":
    main()
