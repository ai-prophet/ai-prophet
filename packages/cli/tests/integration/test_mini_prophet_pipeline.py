"""Integration tests for the mini-prophet pipeline path."""

from __future__ import annotations

import json
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

from ai_prophet_core.client import ServerAPIClient

from ai_prophet.trade.agent import AgentPipeline
from ai_prophet.trade.core import ClientDatabase, EventStore, TickContext
from ai_prophet.trade.core.config import ClientConfig, MiniProphetConfig
from ai_prophet.trade.core.event_store import EventType
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.llm import LLMClient
from ai_prophet.trade.llm.base import LLMResponse


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
    """Create a mock LLM client that handles both legacy and mini-prophet flows.

    For the mini-prophet path, ``generate()`` is used (via the bridge).
    For review/action stages, ``generate_json()`` is used (via tool calling).
    """
    client = Mock(spec=LLMClient)
    client.provider = "mock"
    client.model = "mock-model"

    # Track how many generate() calls have been made to simulate the agent loop
    call_counter = {"n": 0}

    def _generate_json(messages, tool=None, **kwargs):
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
        if tool_name == "submit_trade_decision":
            return {
                "recommendation": "BUY_YES",
                "size_usd": 52.0,
                "rationale": "Forecast-market spread justifies a small long.",
            }
        raise AssertionError(f"Unexpected tool call: {tool_name}")

    def _generate(request):
        """Simulate the agent loop: first call does submit."""
        call_counter["n"] += 1
        # On the first agent generate() call, submit the forecast
        return LLMResponse(
            content="",
            model="mock-model",
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            finish_reason="tool_use",
            tool_output=None,
            tool_calls=[
                {
                    "name": "submit",
                    "arguments": {
                        "probabilities": {"Yes": 0.72, "No": 0.28},
                        "rationale": "Mock forecast: evidence supports Yes.",
                    },
                    "id": f"call_{call_counter['n']}",
                },
            ],
        )

    client.generate_json = Mock(side_effect=_generate_json)
    client.generate = Mock(side_effect=_generate)
    return client


def test_mini_prophet_pipeline_produces_trade_intent():
    """End-to-end test with mini_prophet.enabled=True."""
    run_id = "test_mini_prophet_e2e"
    tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db = ClientDatabase(db_url=f"sqlite:///{data_dir}/test.db")
        db.create_run(run_id=run_id, provider="mock", model_name="mock-model")
        event_store = EventStore(run_id=run_id, db=db)

        api_client = Mock(spec=ServerAPIClient)
        api_client.base_url = "http://test.example.com"
        llm_client = _make_llm_client()

        # Build a config with mini_prophet enabled
        ClientConfig.reset()
        config = ClientConfig.defaults()
        config.mini_prophet = MiniProphetConfig(
            enabled=True,
            step_limit=3,
            search_limit=1,
            cost_limit=0.1,
            context_window=4,
        )

        pipeline = AgentPipeline(
            llm_client=llm_client,
            api_client=api_client,
            event_store=event_store,
            client_config=config,
        )

        # Should be 3 stages: Review, MiniProphetForecast, Action
        assert len(pipeline.stages) == 3
        assert pipeline.stages[1].name == "forecast"

        tick_ctx = _make_tick_context(run_id=run_id, tick_ts=tick_ts)

        # Patch the actual agent run to avoid needing exa-py / real search
        with patch.object(
            pipeline.stages[1],
            "_run_agent_for_market",
            return_value=(
                {"submission": {"Yes": 0.72, "No": 0.28}},
                "Mock: evidence supports Yes.",
            ),
        ):
            result = pipeline.execute(tick_ctx, run_id=run_id)

        assert len(result.intents) == 1
        intent = result.intents[0]
        assert intent["market_id"] == "market_123"
        assert intent["action"] == "BUY"
        assert intent["side"] == "YES"

        # Clean up singleton
        ClientConfig.reset()


def test_mini_prophet_pipeline_event_store_logging():
    """Verify forecast events are logged correctly with mini-prophet path."""
    run_id = "test_mini_prophet_events"
    tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)

    with tempfile.TemporaryDirectory() as tmpdir:
        data_dir = Path(tmpdir)
        db = ClientDatabase(db_url=f"sqlite:///{data_dir}/test.db")
        db.create_run(run_id=run_id, provider="mock", model_name="mock-model")
        event_store = EventStore(run_id=run_id, db=db)

        api_client = Mock(spec=ServerAPIClient)
        api_client.base_url = "http://test.example.com"
        llm_client = _make_llm_client()

        ClientConfig.reset()
        config = ClientConfig.defaults()
        config.mini_prophet = MiniProphetConfig(enabled=True, step_limit=3)

        pipeline = AgentPipeline(
            llm_client=llm_client,
            api_client=api_client,
            event_store=event_store,
            client_config=config,
        )

        tick_ctx = _make_tick_context(run_id=run_id, tick_ts=tick_ts)

        with patch.object(
            pipeline.stages[1],
            "_run_agent_for_market",
            return_value=(
                {"submission": {"Yes": 0.65, "No": 0.35}},
                "Moderate evidence for Yes.",
            ),
        ):
            pipeline.execute(tick_ctx, run_id=run_id)

        # Forecast event should exist (stage name is "forecast")
        forecast_events = event_store.get_events(tick_ts, EventType.FORECAST)
        assert len(forecast_events) == 1

        # No search events (mini-prophet replaces the search stage)
        search_events = event_store.get_events(tick_ts, EventType.SEARCH_RESULT)
        assert len(search_events) == 0

        ClientConfig.reset()
