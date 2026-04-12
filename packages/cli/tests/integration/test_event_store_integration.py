"""Integration tests for EventStore with agent pipeline."""

import shutil
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from ai_prophet.trade.core.database import ClientDatabase
from ai_prophet.trade.core.event_store import EventStore, TickState
from ai_prophet.trade.core.tick_context import TickContext


@pytest.fixture
def temp_data_dir():
    """Create temporary data directory."""
    temp_dir = Path(tempfile.mkdtemp())
    yield temp_dir
    shutil.rmtree(temp_dir)


@pytest.fixture
def client_db(temp_data_dir):
    """Create ClientDatabase for testing."""
    db_path = temp_data_dir / "integration_test.db"
    return ClientDatabase(db_url=f"sqlite:///{db_path}")


@pytest.fixture
def event_store(client_db):
    """Create EventStore for testing."""
    return EventStore(run_id="integration_test", db=client_db, redact=False)


@pytest.fixture
def tick_ctx():
    """Create test TickContext."""
    tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    data_asof_ts = datetime(2026, 1, 20, 5, 58, 0, tzinfo=UTC)

    return TickContext(
        run_id="integration_test",
        tick_ts=tick_ts,
        data_asof_ts=data_asof_ts,
        candidate_set_id="snapshot_abc123",
        submission_deadline=datetime(2026, 1, 20, 6, 5, 0, tzinfo=UTC),
        server_now=datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC),
        candidates=(),
        cash=Decimal("10000.00"),
        equity=Decimal("10000.00"),
        total_pnl=Decimal("0.00"),
        positions=(),
        total_fills=0,
    )


class TestPipelineIntegration:
    """Test EventStore integration with pipeline stages."""

    def test_full_tick_lifecycle(self, event_store, tick_ctx):
        """Test recording a full tick lifecycle."""
        tick_ts = tick_ctx.tick_ts

        # Start tick
        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)

        # Review stage
        event_store.write_review_decision(
            tick_ts=tick_ts,
            market_id="market_1",
            priority=90,
            queries=["query about market 1"],
            rationale="High priority"
        )

        # Search stage
        event_store.write_search_query(tick_ts, "market_1", 0, "query about market 1")
        event_store.write_search_result(
            tick_ts=tick_ts,
            market_id="market_1",
            query_idx=0,
            query="query about market 1",
            summary="Found relevant info",
            urls=["http://example.com"]
        )

        # Forecast stage
        event_store.write_forecast(
            tick_ts=tick_ts,
            market_id="market_1",
            p_yes=0.7,
            rationale="Evidence suggests YES",
            question="Will X happen?"
        )

        # Action stage
        event_store.write_trade_decision(
            tick_ts=tick_ts,
            market_id="market_1",
            recommendation="BUY_YES",
            size_usd=100.0,
            rationale="Good edge"
        )

        # Trade submission
        intents = [{"market_id": "market_1", "action": "BUY", "side": "YES", "shares": "142.86"}]
        result = {"accepted": 1, "rejected": 0, "fills": [], "rejections": []}
        event_store.write_trade_submission(tick_ts, intents, result)

        # Complete tick
        event_store.write_tick_complete(tick_ts)

        # Verify
        assert event_store.tick_already_completed(tick_ts)
        assert event_store.count_events() >= 6

    def test_multiple_markets_in_tick(self, event_store, tick_ctx):
        """Test handling multiple markets in a single tick."""
        tick_ts = tick_ctx.tick_ts

        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)

        for i in range(3):
            market_id = f"market_{i}"
            event_store.write_forecast(
                tick_ts=tick_ts,
                market_id=market_id,
                p_yes=0.5 + i * 0.1,
                rationale=f"Rationale for {market_id}"
            )

        forecasts = event_store.get_forecasts(tick_ts)
        assert len(forecasts) == 3


class TestStateTransitions:
    """Test tick state transitions."""

    def test_state_progression(self, event_store, tick_ctx):
        """Test state progresses through pipeline."""
        tick_ts = tick_ctx.tick_ts

        event_store.write_tick_start(tick_ts, TickState.INITIALIZING)
        assert event_store.get_tick_state(tick_ts) == TickState.INITIALIZING

        event_store.update_tick_state(tick_ts, TickState.REVIEWING)
        assert event_store.get_tick_state(tick_ts) == TickState.REVIEWING

        event_store.update_tick_state(tick_ts, TickState.FORECASTING)
        assert event_store.get_tick_state(tick_ts) == TickState.FORECASTING

        event_store.write_tick_complete(tick_ts)
        assert event_store.get_tick_state(tick_ts) == TickState.COMPLETED


class TestMultipleRuns:
    """Test handling multiple runs."""

    def test_separate_runs(self, client_db, tick_ctx):
        """Test that different runs are isolated."""
        store_1 = EventStore(run_id="run_1", db=client_db)
        store_2 = EventStore(run_id="run_2", db=client_db)

        tick_ts = tick_ctx.tick_ts

        store_1.write_tick_start(tick_ts, TickState.INITIALIZING)
        store_2.write_tick_start(tick_ts, TickState.INITIALIZING)

        store_1.write_forecast(tick_ts, "market_a", 0.6, "Run 1 forecast")
        store_2.write_forecast(tick_ts, "market_b", 0.4, "Run 2 forecast")

        forecasts_1 = store_1.get_forecasts(tick_ts)
        forecasts_2 = store_2.get_forecasts(tick_ts)

        assert len(forecasts_1) == 1
        assert len(forecasts_2) == 1
        assert forecasts_1[0]["payload"]["market_id"] == "market_a"
        assert forecasts_2[0]["payload"]["market_id"] == "market_b"
