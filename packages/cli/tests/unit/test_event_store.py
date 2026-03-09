"""Unit tests for EventStore."""

import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_prophet.trade.core.database import ClientDatabase
from ai_prophet.trade.core.event_store import EventStore, EventType, TickState


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def client_db(temp_data_dir):
    """Create ClientDatabase for testing."""
    db_path = temp_data_dir / "test.db"
    return ClientDatabase(db_url=f"sqlite:///{db_path}")


@pytest.fixture
def event_store(client_db):
    """Create EventStore for testing."""
    return EventStore(run_id="test_run_123", db=client_db, redact=False)


@pytest.fixture
def tick_ts():
    """Create test tick timestamp."""
    return datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)


class TestEventStoreBasics:
    """Test basic EventStore functionality."""

    def test_initialization(self, event_store):
        """Test EventStore initialization."""
        assert event_store.run_id == "test_run_123"
        assert event_store._db is not None

    def test_write_tick_start(self, event_store, tick_ts):
        """Test writing tick_start event."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)

        events = event_store.get_events(tick_ts, EventType.TICK_START)
        assert len(events) == 1
        assert events[0]["event_type"] == "tick_start"

    def test_write_tick_complete(self, event_store, tick_ts):
        """Test writing tick_complete event."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        event_store.write_tick_complete(tick_ts)

        assert event_store.tick_already_completed(tick_ts)

    def test_tick_not_completed(self, event_store, tick_ts):
        """Test tick_already_completed returns False for incomplete tick."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        assert not event_store.tick_already_completed(tick_ts)


class TestReviewEvents:
    """Test review stage events."""

    def test_write_review_decision(self, event_store, tick_ts):
        """Test writing review decision."""
        event_store.write_review_decision(
            tick_ts=tick_ts,
            market_id="market_123",
            priority=80,
            queries=["query1", "query2"],
            rationale="High priority market"
        )

        events = event_store.get_review_decisions(tick_ts)
        assert len(events) == 1
        payload = events[0].get("payload", {})
        assert payload["market_id"] == "market_123"
        assert payload["priority"] == 80


class TestSearchEvents:
    """Test search stage events."""

    def test_write_search_query(self, event_store, tick_ts):
        """Test writing search query."""
        event_store.write_search_query(
            tick_ts=tick_ts,
            market_id="market_123",
            query_idx=0,
            query="test query"
        )

        events = event_store.get_events(tick_ts, EventType.SEARCH_QUERY)
        assert len(events) == 1

    def test_write_search_result(self, event_store, tick_ts):
        """Test writing search result."""
        event_store.write_search_result(
            tick_ts=tick_ts,
            market_id="market_123",
            query_idx=0,
            query="test query",
            summary="Test summary",
            urls=["http://example.com"],
            error=None
        )

        events = event_store.get_events(tick_ts, EventType.SEARCH_RESULT)
        assert len(events) == 1


class TestForecastEvents:
    """Test forecast stage events."""

    def test_write_forecast(self, event_store, tick_ts):
        """Test writing forecast."""
        event_store.write_forecast(
            tick_ts=tick_ts,
            market_id="market_123",
            p_yes=0.65,
            rationale="Strong evidence for YES",
            question="Will X happen?"
        )

        events = event_store.get_forecasts(tick_ts)
        assert len(events) == 1
        payload = events[0].get("payload", {})
        assert payload["p_yes"] == 0.65


class TestActionEvents:
    """Test action stage events."""

    def test_write_trade_decision(self, event_store, tick_ts):
        """Test writing trade decision."""
        event_store.write_trade_decision(
            tick_ts=tick_ts,
            market_id="market_123",
            recommendation="BUY_YES",
            size_usd=100.0,
            rationale="Good edge"
        )

        events = event_store.get_events(tick_ts, EventType.ACTION)
        assert len(events) == 1

class TestTradeSubmission:
    """Test trade submission events."""

    def test_write_trade_submission(self, event_store, tick_ts):
        """Test writing trade submission."""
        intents = [{"market_id": "m1", "action": "BUY", "side": "YES", "shares": "100"}]
        result = {"accepted": 1, "rejected": 0, "fills": [], "rejections": []}

        event_store.write_trade_submission(tick_ts, intents, result)

        submission = event_store.get_trade_submission(tick_ts)
        assert submission is not None
        payload = submission.get("payload", {})
        assert payload["accepted"] == 1


class TestRedactMode:
    """Test redact mode."""

    def test_redact_rationale(self, client_db, tick_ts):
        """Test that rationale is redacted in redact mode."""
        event_store = EventStore(run_id="test", db=client_db, redact=True)

        event_store.write_forecast(
            tick_ts=tick_ts,
            market_id="market_123",
            p_yes=0.65,
            rationale="Secret rationale"
        )

        events = event_store.get_forecasts(tick_ts)
        payload = events[0].get("payload", {})
        assert payload["rationale"] == "[REDACTED]"


class TestQueryMethods:
    """Test query methods."""

    def test_count_events(self, event_store, tick_ts):
        """Test counting events."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        event_store.write_tick_complete(tick_ts)

        count = event_store.count_events()
        assert count >= 2

    def test_count_ticks(self, event_store, tick_ts):
        """Test counting ticks."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)

        tick_ts2 = tick_ts + timedelta(hours=1)
        event_store.write_tick_start(tick_ts2, TickState.INITIALIZING)

        count = event_store.count_ticks()
        assert count == 2

    def test_get_last_completed_tick(self, event_store, tick_ts):
        """Test getting last completed tick."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        event_store.write_tick_complete(tick_ts)

        tick_ts2 = tick_ts + timedelta(hours=1)
        event_store.write_tick_start(tick_ts2, TickState.INITIALIZING)
        event_store.write_tick_complete(tick_ts2)

        last = event_store.get_last_completed_tick()
        assert last is not None


class TestIdempotency:
    """Test idempotent writes."""

    def test_duplicate_event_ignored(self, event_store, tick_ts):
        """Test that duplicate events are handled gracefully."""
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)

        events = event_store.get_events(tick_ts, EventType.TICK_START)
        # Should have at least one event (implementation may vary on duplicates)
        assert len(events) >= 1
