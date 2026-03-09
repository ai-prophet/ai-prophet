"""Optional client-side database for local event logging.

Provides local storage for pipeline events, trade history, and PnL
timeseries. Not required for the default CLI workflow (which is stateless
and relies on the server API for all state), but useful for offline
analysis, debugging, and custom integrations.

Stored data:
- Runs: metadata for each agent run
- Events: pipeline events (tick_start, forecast, action, etc.)
- Event blobs: LLM prompts/responses, search results
- Positions, fills, PnL timeseries

SQLite by default, PostgreSQL optional.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import TypedDict

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .tick_context import Position

logger = logging.getLogger(__name__)


class PortfolioSnapshot(TypedDict):
    """Local snapshot of portfolio state at a specific tick."""

    cash: Decimal
    equity: Decimal
    positions: list[Position]


class RunStatus(StrEnum):
    """Status of an agent run."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ClientDatabase:
    """Unified database for all ai_prophet data.

    Manages schema creation and provides methods for all data operations.

    Example:
        db = ClientDatabase(db_url="sqlite:///./data/ai_prophet.db")

        run_id = db.create_run(
            provider="openai",
            model_name="gpt-5.2",
            llm_config={"temperature": 0.3}
        )

        db.write_event(run_id, "tick_start", {"tick_ts": "2024-01-01T00:00:00Z"})
    """

    def __init__(self, db_url: str = "sqlite:///./data/ai_prophet.db"):
        """Initialize database connection.

        Args:
            db_url: SQLAlchemy database URL. Defaults to local SQLite.
                    For PostgreSQL: "postgresql://user:pass@host/db"
        """
        self.db_url = db_url
        self.engine: Engine = create_engine(db_url, echo=False)
        self.metadata = MetaData()

        self._define_tables()
        self._init_schema()

    def _define_tables(self):
        """Define all database tables."""

        # Runs table - one row per agent run
        self.runs = Table(
            "runs",
            self.metadata,
            Column("run_id", String(128), primary_key=True),
            Column("provider", String(64), nullable=False),
            Column("model_name", String(128), nullable=False),
            Column("llm_config", JSON, nullable=True),
            Column("status", String(32), default="PENDING"),
            Column("started_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)),
            Column("completed_at", DateTime(timezone=True), nullable=True),
            Column("ticks_completed", Integer, default=0),
            Column("total_intents", Integer, default=0),
            Column("successful_trades", Integer, default=0),
            Column("final_pnl", Float, nullable=True),
            Column("final_equity", Float, nullable=True),
            Column("error_message", Text, nullable=True),
        )

        # Events table - all pipeline events
        self.events = Table(
            "events",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(128), nullable=False, index=True),
            Column("event_key", String(256), nullable=False),
            Column("event_type", String(64), nullable=False, index=True),
            Column("tick_ts", DateTime(timezone=True), nullable=True, index=True),
            Column("market_id", String(128), nullable=True, index=True),
            Column("payload", JSON, nullable=True),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)),
            Index("ix_events_run_tick", "run_id", "tick_ts"),
            UniqueConstraint("run_id", "event_key", name="uq_events_run_event_key"),
        )

        # Event blobs - large data (prompts, responses, search results)
        self.event_blobs = Table(
            "event_blobs",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(128), nullable=False, index=True),
            Column("event_key", String(256), nullable=False),
            Column("blob_type", String(64), nullable=False),  # prompt, response, search_result
            Column("content", Text, nullable=False),
            Column("created_at", DateTime(timezone=True), default=lambda: datetime.now(UTC)),
        )

        # Positions snapshot per tick
        self.positions = Table(
            "positions",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(128), nullable=False, index=True),
            Column("tick_ts", DateTime(timezone=True), nullable=False),
            Column("market_id", String(128), nullable=False),
            Column("side", String(8), nullable=False),
            Column("shares", Float, nullable=False),
            Column("avg_entry_price", Float, nullable=False),
            Column("current_price", Float, nullable=False),
            Column("unrealized_pnl", Float, nullable=False),
            Column("realized_pnl", Float, nullable=False),
            Index("ix_positions_run_tick", "run_id", "tick_ts"),
        )

        # Trade fills
        self.fills = Table(
            "fills",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(128), nullable=False, index=True),
            Column("tick_ts", DateTime(timezone=True), nullable=False),
            Column("market_id", String(128), nullable=False),
            Column("action", String(8), nullable=False),
            Column("side", String(8), nullable=False),
            Column("shares", Float, nullable=False),
            Column("price", Float, nullable=False),
            Column("cost", Float, nullable=False),
            Column("fill_id", String(128), nullable=True),
            Index("ix_fills_run_tick", "run_id", "tick_ts"),
        )

        # PnL timeseries
        self.pnl_timeseries = Table(
            "pnl_timeseries",
            self.metadata,
            Column("id", Integer, primary_key=True, autoincrement=True),
            Column("run_id", String(128), nullable=False, index=True),
            Column("tick_ts", DateTime(timezone=True), nullable=False),
            Column("cash", Float, nullable=False),
            Column("equity", Float, nullable=False),
            Column("total_pnl", Float, nullable=False),
            Column("tick_pnl", Float, nullable=False),
            Index("ix_pnl_run_tick", "run_id", "tick_ts"),
        )

    def _init_schema(self):
        """Create all tables if they don't exist."""
        try:
            self.metadata.create_all(self.engine, checkfirst=True)
            logger.debug(f"Database schema initialized: {self.db_url}")
        except Exception as e:
            # Handle race condition when multiple processes create tables
            if "already exists" in str(e):
                logger.debug(f"Tables already exist (concurrent init): {e}")
            else:
                raise

    # === Run Management ===

    def create_run(
        self,
        run_id: str | None = None,
        provider: str = "openai",
        model_name: str = "gpt-4o",
        llm_config: dict | None = None,
        search_config: dict | None = None,
    ) -> str:
        """Create a new run record.

        Args:
            run_id: Optional run ID (generated if not provided)
            provider: LLM provider name
            model_name: Model name
            llm_config: Optional LLM configuration
            search_config: Optional search configuration

        Returns:
            The run_id
        """
        if run_id is None:
            run_id = f"{model_name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

        # Combine configs into llm_config for storage
        config = llm_config or {}
        if search_config:
            config["search"] = search_config

        with self.engine.connect() as conn:
            conn.execute(
                self.runs.insert().values(
                    run_id=run_id,
                    provider=provider,
                    model_name=model_name,
                    llm_config=config if config else None,
                    status=RunStatus.PENDING.value,
                    started_at=datetime.now(UTC),
                )
            )
            conn.commit()

        logger.info(f"Created run: {run_id} ({provider}/{model_name})")
        return run_id

    def get_run(self, run_id: str) -> dict | None:
        """Get run metadata."""
        with self.engine.connect() as conn:
            result = conn.execute(
                self.runs.select().where(self.runs.c.run_id == run_id)
            ).fetchone()

            if result:
                return dict(result._mapping)
            return None

    def update_run_status(
        self,
        run_id: str,
        status: RunStatus,
        error_message: str | None = None,
        **kwargs,
    ):
        """Update run status and optional fields."""
        values: dict[str, object] = {"status": status.value}

        if status == RunStatus.COMPLETED:
            values["completed_at"] = datetime.now(UTC)

        if error_message:
            values["error_message"] = error_message

        # Allow updating stats
        for key in ["ticks_completed", "total_intents", "successful_trades", "final_pnl", "final_equity"]:
            if key in kwargs:
                values[key] = kwargs[key]

        with self.engine.connect() as conn:
            conn.execute(
                self.runs.update().where(self.runs.c.run_id == run_id).values(**values)
            )
            conn.commit()

    def list_runs(
        self,
        provider: str | None = None,
        model_name: str | None = None,
        status: RunStatus | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """List runs with optional filters."""
        query = self.runs.select().order_by(self.runs.c.started_at.desc()).limit(limit)

        if provider:
            query = query.where(self.runs.c.provider == provider)
        if model_name:
            query = query.where(self.runs.c.model_name == model_name)
        if status:
            query = query.where(self.runs.c.status == status.value)

        with self.engine.connect() as conn:
            results = conn.execute(query).fetchall()
            return [dict(r._mapping) for r in results]

    # === Event Management ===

    def write_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict,
        event_key: str | None = None,
        tick_ts: datetime | None = None,
        market_id: str | None = None,
    ) -> str:
        """Write an event to the database.

        Args:
            run_id: Run identifier
            event_type: Type of event (tick_start, forecast, action, etc.)
            payload: Event data
            event_key: Optional unique key for idempotency
            tick_ts: Optional tick timestamp
            market_id: Optional market identifier

        Returns:
            The event_key
        """
        if event_key is None:
            ts_str = tick_ts.isoformat() if tick_ts else datetime.now(UTC).isoformat()
            event_key = f"{run_id}:{event_type}:{ts_str}"
            if market_id:
                event_key += f":{market_id}"

        with self.engine.connect() as conn:
            try:
                conn.execute(
                    self.events.insert().values(
                        run_id=run_id,
                        event_key=event_key,
                        event_type=event_type,
                        tick_ts=tick_ts,
                        market_id=market_id,
                        payload=payload,
                    )
                )
                conn.commit()
            except IntegrityError:
                # Idempotent write: event already exists for this run/event_key.
                conn.rollback()

        return event_key

    def write_event_blob(
        self,
        run_id: str,
        event_key: str,
        blob_type: str,
        content: str,
    ):
        """Write a large blob (prompt, response, etc.) associated with an event."""
        with self.engine.connect() as conn:
            conn.execute(
                self.event_blobs.insert().values(
                    run_id=run_id,
                    event_key=event_key,
                    blob_type=blob_type,
                    content=content,
                )
            )
            conn.commit()

    def get_run_stats(self, run_id_prefix: str) -> dict[str, dict]:
        """Get aggregated stats (ticks, trades) for runs matching prefix.

        Returns dict keyed by run_id with {ticks: int, trades: int}.
        Uses SQL aggregation for efficiency.
        """
        with self.engine.connect() as conn:
            # Count tick_start events per run
            tick_query = (
                self.events.select()
                .with_only_columns(
                    self.events.c.run_id,
                    func.count().label("ticks")
                )
                .where(self.events.c.run_id.like(f"{run_id_prefix}%"))
                .where(self.events.c.event_type == "tick_start")
                .group_by(self.events.c.run_id)
            )
            tick_results = {r.run_id: r.ticks for r in conn.execute(tick_query)}

            # Get trade_submission events to count accepted trades
            trade_query = (
                self.events.select()
                .where(self.events.c.run_id.like(f"{run_id_prefix}%"))
                .where(self.events.c.event_type == "trade_submission")
            )
            trade_results = conn.execute(trade_query).fetchall()

            # Sum accepted trades per run
            trades_by_run: dict[str, int] = {}
            for r in trade_results:
                rid = r.run_id
                payload = r.payload or {}
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except json.JSONDecodeError:
                        payload = {}
                trades_by_run[rid] = trades_by_run.get(rid, 0) + payload.get("accepted", 0)

            # Combine results
            all_run_ids = set(tick_results.keys()) | set(trades_by_run.keys())
            return {
                rid: {"ticks": tick_results.get(rid, 0), "trades": trades_by_run.get(rid, 0)}
                for rid in all_run_ids
            }

    def get_events(
        self,
        run_id: str,
        event_type: str | None = None,
        event_types: list[str] | None = None,
        tick_ts: datetime | None = None,
        market_id: str | None = None,
        limit: int = 1000,
        prefix_match: bool = False,
    ) -> list[dict]:
        """Query events with optional filters.

        Args:
            run_id: Run ID (exact match) or prefix (if prefix_match=True)
            event_type: Single event type filter
            event_types: Multiple event types (OR)
            prefix_match: If True, match run_id as prefix
        """
        if prefix_match:
            query = self.events.select().where(self.events.c.run_id.like(f"{run_id}%"))
        else:
            query = self.events.select().where(self.events.c.run_id == run_id)

        if event_type:
            query = query.where(self.events.c.event_type == event_type)
        if event_types:
            query = query.where(self.events.c.event_type.in_(event_types))
        if tick_ts:
            query = query.where(self.events.c.tick_ts == tick_ts)
        if market_id:
            query = query.where(self.events.c.market_id == market_id)

        query = query.order_by(self.events.c.created_at.desc()).limit(limit)

        with self.engine.connect() as conn:
            results = conn.execute(query).fetchall()
            return [dict(r._mapping) for r in results]

    def get_event_blobs(
        self,
        run_id: str,
        event_key: str | None = None,
        blob_type: str | None = None,
    ) -> list[dict]:
        """Get event blobs."""
        query = self.event_blobs.select().where(
            self.event_blobs.c.run_id == run_id
        )

        if event_key:
            query = query.where(self.event_blobs.c.event_key == event_key)
        if blob_type:
            query = query.where(self.event_blobs.c.blob_type == blob_type)

        with self.engine.connect() as conn:
            results = conn.execute(query).fetchall()
            return [dict(r._mapping) for r in results]

    # === Portfolio Management ===

    def write_position_snapshot(
        self,
        run_id: str,
        tick_ts: datetime,
        positions: list[Position],
    ):
        """Write position snapshot for a tick."""
        with self.engine.connect() as conn:
            for pos in positions:
                conn.execute(
                    self.positions.insert().values(
                        run_id=run_id,
                        tick_ts=tick_ts,
                        market_id=pos.market_id,
                        side=pos.side,
                        shares=float(pos.shares),
                        avg_entry_price=float(pos.avg_entry_price),
                        current_price=float(pos.current_price),
                        unrealized_pnl=float(pos.unrealized_pnl),
                        realized_pnl=float(pos.realized_pnl),
                    )
                )
            conn.commit()

    def write_fill(
        self,
        run_id: str,
        tick_ts: datetime,
        market_id: str,
        action: str,
        side: str,
        shares: float,
        price: float,
        cost: float,
        fill_id: str | None = None,
    ):
        """Write a trade fill."""
        with self.engine.connect() as conn:
            conn.execute(
                self.fills.insert().values(
                    run_id=run_id,
                    tick_ts=tick_ts,
                    market_id=market_id,
                    action=action,
                    side=side,
                    shares=shares,
                    price=price,
                    cost=cost,
                    fill_id=fill_id,
                )
            )
            conn.commit()

    def write_pnl(
        self,
        run_id: str,
        tick_ts: datetime,
        cash: float,
        equity: float,
        total_pnl: float,
        tick_pnl: float,
    ):
        """Write PnL timeseries point."""
        with self.engine.connect() as conn:
            conn.execute(
                self.pnl_timeseries.insert().values(
                    run_id=run_id,
                    tick_ts=tick_ts,
                    cash=cash,
                    equity=equity,
                    total_pnl=total_pnl,
                    tick_pnl=tick_pnl,
                )
            )
            conn.commit()

    def get_pnl_history(self, run_id: str) -> list[dict]:
        """Get PnL timeseries for a run."""
        with self.engine.connect() as conn:
            results = conn.execute(
                self.pnl_timeseries.select().where(
                    self.pnl_timeseries.c.run_id == run_id
                ).order_by(self.pnl_timeseries.c.tick_ts)
            ).fetchall()
            return [dict(r._mapping) for r in results]

    def get_portfolio(self, run_id: str, tick_ts: datetime) -> PortfolioSnapshot | None:
        """Get portfolio state at a specific tick."""
        with self.engine.connect() as conn:
            # Get positions
            pos_results = conn.execute(
                self.positions.select().where(
                    (self.positions.c.run_id == run_id) &
                    (self.positions.c.tick_ts == tick_ts)
                )
            ).fetchall()

            # Get PnL for cash/equity
            pnl_result = conn.execute(
                self.pnl_timeseries.select().where(
                    (self.pnl_timeseries.c.run_id == run_id) &
                    (self.pnl_timeseries.c.tick_ts == tick_ts)
                )
            ).fetchone()

            if not pnl_result:
                return None

            positions = [
                Position(
                    market_id=r.market_id,
                    side=r.side,
                    shares=Decimal(str(r.shares)),
                    avg_entry_price=Decimal(str(r.avg_entry_price)),
                    current_price=Decimal(str(r.current_price)),
                    unrealized_pnl=Decimal(str(r.unrealized_pnl)),
                    realized_pnl=Decimal(str(r.realized_pnl)),
                    updated_at=tick_ts,
                )
                for r in pos_results
            ]

            return {
                "cash": Decimal(str(pnl_result.cash)),
                "equity": Decimal(str(pnl_result.equity)),
                "positions": positions,
            }

    # === Counts ===

    def count_events(
        self,
        run_id: str,
        event_type: str | None = None,
    ) -> int:
        """Count events for a run using SQL COUNT.

        Args:
            run_id: Run identifier
            event_type: Optional event type filter

        Returns:
            Number of matching events
        """
        query = (
            self.events.select()
            .with_only_columns(func.count())
            .where(self.events.c.run_id == run_id)
        )
        if event_type:
            query = query.where(self.events.c.event_type == event_type)

        with self.engine.connect() as conn:
            return conn.execute(query).scalar() or 0

    # === Analysis ===

    def compare_runs(
        self,
        run_ids: list[str] | None = None,
        group_by_model: bool = False,
    ) -> dict:
        """Compare runs by performance.

        Args:
            run_ids: Optional list of run IDs to compare (all completed if None)
            group_by_model: If True, group results by model

        Returns:
            Comparison data
        """
        with self.engine.connect() as conn:
            if run_ids:
                results = conn.execute(
                    self.runs.select().where(self.runs.c.run_id.in_(run_ids))
                ).fetchall()
            else:
                results = conn.execute(
                    self.runs.select().where(self.runs.c.status == RunStatus.COMPLETED.value)
                ).fetchall()

            runs = [dict(r._mapping) for r in results]

            if group_by_model:
                grouped: dict[str, list[dict]] = {}
                for run in runs:
                    key = f"{run['provider']}:{run['model_name']}"
                    if key not in grouped:
                        grouped[key] = []
                    grouped[key].append(run)
                return {"grouped": grouped, "runs": runs}

            return {"runs": runs}

