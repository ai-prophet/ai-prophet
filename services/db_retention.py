"""Automated DB retention — delete old snapshot/log rows to control storage.

Designed to run on a schedule (e.g. daily cron or Cloud Scheduler).
Keeps recent data needed by the dashboard and purges the rest.

Usage:
    python services/db_retention.py                # dry-run (default)
    python services/db_retention.py --apply        # actually delete
    python services/db_retention.py --days 14      # custom retention window

Environment:
    DATABASE_URL  — PostgreSQL connection string (required)
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv()

from sqlalchemy import text

from ai_prophet_core.betting.db import create_db_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Tables to prune and their timestamp column.
# Order doesn't matter — these are independent append-only tables.
RETENTION_TARGETS = [
    ("kalshi_position_snapshots", "snapshot_ts"),
    ("kalshi_order_snapshots",    "captured_at"),
    ("kalshi_balance_snapshots",  "snapshot_ts"),
    ("market_price_snapshots",    "timestamp"),
    ("model_runs",                "timestamp"),
    ("betting_predictions",       "created_at"),
]

# Heartbeat logs are only useful for recent operational status.
# Keep 7 days regardless of the main retention window.
HEARTBEAT_RETENTION_DAYS = 7


def _table_size_mb(conn, table: str) -> float | None:
    """Return table + index size in MB (PostgreSQL only)."""
    try:
        result = conn.execute(
            text("SELECT pg_total_relation_size(:t) / (1024.0 * 1024.0)"),
            {"t": table},
        ).scalar()
        return round(result, 1) if result else None
    except Exception:
        return None


def run_retention(days: int, apply: bool) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    engine = create_db_engine()

    logger.info("Retention policy: delete rows older than %d days (before %s)", days, cutoff.isoformat())
    if not apply:
        logger.info("DRY RUN — pass --apply to actually delete rows")

    total_deleted = 0

    with engine.connect() as conn:
        for table, ts_col in RETENTION_TARGETS:
            # Count rows to delete
            try:
                count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE {ts_col} < :cutoff"),
                    {"cutoff": cutoff},
                ).scalar()
            except Exception as e:
                logger.warning("  %s: skipped (%s)", table, e)
                conn.rollback()
                continue

            size_mb = _table_size_mb(conn, table)
            size_str = f" ({size_mb} MB)" if size_mb else ""

            if count == 0:
                logger.info("  %s: 0 rows to delete%s", table, size_str)
                continue

            if not apply:
                logger.info("  %s: %d rows would be deleted%s", table, count, size_str)
                continue

            # Delete in batches to avoid long locks and WAL bloat
            deleted = 0
            batch_size = 10_000
            while True:
                result = conn.execute(
                    text(f"""
                        DELETE FROM {table}
                        WHERE id IN (
                            SELECT id FROM {table}
                            WHERE {ts_col} < :cutoff
                            LIMIT :batch
                        )
                    """),
                    {"cutoff": cutoff, "batch": batch_size},
                )
                conn.commit()
                batch_deleted = result.rowcount
                deleted += batch_deleted
                if batch_deleted < batch_size:
                    break

            total_deleted += deleted
            new_size = _table_size_mb(conn, table)
            new_size_str = f" ({new_size} MB)" if new_size else ""
            logger.info("  %s: deleted %d rows%s → %s", table, deleted, size_str, new_size_str)

    # Prune heartbeat logs more aggressively (7 days)
    hb_cutoff = datetime.now(timezone.utc) - timedelta(days=HEARTBEAT_RETENTION_DAYS)
    with engine.connect() as conn:
        try:
            count = conn.execute(
                text("SELECT COUNT(*) FROM system_logs WHERE level = 'HEARTBEAT' AND created_at < :cutoff"),
                {"cutoff": hb_cutoff},
            ).scalar()
        except Exception:
            count = 0
            conn.rollback()
        if count and count > 0:
            if not apply:
                logger.info("  system_logs (HEARTBEAT): %d rows older than %d days would be deleted", count, HEARTBEAT_RETENTION_DAYS)
            else:
                deleted = 0
                while True:
                    result = conn.execute(
                        text("""
                            DELETE FROM system_logs
                            WHERE id IN (
                                SELECT id FROM system_logs
                                WHERE level = 'HEARTBEAT' AND created_at < :cutoff
                                LIMIT :batch
                            )
                        """),
                        {"cutoff": hb_cutoff, "batch": 10_000},
                    )
                    conn.commit()
                    batch_deleted = result.rowcount
                    deleted += batch_deleted
                    if batch_deleted < 10_000:
                        break
                total_deleted += deleted
                logger.info("  system_logs (HEARTBEAT): deleted %d rows", deleted)

    # Null out raw_json on remaining rows — the structured columns have the data
    RAW_JSON_TABLES = [
        "kalshi_position_snapshots",
        "kalshi_order_snapshots",
        "kalshi_balance_snapshots",
    ]
    with engine.connect() as conn:
        for table in RAW_JSON_TABLES:
            try:
                count = conn.execute(
                    text(f"SELECT COUNT(*) FROM {table} WHERE raw_json IS NOT NULL"),
                ).scalar()
            except Exception:
                conn.rollback()
                continue
            if count == 0:
                continue
            if not apply:
                logger.info("  %s: %d rows with raw_json to null out", table, count)
                continue
            # Batch update to avoid long locks
            updated = 0
            while True:
                result = conn.execute(
                    text(f"""
                        UPDATE {table} SET raw_json = NULL
                        WHERE id IN (
                            SELECT id FROM {table}
                            WHERE raw_json IS NOT NULL
                            LIMIT :batch
                        )
                    """),
                    {"batch": 10_000},
                )
                conn.commit()
                batch_updated = result.rowcount
                updated += batch_updated
                if batch_updated < 10_000:
                    break
            total_deleted += updated
            logger.info("  %s: nulled raw_json on %d rows", table, updated)

    if apply and total_deleted > 0:
        logger.info("Total modified: %d rows. Run VACUUM FULL on Supabase dashboard to reclaim disk.", total_deleted)
    elif apply:
        logger.info("Nothing to delete.")

    engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="DB retention cleanup")
    parser.add_argument("--days", type=int, default=30, help="Keep rows from the last N days (default: 30)")
    parser.add_argument("--apply", action="store_true", help="Actually delete (default is dry-run)")
    args = parser.parse_args()

    run_retention(days=args.days, apply=args.apply)


if __name__ == "__main__":
    main()
