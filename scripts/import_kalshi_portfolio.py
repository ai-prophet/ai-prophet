#!/usr/bin/env python3
"""
Import Kalshi portfolio and fills into the database.

This script fetches your actual Kalshi positions and fills history,
then creates the corresponding database records for orders and positions.

Usage:
    python scripts/import_kalshi_portfolio.py --instance Haifeng
    python scripts/import_kalshi_portfolio.py --instance Jibang --dry-run
"""

import argparse
import os
import sys
import json
from datetime import datetime, timezone
from uuid import uuid4

# Add services to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'services'))

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


def get_kalshi_headers(instance_name: str, method: str, path: str):
    """Generate Kalshi API headers with signature."""
    import base64
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    suffix = instance_name.upper()
    api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{suffix}") or os.getenv("KALSHI_API_KEY_ID")
    private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}") or os.getenv("KALSHI_PRIVATE_KEY_B64")

    if not api_key_id or not private_key_b64:
        raise ValueError(f"Missing Kalshi credentials for {instance_name}")

    # Load private key
    key_bytes = base64.b64decode(private_key_b64)
    private_key = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())

    # Generate signature
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


def fetch_kalshi_positions(instance_name: str):
    """Fetch current positions from Kalshi."""
    base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
    path = "/trade-api/v2/portfolio/positions"
    headers = get_kalshi_headers(instance_name, "GET", path)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data.get("positions", [])


def fetch_kalshi_fills(instance_name: str, limit=100):
    """Fetch recent fill history from Kalshi."""
    base_url = os.getenv("KALSHI_BASE_URL", "https://api.elections.kalshi.com")
    path = f"/trade-api/v2/portfolio/fills?limit={limit}"
    headers = get_kalshi_headers(instance_name, "GET", path)

    response = requests.get(base_url + path, headers=headers, timeout=30)
    response.raise_for_status()

    data = response.json()
    return data.get("fills", [])


