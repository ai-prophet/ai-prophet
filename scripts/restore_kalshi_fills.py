#!/usr/bin/env python3
"""Restore missing Kalshi fills/trades into the database.

Usage:
    python scripts/restore_kalshi_fills.py
    python scripts/restore_kalshi_fills.py --instance Haifeng
    python scripts/restore_kalshi_fills.py --instance Jibang
    python scripts/restore_kalshi_fills.py --dry-run
"""

from __future__ import annotations

import argparse
import base64
import logging
import os
import sys
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))

from instance_config import env_suffix, normalize_instance_name

load_dotenv()

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def sign_kalshi_request(method: str, path: str, api_key_id: str, private_key_b64: str) -> dict[str, str]:
    """Generate authenticated headers for Kalshi API."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

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


def fetch_kalshi_fills(instance_name: str, limit: int = 200):
    """Fetch fills from Kalshi portfolio API."""
    suffix = env_suffix(instance_name)
    logger.debug(f"Looking for credentials with suffix: {suffix}")

    # For Haifeng, try both suffixed and base credentials
    # For Jibang, only try suffixed
    if instance_name == "Haifeng":
        api_key_id = os.getenv("KALSHI_API_KEY_ID") or os.getenv(f"KALSHI_API_KEY_ID_{suffix}")
        private_key_b64 = os.getenv("KALSHI_PRIVATE_KEY_B64") or os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}")
    else:
        api_key_id = os.getenv(f"KALSHI_API_KEY_ID_{suffix}") or os.getenv("KALSHI_API_KEY_ID")
        private_key_b64 = os.getenv(f"KALSHI_PRIVATE_KEY_B64_{suffix}") or os.getenv("KALSHI_PRIVATE_KEY_B64")

    base_url = (
        os.getenv(f"KALSHI_BASE_URL_{suffix}")
        or os.getenv("KALSHI_BASE_URL")
        or "https://trading-api.kalshi.com"
    )

    if not api_key_id or not private_key_b64:
        raise ValueError(f"Missing Kalshi credentials for {instance_name}")

    logger.debug(f"Using API key: {api_key_id[:10]}...")
    logger.debug(f"Using base URL: {base_url}")

    path = f"/trade-api/v2/portfolio/fills?limit={limit}"
    headers = sign_kalshi_request("GET", path, api_key_id, private_key_b64)

    logger.info("Fetching fills from Kalshi API...")
    try:
        response = requests.get(base_url + path, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()
        fills = data.get("fills", [])
        logger.info(f"Fetched {len(fills)} fills from Kalshi")
        return fills
    except Exception as e:
        logger.error(f"Failed to fetch fills: {e}")
        if hasattr(e, "response") and hasattr(e.response, "text"):
            logger.error(f"Response: {e.response.text}")
        raise


def import_fills_to_db(fills: list[dict], instance_name: str, dry_run: bool = False):
    """Import Kalshi fills into the betting_orders table."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from db_models import BettingOrder

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("DATABASE_URL environment variable not set")

    engine = create_engine(db_url)
    Session = sessionmaker(bind=engine)
    session = Session()

    imported_count = 0
    skipped_count = 0

    try:
        for fill in fills:
            # Extract fill data
            fill_id = fill.get("order_id", "")
            ticker = fill.get("ticker", "")
            side = fill.get("side", "yes").lower()  # "yes" or "no"
            action = fill.get("action", "buy").lower()  # "buy" or "sell"
            count = int(fill.get("count", 0))
            yes_price = fill.get("yes_price")
            no_price = fill.get("no_price")
            created_time = fill.get("created_time", "")
            trade_id = fill.get("trade_id", "")

            # Determine fill price in dollars (Kalshi returns cents)
            if side == "yes" and yes_price is not None:
                fill_price = yes_price / 100.0
            elif side == "no" and no_price is not None:
                fill_price = no_price / 100.0
            else:
                logger.warning(f"Could not determine fill price for fill {fill_id}")
                continue

            # Check if this order already exists in the database
            existing = (
                session.query(BettingOrder)
                .filter_by(instance_name=instance_name, order_id=fill_id)
                .first()
            )

            if existing:
                skipped_count += 1
                logger.debug(f"Order {fill_id} already exists, skipping")
                continue

            # Parse created_time
            try:
                created_at = datetime.fromisoformat(created_time.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                created_at = datetime.now(timezone.utc)

            # Create new betting order
            order = BettingOrder(
                instance_name=instance_name,
                market_id=f"kalshi:{ticker}",
                ticker=ticker,
                side=side,
                action=action,
                contract=side,
                price=fill_price,
                quantity=count,
                status="FILLED",
                filled_quantity=count,
                fill_price=fill_price,
                order_id=fill_id,
                created_at=created_at,
                dry_run=False,
            )

            if dry_run:
                logger.info(
                    f"[DRY-RUN] Would import: {action.upper()} {count}x {ticker} {side.upper()} @ ${fill_price:.2f} (order_id={fill_id})"
                )
                imported_count += 1
            else:
                session.add(order)
                logger.info(
                    f"Imported: {action.upper()} {count}x {ticker} {side.upper()} @ ${fill_price:.2f} (order_id={fill_id}, trade_id={trade_id})"
                )
                imported_count += 1

        if not dry_run:
            session.commit()
            logger.info(f"✓ Successfully imported {imported_count} fills, skipped {skipped_count} existing")
        else:
            logger.info(f"[DRY-RUN] Would import {imported_count} fills, skip {skipped_count} existing")

    except Exception as e:
        session.rollback()
        logger.error(f"Failed to import fills: {e}")
        raise
    finally:
        session.close()


def main():
    parser = argparse.ArgumentParser(description="Restore Kalshi fills to database")
    parser.add_argument(
        "--instance",
        type=str,
        default="Haifeng",
        help="Instance name (default: Haifeng)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=200,
        help="Max number of fills to fetch (default: 200)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode (don't actually import to DB)",
    )
    args = parser.parse_args()

    instance_name = normalize_instance_name(args.instance)
    logger.info(f"Restoring Kalshi fills for instance: {instance_name}")

    # Fetch fills from Kalshi
    fills = fetch_kalshi_fills(instance_name, limit=args.limit)

    if not fills:
        logger.info("No fills found on Kalshi")
        return

    # Import fills to database
    import_fills_to_db(fills, instance_name, dry_run=args.dry_run)

    logger.info("Done!")


if __name__ == "__main__":
    main()
