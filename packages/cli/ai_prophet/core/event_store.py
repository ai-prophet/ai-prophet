"""EventStore - Append-only event log for agent runs.

All events are stored in ClientDatabase for unified storage.

Properties:
- Append-only (no updates/deletes)
- Deterministic event keys for traceability
- Supports --redact mode (no prompt/response storage)
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .database import ClientDatabase


class EventType(StrEnum):
    """Types of events stored in EventStore."""
    TICK_START = "tick_start"
    TICK_COMPLETE = "tick_complete"
    REVIEW_DECISION = "review_decision"
    SEARCH_QUERY = "search_query"
    SEARCH_RESULT = "search_result"
    FORECAST = "forecast"
    ACTION = "action"
    TRADE_SUBMISSION = "trade_submission"
    PNL_SNAPSHOT = "pnl_snapshot"


class TickState(StrEnum):
    """States a tick can be in."""
    INITIALIZING = "INITIALIZING"
    REVIEWING = "REVIEWING"
    SEARCHING = "SEARCHING"
    FORECASTING = "FORECASTING"
    ACTING = "ACTING"
    SUBMITTING = "SUBMITTING"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


class EventStore:
    """Append-only event store for one run.

    Wraps ClientDatabase with run-scoped event logging.

    Example:
        db = ClientDatabase()
        event_store = EventStore(run_id="my_run", db=db)
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
    """

    def __init__(self, run_id: str, db: ClientDatabase, redact: bool = False):
        """Initialize EventStore.

        Args:
            run_id: Run identifier
            db: ClientDatabase instance
            redact: If True, don't store prompts/responses
        """
        self.run_id = run_id
        self._db = db
        self.redact = redact

    def _make_event_id(
        self,
        event_type: EventType,
        tick_ts: datetime,
        market_id: str | None = None,
        extra: str | None = None
    ) -> str:
        """Create deterministic event ID.

        Format: {event_type}:{tick_ts}[:{market_id}][:{extra}]
        """
        parts = [event_type.value, tick_ts.isoformat()]
        if market_id:
            parts.append(market_id)
        if extra:
            parts.append(extra)
        return ":".join(parts)

    def _write_event(
        self,
        event_id: str,
        event_type: EventType,
        tick_ts: datetime,
        data: dict[str, Any],
        market_id: str | None = None
    ):
        """Write event to database (idempotent)."""
        self._db.write_event(
            run_id=self.run_id,
            event_key=event_id,
            event_type=event_type.value,
            tick_ts=tick_ts,
            payload=data,
            market_id=market_id,
        )

    # Tick-level events

    def write_tick_start(self, tick_ts: datetime, state: TickState):
        """Record tick initialization."""
        event_id = self._make_event_id(EventType.TICK_START, tick_ts)
        data = {"state": state.value}
        self._write_event(event_id, EventType.TICK_START, tick_ts, data)
        self.update_tick_state(tick_ts, state)

    def write_tick_complete(self, tick_ts: datetime):
        """Record tick completion."""
        event_id = self._make_event_id(EventType.TICK_COMPLETE, tick_ts)
        data = {"completed_at": datetime.now(UTC).isoformat()}
        self._write_event(event_id, EventType.TICK_COMPLETE, tick_ts, data)
        self.update_tick_state(tick_ts, TickState.COMPLETED)

    def update_tick_state(self, tick_ts: datetime, state: TickState):
        """Update tick state."""
        # Tick state is a timeline, not an idempotent singleton: use a unique
        # key per transition so REVIEWING/FORECASTING/COMPLETED are preserved.
        event_key = (
            f"tick_state:{self.run_id}:{tick_ts.isoformat()}:"
            f"{state.value}:{datetime.now(UTC).isoformat()}"
        )
        self._db.write_event(
            run_id=self.run_id,
            event_key=event_key,
            event_type="tick_state",
            tick_ts=tick_ts,
            payload={"state": state.value},
        )

    # Review stage events

    def write_review_decision(
        self,
        tick_ts: datetime,
        market_id: str,
        priority: int,
        queries: list[str],
        rationale: str
    ):
        """Record review decision for a market."""
        event_id = self._make_event_id(EventType.REVIEW_DECISION, tick_ts, market_id)
        data = {
            "market_id": market_id,
            "priority": priority,
            "queries": queries,
            "rationale": rationale if not self.redact else "[REDACTED]"
        }
        self._write_event(event_id, EventType.REVIEW_DECISION, tick_ts, data, market_id)

    # Search stage events

    def write_search_query(
        self,
        tick_ts: datetime,
        market_id: str,
        query_idx: int,
        query: str
    ):
        """Record search query."""
        event_id = self._make_event_id(EventType.SEARCH_QUERY, tick_ts, market_id, f"q{query_idx}")
        data = {
            "market_id": market_id,
            "query_idx": query_idx,
            "query": query if not self.redact else "[REDACTED]"
        }
        self._write_event(event_id, EventType.SEARCH_QUERY, tick_ts, data, market_id)

    def write_search_result(
        self,
        tick_ts: datetime,
        market_id: str,
        query_idx: int,
        query: str,
        summary: str,
        urls: list[str],
        error: str | None = None
    ):
        """Record search result."""
        event_id = self._make_event_id(EventType.SEARCH_RESULT, tick_ts, market_id, f"r{query_idx}")
        data = {
            "market_id": market_id,
            "query_idx": query_idx,
            "query": query if not self.redact else "[REDACTED]",
            "summary": summary if not self.redact else "[REDACTED]",
            "urls": urls,
            "error": error
        }
        self._write_event(event_id, EventType.SEARCH_RESULT, tick_ts, data, market_id)

    # Forecast stage events

    def write_forecast(
        self,
        tick_ts: datetime,
        market_id: str,
        p_yes: float,
        rationale: str,
        question: str | None = None
    ):
        """Record probability forecast."""
        event_id = self._make_event_id(EventType.FORECAST, tick_ts, market_id)
        data = {
            "market_id": market_id,
            "question": question,
            "p_yes": p_yes,
            "rationale": rationale if not self.redact else "[REDACTED]"
        }
        self._write_event(event_id, EventType.FORECAST, tick_ts, data, market_id)

    def write_trade_decision(
        self,
        tick_ts: datetime,
        market_id: str,
        recommendation: str,
        size_usd: float,
        rationale: str,
        question: str | None = None
    ):
        """Record trade decision from action stage."""
        event_id = self._make_event_id(EventType.ACTION, tick_ts, market_id, "decision")
        data = {
            "market_id": market_id,
            "question": question,
            "recommendation": recommendation,
            "size_usd": size_usd,
            "rationale": rationale if not self.redact else "[REDACTED]"
        }
        self._write_event(event_id, EventType.ACTION, tick_ts, data, market_id)

    # Trade submission events

    def write_trade_submission(
        self,
        tick_ts: datetime,
        intents: list[dict],
        result: dict
    ):
        """Record trade submission and result."""
        event_id = self._make_event_id(EventType.TRADE_SUBMISSION, tick_ts)
        data = {
            "num_intents": len(intents),
            "intents": intents,
            "accepted": result.get("accepted", 0),
            "rejected": result.get("rejected", 0),
            "fills": result.get("fills", []),
            "rejections": result.get("rejections", [])
        }
        self._write_event(event_id, EventType.TRADE_SUBMISSION, tick_ts, data)

    def write_pnl_snapshot(self, tick_ts: datetime, cash: float, equity: float, pnl: float):
        """Record portfolio value snapshot at end of tick."""
        event_id = self._make_event_id(EventType.PNL_SNAPSHOT, tick_ts)
        self._write_event(event_id, EventType.PNL_SNAPSHOT, tick_ts, {
            "cash": cash, "equity": equity, "pnl": pnl
        })

    # Query methods

    def tick_already_completed(self, tick_ts: datetime) -> bool:
        """Check if tick is already completed."""
        events = self._db.get_events(
            run_id=self.run_id,
            event_type="tick_complete",
            tick_ts=tick_ts,
            limit=1
        )
        return len(events) > 0

    def get_tick_state(self, tick_ts: datetime) -> TickState | None:
        """Get current state of a tick."""
        events = self._db.get_events(
            run_id=self.run_id,
            event_type="tick_state",
            tick_ts=tick_ts,
            limit=10
        )
        if events:
            latest = events[0]  # events ordered by created_at DESC
            payload = latest.get("payload", {})
            state_str = payload.get("state")
            if state_str:
                return TickState(state_str)
        return None

    def get_last_completed_tick(self) -> datetime | None:
        """Get timestamp of last completed tick."""
        events = self._db.get_events(
            run_id=self.run_id,
            event_type="tick_complete",
            limit=1,
        )
        if events:
            return events[0].get("tick_ts")  # events ordered by created_at DESC
        return None

    def get_events(
        self,
        tick_ts: datetime | None = None,
        event_type: EventType | None = None,
        market_id: str | None = None,
        limit: int | None = None
    ) -> list[dict]:
        """Query events with optional filters."""
        event_type_str = event_type.value if event_type else None
        return self._db.get_events(
            run_id=self.run_id,
            event_type=event_type_str,
            tick_ts=tick_ts,
            market_id=market_id,
            limit=limit or 1000,
        )

    def get_review_decisions(self, tick_ts: datetime) -> list[dict]:
        """Get all review decisions for a tick."""
        return self.get_events(tick_ts, EventType.REVIEW_DECISION)

    def get_forecasts(self, tick_ts: datetime) -> list[dict]:
        """Get all forecasts for a tick."""
        return self.get_events(tick_ts, EventType.FORECAST)

    def get_trade_submission(self, tick_ts: datetime) -> dict | None:
        """Get trade submission for a tick."""
        events = self.get_events(tick_ts, EventType.TRADE_SUBMISSION)
        return events[0] if events else None

    def count_events(self) -> int:
        """Total number of events stored."""
        return self._db.count_events(self.run_id)

    def count_ticks(self) -> int:
        """Total number of ticks processed."""
        return self._db.count_events(self.run_id, event_type="tick_start")
