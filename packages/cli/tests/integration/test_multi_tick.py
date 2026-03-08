"""Integration tests for multi-tick pipeline behavior."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

from ai_prophet_core.client import ServerAPIClient

from ai_prophet.agent import AgentPipeline
from ai_prophet.core import ClientDatabase, EventStore, TickContext
from ai_prophet.core.event_store import EventType, TickState
from ai_prophet.core.tick_context import CandidateMarket
from ai_prophet.llm import LLMClient


def _make_tick_context(run_id: str, tick_ts: datetime) -> TickContext:
    candidate = CandidateMarket(
        market_id="market_123",
        question="Will event X happen before Feb 1?",
        description="Test market",
        resolution_time=datetime(2026, 2, 1, 12, 0, 0, tzinfo=UTC),
        yes_bid=0.50,
        yes_ask=0.52,
        yes_mark=0.51,
        no_bid=0.48,
        no_ask=0.50,
        no_mark=0.49,
        volume_24h=50000.0,
        quote_ts=tick_ts,
    )

    return TickContext(
        run_id=run_id,
        tick_ts=tick_ts,
        data_asof_ts=tick_ts - timedelta(minutes=5),
        candidate_set_id="snapshot_1",
        submission_deadline=tick_ts + timedelta(minutes=5),
        server_now=tick_ts,
        candidates=(candidate,),
        cash=Decimal("10000.00"),
        equity=Decimal("10000.00"),
        total_pnl=Decimal("0.00"),
        positions=(),
        total_fills=0,
    )


def _make_llm_client() -> Mock:
    client = Mock(spec=LLMClient)
    client.provider = "mock"
    client.model = "mock-model"

    def _generate_json(messages, tool=None, **kwargs):
        _ = (messages, kwargs)
        tool_name = getattr(tool, "name", None)
        if tool_name == "submit_review":
            return {
                "review": [
                    {
                        "market_id": "market_123",
                        "priority": 80,
                        "queries": ["latest event X evidence"],
                        "rationale": "High expected information value.",
                    }
                ]
            }
        if tool_name == "submit_search_summary":
            return {
                "summary": "Recent developments support a moderate YES edge.",
                "key_points": ["Evidence trend is positive."],
                "open_questions": ["How durable is the trend?"],
            }
        if tool_name == "submit_forecast":
            return {
                "p_yes": 0.65,
                "rationale": "Signals imply a higher than market probability.",
            }
        if tool_name == "submit_trade_decision":
            return {
                "recommendation": "BUY_YES",
                "size_usd": 52.0,
                "rationale": "Forecast-market spread justifies a small long.",
            }
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    client.generate_json = Mock(side_effect=_generate_json)
    return client


def test_pipeline_emits_expected_intent_and_events():
    run_id = "test_multi_tick_signal"
    tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db = ClientDatabase(db_url=f"sqlite:///{data_dir}/test.db")
        db.create_run(run_id=run_id, provider="mock", model_name="mock-model")
        event_store = EventStore(run_id=run_id, db=db)

        api_client = Mock(spec=ServerAPIClient)
        api_client.base_url = "http://test.example.com"
        llm_client = _make_llm_client()
        pipeline = AgentPipeline(
            llm_client=llm_client,
            api_client=api_client,
            event_store=event_store,
        )

        tick_ctx = _make_tick_context(run_id=run_id, tick_ts=tick_ts)
        result = pipeline.execute(tick_ctx, run_id=run_id, publish_reasoning=True)

        assert len(result.intents) == 1
        intent = result.intents[0]
        assert intent["market_id"] == "market_123"
        assert intent["action"] == "BUY"
        assert intent["side"] == "YES"
        assert intent["shares"] == "100.00"

        assert result.reasoning is not None
        assert "review" in result.reasoning
        assert "search" in result.reasoning
        assert "forecasts" in result.reasoning
        assert "decisions" in result.reasoning

        assert len(event_store.get_events(tick_ts, EventType.TICK_START)) == 1
        assert len(event_store.get_events(tick_ts, EventType.REVIEW_DECISION)) == 1
        assert len(event_store.get_events(tick_ts, EventType.SEARCH_RESULT)) == 1
        assert len(event_store.get_events(tick_ts, EventType.FORECAST)) == 1
        assert len(event_store.get_events(tick_ts, EventType.ACTION)) == 1
        assert len(event_store.get_events(tick_ts, EventType.TICK_COMPLETE)) == 1
        assert event_store.get_tick_state(tick_ts) == TickState.COMPLETED


def test_reexecuting_same_tick_keeps_stage_events_idempotent():
    run_id = "test_multi_tick_idempotent"
    tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db = ClientDatabase(db_url=f"sqlite:///{data_dir}/test.db")
        db.create_run(run_id=run_id, provider="mock", model_name="mock-model")
        event_store = EventStore(run_id=run_id, db=db)

        api_client = Mock(spec=ServerAPIClient)
        api_client.base_url = "http://test.example.com"
        llm_client = _make_llm_client()
        pipeline = AgentPipeline(
            llm_client=llm_client,
            api_client=api_client,
            event_store=event_store,
        )

        tick_ctx = _make_tick_context(run_id=run_id, tick_ts=tick_ts)
        first = pipeline.execute(tick_ctx, run_id=run_id)
        second = pipeline.execute(tick_ctx, run_id=run_id)

        assert first.intents == second.intents

        # EventStore event keys are deterministic for these stage events.
        assert len(event_store.get_events(tick_ts, EventType.TICK_START)) == 1
        assert len(event_store.get_events(tick_ts, EventType.REVIEW_DECISION)) == 1
        assert len(event_store.get_events(tick_ts, EventType.SEARCH_RESULT)) == 1
        assert len(event_store.get_events(tick_ts, EventType.FORECAST)) == 1
        assert len(event_store.get_events(tick_ts, EventType.ACTION)) == 1
        assert len(event_store.get_events(tick_ts, EventType.TICK_COMPLETE)) == 1