def import_portfolio(instance_name: str, dry_run: bool = False):
    """Import Kalshi portfolio into database."""
    print(f"Importing portfolio for {instance_name}...")

    # Fetch data from Kalshi
    print("\n1. Fetching positions from Kalshi...")
    positions = fetch_kalshi_positions(instance_name)
    print(f"   Found {len(positions)} positions")

    print("\n2. Fetching fills from Kalshi...")
    fills = fetch_kalshi_fills(instance_name, limit=100)
    print(f"   Found {len(fills)} fills")

    if dry_run:
        print("\n🔍 DRY RUN - Showing what would be imported:\n")
        print("Positions:")
        for pos in positions:
            ticker = pos.get("ticker")
            position = pos.get("position", 0)
            if position != 0:
                side = "YES" if position > 0 else "NO"
                qty = abs(position)
                total_cost = pos.get("total_cost", 0) / 100.0
                avg_price = total_cost / qty if qty > 0 else 0
                print(f"  {ticker}: {side} {qty} @ ${avg_price:.2f}")

        print(f"\nFills (showing last 10):")
        for fill in fills[:10]:
            ticker = fill.get("ticker")
            side = fill.get("side", "").upper()
            action = fill.get("action", "").upper()
            count = fill.get("count", 0)
            price = fill.get("yes_price" if side == "yes" else "no_price", 0) / 100.0
            created = fill.get("created_time", "")
            print(f"  {created[:19]} | {ticker} | {action} {side.upper()} {count} @ ${price:.2f}")

        return

    # Connect to database
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL not set")

    engine = create_engine(database_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        now = datetime.now(timezone.utc)

        # Import fills as betting_orders
        print("\n3. Importing fills as orders...")
        imported_orders = 0

        for fill in fills:
            ticker = fill.get("ticker")
            side = fill.get("side", "").lower()  # yes or no
            action = fill.get("action", "").lower()  # buy or sell
            count = fill.get("count", 0)
            yes_price = fill.get("yes_price", 0) / 100.0
            no_price = fill.get("no_price", 0) / 100.0
            price = yes_price if side == "yes" else no_price
            created_time = fill.get("created_time")
            trade_id = fill.get("trade_id", str(uuid4()))

            if not ticker or count == 0:
                continue

            # Check if order already exists
            existing = session.execute(
                text("SELECT id FROM betting_orders WHERE exchange_order_id = :oid AND instance_name = :inst"),
                {"oid": trade_id, "inst": instance_name}
            ).fetchone()

            if existing:
                continue  # Skip duplicates

            # Insert order
            session.execute(text("""
                INSERT INTO betting_orders (
                    instance_name, ticker, action, side, count, price_cents,
                    status, filled_shares, fill_price, exchange_order_id,
                    dry_run, created_at, order_id, signal_id
                ) VALUES (
                    :instance, :ticker, :action, :side, :count, :price_cents,
                    'FILLED', :filled, :fill_price, :exchange_id,
                    false, :created, :order_id, NULL
                )
            """), {
                "instance": instance_name,
                "ticker": ticker,
                "action": action,
                "side": side,
                "count": count,
                "price_cents": int(price * 100),
                "filled": count,
                "fill_price": price,
                "exchange_id": trade_id,
                "created": created_time or now,
                "order_id": str(uuid4()),
            })

            imported_orders += 1

        print(f"   Imported {imported_orders} new orders")

        # Import positions
        print("\n4. Importing positions...")
        imported_positions = 0

        for pos in positions:
            ticker = pos.get("ticker")
            position = pos.get("position", 0)

            if position == 0:
                continue

            market_id = f"kalshi:{ticker}"
            side = "yes" if position > 0 else "no"
            qty = abs(position)
            total_cost = pos.get("total_cost", 0) / 100.0
            avg_price = total_cost / qty if qty > 0 else 0

            # Check if position exists
            existing = session.execute(
                text("SELECT id FROM trading_positions WHERE market_id = :mid AND instance_name = :inst"),
                {"mid": market_id, "inst": instance_name}
            ).fetchone()

            if existing:
                # Update existing
                session.execute(text("""
                    UPDATE trading_positions
                    SET contract = :side, quantity = :qty, avg_price = :avg,
                        unrealized_pnl = 0, updated_at = :now
                    WHERE id = :id
                """), {
                    "side": side,
                    "qty": qty,
                    "avg": avg_price,
                    "now": now,
                    "id": existing[0]
                })
            else:
                # Insert new
                session.execute(text("""
                    INSERT INTO trading_positions (
                        instance_name, market_id, contract, quantity, avg_price,
                        realized_pnl, unrealized_pnl, max_position, realized_trades, updated_at
                    ) VALUES (
                        :instance, :market_id, :side, :qty, :avg,
                        0, 0, :qty, 0, :now
                    )
                """), {
                    "instance": instance_name,
                    "market_id": market_id,
                    "side": side,
                    "qty": qty,
                    "avg": avg_price,
                    "now": now
                })

                # Also create trading_market entry
                session.execute(text("""
                    INSERT INTO trading_markets (
                        instance_name, market_id, ticker, title, category,
                        yes_bid, yes_ask, no_bid, no_ask, last_price, expiration, updated_at
                    ) VALUES (
                        :instance, :market_id, :ticker, :title, '',
                        0, 0, 0, 0, 0, :now, :now
                    )
                    ON CONFLICT (instance_name, market_id) DO NOTHING
                """), {
                    "instance": instance_name,
                    "market_id": market_id,
                    "ticker": ticker,
                    "title": f"Imported: {ticker}",
                    "now": now
                })

            imported_positions += 1

        print(f"   Imported/updated {imported_positions} positions")

        session.commit()
        print(f"\n✅ Successfully imported portfolio for {instance_name}")

    except Exception as e:
        session.rollback()
        raise e
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Import Kalshi portfolio to database")
    parser.add_argument("--instance", required=True, choices=["Haifeng", "Jibang"],
                        help="Instance name")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be imported without making changes")

    args = parser.parse_args()

    try:
        import_portfolio(args.instance, dry_run=args.dry_run)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
