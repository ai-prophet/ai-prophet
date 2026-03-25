"""Run database migrations — creates all tables and adds missing columns.

Usage:
    python services/migrate.py

Reads DATABASE_URL from environment or .env file.
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from sqlalchemy import inspect, text

# Add project root to path so packages are importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

load_dotenv()

from ai_prophet_core.betting.db import create_db_engine
from ai_prophet_core.betting.db_schema import Base as CoreBase

# Import service-specific models so they register with the Base metadata
from db_models import (  # noqa: F401
    MarketPriceSnapshot,
    ModelRun,
    SystemLog,
    TradingMarket,
    TradingPosition,
)

# Column additions for existing tables: (table, column, SQL type, default)
COLUMN_MIGRATIONS = [
    ("betting_predictions", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("betting_signals", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("betting_orders", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("trading_markets", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("trading_positions", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("model_runs", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("system_logs", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("market_price_snapshots", "instance_name", "VARCHAR(64)", "'Haifeng'"),
    ("trading_positions", "max_position", "DOUBLE PRECISION", "0"),
    ("trading_positions", "realized_trades", "INTEGER", "0"),
    ("betting_orders", "fee_paid", "DOUBLE PRECISION", "0"),
]


def add_missing_columns(engine) -> None:
    """Add columns that exist in models but not yet in the database."""
    inspector = inspect(engine)
    for table, column, sql_type, default in COLUMN_MIGRATIONS:
        if not inspector.has_table(table):
            continue
        existing = {c["name"] for c in inspector.get_columns(table)}
        if column not in existing:
            stmt = f"ALTER TABLE {table} ADD COLUMN {column} {sql_type} NOT NULL DEFAULT {default}"
            with engine.begin() as conn:
                conn.execute(text(stmt))
            print(f"  Added column {table}.{column}")


def apply_index_migrations(engine) -> None:
    """Create missing indexes that were added after initial deployment."""
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_betting_order_instance_created ON betting_orders(instance_name, created_at)",
        "CREATE INDEX IF NOT EXISTS ix_trading_market_instance_updated ON trading_markets(instance_name, updated_at)",
        "CREATE INDEX IF NOT EXISTS ix_model_run_instance_market_ts ON model_runs(instance_name, market_id, timestamp DESC)",
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
            print(f"  Applied: {stmt[:80]}...")


def apply_constraint_migrations(engine) -> None:
    """Adjust uniqueness/indexing to be per-instance instead of global."""
    statements = [
        "ALTER TABLE betting_predictions DROP CONSTRAINT IF EXISTS uq_betting_prediction",
        "ALTER TABLE trading_markets DROP CONSTRAINT IF EXISTS trading_markets_market_id_key",
        "ALTER TABLE trading_positions DROP CONSTRAINT IF EXISTS trading_positions_market_id_key",
        (
            "ALTER TABLE betting_predictions "
            "ADD CONSTRAINT uq_betting_prediction "
            "UNIQUE (instance_name, source, tick_ts, market_id)"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_trading_market_instance_market "
            "ON trading_markets(instance_name, market_id)"
        ),
        (
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_trading_position_instance_market "
            "ON trading_positions(instance_name, market_id)"
        ),
    ]
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))


def main() -> None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set. Set it in .env or environment.")
        sys.exit(1)

    print("Connecting to database...")
    engine = create_db_engine(database_url)

    print("Creating tables...")
    CoreBase.metadata.create_all(engine, checkfirst=True)

    print("Adding missing columns...")
    add_missing_columns(engine)

    print("Applying constraint migrations...")
    apply_constraint_migrations(engine)

    print("Applying index migrations...")
    apply_index_migrations(engine)

    print("Done.")
    engine.dispose()


if __name__ == "__main__":
    main()
