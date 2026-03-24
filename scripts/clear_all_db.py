#!/usr/bin/env python3
"""Clear ALL data from ALL tables in the database."""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

db_url = os.getenv("DATABASE_URL")
if not db_url:
    print("ERROR: DATABASE_URL not set")
    sys.exit(1)

engine = create_engine(db_url)

# List of all tables in dependency order (children first)
tables = [
    "betting_orders",
    "betting_signals",
    "betting_predictions",
    "trading_positions",
    "trading_markets",
    "model_runs",
    "system_logs",
]

print("=" * 60)
print("CLEARING ALL DATABASE TABLES")
print("=" * 60)

with engine.connect() as conn:
    for table in tables:
        try:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()

            if count > 0:
                conn.execute(text(f"DELETE FROM {table}"))
                conn.commit()
                print(f"✓ Deleted {count:,} rows from {table}")
            else:
                print(f"  {table}: already empty")
        except Exception as e:
            print(f"✗ Error clearing {table}: {e}")

print("\n" + "=" * 60)
print("DATABASE COMPLETELY CLEARED")
print("=" * 60)

# Verify all tables are empty
print("\nVerification:")
with engine.connect() as conn:
    for table in tables:
        try:
            result = conn.execute(text(f"SELECT COUNT(*) FROM {table}"))
            count = result.scalar()
            status = "✓" if count == 0 else "✗"
            print(f"{status} {table}: {count} rows")
        except Exception as e:
            print(f"✗ {table}: Error - {e}")

print("\n✅ DONE - Fresh database ready!")
