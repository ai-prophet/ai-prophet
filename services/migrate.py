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
    ("trading_positions", "max_position", "DOUBLE PRECISION", "0"),
    ("trading_positions", "realized_trades", "INTEGER", "0"),
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

    print("Done.")
    engine.dispose()


if __name__ == "__main__":
    main()
