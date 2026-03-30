"""Strategy version tracking and transition markers for clear PnL segmentation."""

from datetime import datetime, UTC
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import text
import json

# Strategy Version Definition
CURRENT_STRATEGY_VERSION = "v2.0-constrained"
STRATEGY_START_DATE = datetime.now(UTC)

STRATEGY_CONFIG = {
    "version": CURRENT_STRATEGY_VERSION,
    "name": "Constrained Trading Strategy",
    "start_date": STRATEGY_START_DATE.isoformat(),
    "constraints": {
        "pre_resolution_block_hours": 36,
        "min_hours_between_trades": 4,
        "min_market_price_deviation_cents": 10,  # Market must move 10¢ since last FORECAST (not trade)
        "spread_filter": None,  # Removed - trade ALL markets regardless of spread
    },
    "position_sizing": {
        "strategy": "rebalancing",
        "formula": "position = (model_probability - market_yes_ask)",
        "description": "Maintains position proportional to probability edge (p - q)"
    },
    "capital": {
        "starting_cash": 500,
        "comparison_workers": ["GPT5", "Grok4", "Opus46"]
    },
    "description": "Rebalancing strategy: 36hr pre-resolution block, 4hr between trades, 10¢ market movement since last forecast, NO spread filter"
}


def create_strategy_marker_table(session: Session) -> None:
    """Create the strategy markers table if it doesn't exist."""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS strategy_markers (
        id SERIAL PRIMARY KEY,
        instance_name VARCHAR(255) NOT NULL,
        strategy_version VARCHAR(100) NOT NULL,
        start_timestamp TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        end_timestamp TIMESTAMPTZ,
        config JSONB NOT NULL,
        starting_balance DECIMAL(15,2),
        ending_balance DECIMAL(15,2),
        total_pnl DECIMAL(15,2),
        is_active BOOLEAN DEFAULT TRUE,
        notes TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        CONSTRAINT unique_active_strategy UNIQUE (instance_name, is_active) WHERE is_active = TRUE
    );

    CREATE INDEX IF NOT EXISTS idx_strategy_markers_instance ON strategy_markers(instance_name);
    CREATE INDEX IF NOT EXISTS idx_strategy_markers_version ON strategy_markers(strategy_version);
    CREATE INDEX IF NOT EXISTS idx_strategy_markers_timestamp ON strategy_markers(start_timestamp);
    """
    session.execute(text(create_table_sql))
    session.commit()


def mark_strategy_transition(
    session: Session,
    instance_name: str,
    new_version: str = CURRENT_STRATEGY_VERSION,
    config: Dict[str, Any] = None,
    notes: Optional[str] = None
) -> int:
    """
    Mark a strategy transition point for clear PnL tracking.

    This will:
    1. Close the previous strategy period (if any)
    2. Calculate final PnL for the old strategy
    3. Start a new strategy period
    """

    # First, close any existing active strategy
    close_sql = """
    UPDATE strategy_markers
    SET
        is_active = FALSE,
        end_timestamp = CURRENT_TIMESTAMP,
        ending_balance = (
            SELECT balance
            FROM kalshi_balance_snapshots
            WHERE instance_name = :instance_name
            ORDER BY polled_at DESC
            LIMIT 1
        ),
        total_pnl = (
            SELECT SUM(COALESCE(realized_pnl, 0))
            FROM kalshi_position_snapshots
            WHERE instance_name = :instance_name
            AND polled_at >= strategy_markers.start_timestamp
        )
    WHERE instance_name = :instance_name
    AND is_active = TRUE
    RETURNING id;
    """

    result = session.execute(
        text(close_sql),
        {"instance_name": instance_name}
    )
    closed_ids = result.fetchall()

    # Get current balance for the new strategy period
    balance_sql = """
    SELECT balance
    FROM kalshi_balance_snapshots
    WHERE instance_name = :instance_name
    ORDER BY polled_at DESC
    LIMIT 1
    """
    balance_result = session.execute(
        text(balance_sql),
        {"instance_name": instance_name}
    ).fetchone()

    current_balance = balance_result[0] if balance_result else 500.0

    # Insert new strategy marker
    insert_sql = """
    INSERT INTO strategy_markers (
        instance_name,
        strategy_version,
        start_timestamp,
        config,
        starting_balance,
        notes,
        is_active
    ) VALUES (
        :instance_name,
        :version,
        CURRENT_TIMESTAMP,
        :config,
        :starting_balance,
        :notes,
        TRUE
    ) RETURNING id;
    """

    strategy_config = config or STRATEGY_CONFIG

    result = session.execute(
        text(insert_sql),
        {
            "instance_name": instance_name,
            "version": new_version,
            "config": json.dumps(strategy_config),
            "starting_balance": current_balance,
            "notes": notes or f"Strategy transition to {new_version}"
        }
    )

    new_id = result.fetchone()[0]
    session.commit()

    print(f"✅ Strategy transition marked for {instance_name}")
    print(f"   Previous strategy IDs closed: {[id[0] for id in closed_ids] if closed_ids else 'None'}")
    print(f"   New strategy ID: {new_id}")
    print(f"   Version: {new_version}")
    print(f"   Starting balance: ${current_balance:.2f}")

    return new_id


def get_current_strategy(session: Session, instance_name: str) -> Optional[Dict[str, Any]]:
    """Get the current active strategy for an instance."""
    sql = """
    SELECT
        id,
        strategy_version,
        start_timestamp,
        config,
        starting_balance,
        notes,
        created_at
    FROM strategy_markers
    WHERE instance_name = :instance_name
    AND is_active = TRUE
    LIMIT 1;
    """

    result = session.execute(
        text(sql),
        {"instance_name": instance_name}
    ).fetchone()

    if result:
        return {
            "id": result[0],
            "version": result[1],
            "start_timestamp": result[2],
            "config": result[3],
            "starting_balance": float(result[4]) if result[4] else None,
            "notes": result[5],
            "created_at": result[6]
        }
    return None


def get_strategy_pnl(
    session: Session,
    instance_name: str,
    strategy_id: Optional[int] = None
) -> Dict[str, Any]:
    """
    Get PnL for a specific strategy period.
    If no strategy_id provided, gets current active strategy.
    """
    if strategy_id is None:
        # Get current active strategy
        current = get_current_strategy(session, instance_name)
        if not current:
            return {"error": "No active strategy found"}
        strategy_id = current["id"]

    sql = """
    SELECT
        sm.id,
        sm.strategy_version,
        sm.start_timestamp,
        sm.end_timestamp,
        sm.starting_balance,
        sm.ending_balance,
        sm.is_active,
        COALESCE(sm.ending_balance, kbs.balance) as current_balance,
        COALESCE(sm.ending_balance, kbs.balance) - sm.starting_balance as total_pnl,
        CASE
            WHEN sm.starting_balance > 0 THEN
                ((COALESCE(sm.ending_balance, kbs.balance) - sm.starting_balance) / sm.starting_balance * 100)
            ELSE 0
        END as return_percentage,
        COUNT(DISTINCT kps.ticker) as markets_traded,
        COUNT(kps.id) as total_positions
    FROM strategy_markers sm
    LEFT JOIN LATERAL (
        SELECT balance
        FROM kalshi_balance_snapshots
        WHERE instance_name = sm.instance_name
        ORDER BY polled_at DESC
        LIMIT 1
    ) kbs ON TRUE
    LEFT JOIN kalshi_position_snapshots kps ON
        kps.instance_name = sm.instance_name
        AND kps.polled_at >= sm.start_timestamp
        AND (sm.end_timestamp IS NULL OR kps.polled_at <= sm.end_timestamp)
    WHERE sm.id = :strategy_id
    GROUP BY sm.id, sm.strategy_version, sm.start_timestamp, sm.end_timestamp,
             sm.starting_balance, sm.ending_balance, sm.is_active, kbs.balance;
    """

    result = session.execute(
        text(sql),
        {"strategy_id": strategy_id}
    ).fetchone()

    if result:
        return {
            "strategy_id": result[0],
            "version": result[1],
            "start_timestamp": result[2],
            "end_timestamp": result[3],
            "starting_balance": float(result[4]) if result[4] else 0,
            "ending_balance": float(result[5]) if result[5] else None,
            "is_active": result[6],
            "current_balance": float(result[7]) if result[7] else 0,
            "total_pnl": float(result[8]) if result[8] else 0,
            "return_percentage": float(result[9]) if result[9] else 0,
            "markets_traded": result[10],
            "total_positions": result[11]
        }

    return {"error": f"Strategy {strategy_id} not found"}


def mark_all_workers_strategy_transition(session: Session, notes: Optional[str] = None):
    """Mark strategy transition for all comparison workers at once."""
    workers = ["GPT5", "Grok4", "Opus46", "Haifeng", "Jibang"]

    print(f"\n{'='*60}")
    print(f"🔄 MARKING STRATEGY TRANSITION: {CURRENT_STRATEGY_VERSION}")
    print(f"{'='*60}")
    print(f"Timestamp: {datetime.now(UTC).isoformat()}")
    print(f"Strategy Config:")
    print(f"  • 36-hour pre-resolution block")
    print(f"  • 4-hour minimum between trades")
    print(f"  • 10-cent price deviation requirement")
    print(f"  • NO spread filter (removed)")
    print(f"  • Starting cash: $500")
    print(f"{'='*60}\n")

    for worker in workers:
        try:
            mark_strategy_transition(
                session,
                worker,
                notes=notes or f"Transition to {CURRENT_STRATEGY_VERSION} - Constrained trading with no spread filter"
            )
        except Exception as e:
            print(f"⚠️  Error marking transition for {worker}: {e}")

    print(f"\n{'='*60}")
    print(f"✅ Strategy transition complete!")
    print(f"{'='*60}\n")