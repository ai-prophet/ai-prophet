"""Seed a resolved test market with predictions for calibration testing.

Usage:
    python services/seed_test_calibration.py [--instance INSTANCE_NAME] [--remove]

Inserts one resolved market (last_price=1.0, expired yesterday) and a handful
of BettingPrediction rows from two fake model sources covering a spread of
predicted probabilities.  Run with --remove to delete the seed rows.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

# Make sure the repo packages are importable when run from the project root.
sys.path.insert(0, "packages/core")
sys.path.insert(0, "services")

from ai_prophet_core.betting.db import create_db_engine, get_session
from ai_prophet_core.betting.db_schema import BettingPrediction
from db_models import TradingMarket, TradingPosition

TEST_MARKET_ID = "__test_calibration_market__"
INSTANCE_NAME_DEFAULT = "Haifeng"

# Predictions: (source, p_yes, yes_ask, no_ask)
# Market resolved YES (1.0).  Good model is mostly high-confidence YES;
# bad model is scattered.  This gives visible calibration curves + Brier scores.
SEED_PREDICTIONS = [
    # Good model — well-calibrated, high confidence on a YES market
    ("claude-sonnet-4-5:calibration-test", 0.82, 0.78, 0.24),
    ("claude-sonnet-4-5:calibration-test", 0.76, 0.72, 0.30),
    ("claude-sonnet-4-5:calibration-test", 0.88, 0.84, 0.18),
    # Overconfident model — very high p_yes but market was more uncertain
    ("gpt-4o:calibration-test", 0.95, 0.78, 0.24),
    ("gpt-4o:calibration-test", 0.91, 0.72, 0.30),
    ("gpt-4o:calibration-test", 0.97, 0.84, 0.18),
]


def seed(instance_name: str) -> None:
    engine = create_db_engine()
    with get_session(engine) as session:
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)

        # Upsert the resolved market
        existing = (
            session.query(TradingMarket)
            .filter_by(instance_name=instance_name, market_id=TEST_MARKET_ID)
            .first()
        )
        if existing:
            existing.last_price = 1.0
            existing.expiration = yesterday
            existing.updated_at = now
            print(f"Updated existing test market for instance '{instance_name}'.")
        else:
            session.add(
                TradingMarket(
                    instance_name=instance_name,
                    market_id=TEST_MARKET_ID,
                    ticker="TEST-CALIB-YES",
                    event_ticker="TEST-CALIB",
                    title="[TEST] Will this calibration seed market resolve YES?",
                    category="test",
                    expiration=yesterday,
                    last_price=1.0,        # resolved YES
                    yes_bid=0.99,
                    yes_ask=1.0,
                    no_bid=0.0,
                    no_ask=0.01,
                    volume_24h=0.0,
                    updated_at=now,
                )
            )
            print(f"Inserted test market for instance '{instance_name}'.")

        # Insert predictions (skip duplicates via unique constraint)
        inserted = 0
        for i, (source, p_yes, yes_ask, no_ask) in enumerate(SEED_PREDICTIONS):
            tick_ts = now - timedelta(hours=len(SEED_PREDICTIONS) - i)
            pred = BettingPrediction(
                instance_name=instance_name,
                tick_ts=tick_ts,
                market_id=TEST_MARKET_ID,
                source=source,
                p_yes=p_yes,
                yes_ask=yes_ask,
                no_ask=no_ask,
                created_at=tick_ts,
            )
            try:
                session.add(pred)
                session.flush()
                inserted += 1
            except Exception:
                session.rollback()

        print(f"Inserted {inserted} predictions.")

        # Upsert a YES position — we bought 10 contracts at 80¢, market resolved YES
        # realized_pnl = 10 * (1.0 - 0.80) = $2.00
        existing_pos = (
            session.query(TradingPosition)
            .filter_by(instance_name=instance_name, market_id=TEST_MARKET_ID)
            .first()
        )
        if existing_pos:
            print("Test position already exists, skipping.")
        else:
            session.add(
                TradingPosition(
                    instance_name=instance_name,
                    market_id=TEST_MARKET_ID,
                    contract="yes",
                    quantity=10,
                    avg_price=0.80,
                    realized_pnl=2.00,
                    unrealized_pnl=0.0,
                    max_position=10,
                    realized_trades=1,
                    updated_at=now,
                )
            )
            print("Inserted test position (10 YES @ 80¢, realized P&L $2.00).")
    print("Done. Refresh the dashboard to see resolved market data.")


def remove(instance_name: str) -> None:
    engine = create_db_engine()
    with get_session(engine) as session:
        n_preds = (
            session.query(BettingPrediction)
            .filter_by(instance_name=instance_name, market_id=TEST_MARKET_ID)
            .delete()
        )
        n_positions = (
            session.query(TradingPosition)
            .filter_by(instance_name=instance_name, market_id=TEST_MARKET_ID)
            .delete()
        )
        n_markets = (
            session.query(TradingMarket)
            .filter_by(instance_name=instance_name, market_id=TEST_MARKET_ID)
            .delete()
        )
        print(f"Removed {n_markets} market(s), {n_positions} position(s), and {n_preds} prediction(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--instance", default=INSTANCE_NAME_DEFAULT,
        help=f"Instance name (default: {INSTANCE_NAME_DEFAULT})",
    )
    parser.add_argument(
        "--remove", action="store_true",
        help="Delete the seed rows instead of inserting them",
    )
    args = parser.parse_args()

    if args.remove:
        remove(args.instance)
    else:
        seed(args.instance)
