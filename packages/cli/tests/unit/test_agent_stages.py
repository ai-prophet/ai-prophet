"""Unit tests for agent pipeline stages."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from ai_prophet.trade.agent.stages import ActionStage, SearchStage, StageResult
from ai_prophet.trade.core.tick_context import CandidateMarket


@pytest.fixture
def tick_ctx():
    """Create mock TickContext for testing."""
    ctx = MagicMock()
    ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    ctx.data_asof_ts = datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC)
    ctx.candidate_set_id = "snap_test"
    ctx.submission_deadline = datetime(2026, 1, 20, 6, 55, 0, tzinfo=UTC)
    ctx.server_now = datetime(2026, 1, 20, 5, 45, 0, tzinfo=UTC)
    ctx.candidates = []
    ctx.cash = Decimal("10000.0")
    ctx.equity = Decimal("10000.0")
    return ctx


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client for testing."""
    client = MagicMock()
    return client


def test_stage_result_creation():
    """Test StageResult dataclass."""
    result = StageResult(
        stage_name="test",
        success=True,
        data={"key": "value"},
    )

    assert result.stage_name == "test"
    assert result.success is True
    assert result.data == {"key": "value"}
    assert result.error is None


def test_action_stage_initialization(mock_llm_client):
    """Test ActionStage initialization."""
    stage = ActionStage(llm_client=mock_llm_client, min_size_usd=5.0)

    assert stage.name == "action"
    assert stage.min_size_usd == 5.0


def test_action_stage_no_forecasts(tick_ctx, mock_llm_client):
    """Test action stage with no forecasts."""
    stage = ActionStage(llm_client=mock_llm_client)
    previous_results = {
        "forecast": StageResult(
            stage_name="forecast",
            success=True,
            data={"forecasts": {}}
        )
    }

    result = stage.execute(tick_ctx, previous_results)

    assert result.success is True
    assert result.data["intents"] == []


def test_search_stage_without_search_client_returns_explicit_no_search_summary():
    """Search stage should not fabricate evidence when search is unavailable."""
    llm_client = MagicMock()
    stage = SearchStage(
        llm_client=llm_client,
        search_client=None,
        max_queries_per_market=1,
        max_results_per_query=3,
    )

    tick_ctx = MagicMock()
    tick_ctx.candidates = [
        CandidateMarket(
            market_id="market_1",
            question="Will X happen?",
            description="Description",
            resolution_time=datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC),
        )
    ]

    previous_results = {
        "review": StageResult(
            stage_name="review",
            success=True,
            data={
                "review": [
                    {
                        "market_id": "market_1",
                        "priority": 80,
                        "queries": ["latest evidence for X"],
                        "rationale": "High information value",
                    }
                ]
            },
        )
    }

    result = stage.execute(tick_ctx, previous_results)

    assert result.success is True
    summary = result.data["summaries"]["market_1"]
    assert "No external web evidence was retrieved" in summary["summary"]
    assert summary["key_points"] == []
    assert summary["open_questions"]
    llm_client.generate_json.assert_not_called()


def test_action_stage_hold_recommendation(tick_ctx, mock_llm_client):
    """Test action stage with LLM returning HOLD generates no intent.

    Note: Forecasts now only contain p_yes + rationale.
    The action stage calls the LLM to get recommendation + size_usd.
    """
    # Configure LLM to return HOLD decision
    mock_llm_client.generate_json.return_value = {
        "recommendation": "HOLD",
        "size_usd": 0,
        "rationale": "No edge - market price matches forecast"
    }

    stage = ActionStage(llm_client=mock_llm_client)

    # Add mock market to tick_ctx
    tick_ctx.candidates = [
        CandidateMarket(
            market_id="market_1",
            question="Will X happen?",
            description="Description",
            resolution_time=datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC)
        )
    ]

    # Forecasts now only have p_yes + rationale (no recommendation/size_usd)
    previous_results = {
        "forecast": StageResult(
            stage_name="forecast",
            success=True,
            data={
                "forecasts": {
                    "market_1": {
                        "p_yes": 0.50,
                        "rationale": "Uncertain outcome"
                    }
                }
            }
        )
    }

    result = stage.execute(tick_ctx, previous_results)

    assert result.success is True
    assert len(result.data["intents"]) == 0
    # Verify LLM was called for trade decision
    assert mock_llm_client.generate_json.called


def test_action_stage_size_below_minimum(mock_llm_client):
    """Test action stage skips trades with size below minimum.

    Note: The LLM returns a small size, which is filtered out by min_size_usd.
    """
    # Configure LLM to return small size
    mock_llm_client.generate_json.return_value = {
        "recommendation": "BUY_YES",
        "size_usd": 5.0,  # Below $10 minimum
        "rationale": "Small edge detected"
    }

    stage = ActionStage(llm_client=mock_llm_client, min_size_usd=10.0)  # Require at least $10

    tick_ctx = MagicMock()
    tick_ctx.run_id = "test_run"
    tick_ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    tick_ctx.cash = Decimal("10000.0")
    tick_ctx.candidates = [
        CandidateMarket(
            market_id="market_1",
            question="Will X happen?",
            description="Description",
            resolution_time=datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC)
        )
    ]

    # Forecasts now only have p_yes + rationale
    previous_results = {
        "forecast": StageResult(
            stage_name="forecast",
            success=True,
            data={
                "forecasts": {
                    "market_1": {
                        "p_yes": 0.58,
                        "rationale": "Slightly bullish"
                    }
                }
            }
        )
    }

    result = stage.execute(tick_ctx, previous_results)

    assert result.success is True
    assert len(result.data["intents"]) == 0  # No intent due to size below minimum


def test_action_stage_missing_forecast_stage(mock_llm_client):
    """Test action stage fails gracefully when forecast stage missing."""
    stage = ActionStage(llm_client=mock_llm_client)
    ctx = MagicMock()
    ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    ctx.candidates = []

    result = stage.execute(ctx, {})

    assert result.success is False
    assert "Forecast stage result not found" in result.error


def test_action_stage_missing_llm_client():
    """Test action stage requires LLM client."""
    stage = ActionStage(llm_client=None)
    ctx = MagicMock()
    ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    ctx.candidates = []

    previous_results = {
        "forecast": StageResult(
            stage_name="forecast",
            success=True,
            data={"forecasts": {"market_1": {"p_yes": 0.6, "rationale": "Test"}}}
        )
    }

    result = stage.execute(ctx, previous_results)

    assert result.success is False
    assert "LLM client required" in result.error


def test_action_stage_generates_buy_yes_intent(mock_llm_client):
    """Test action stage generates BUY YES intent when LLM recommends it."""
    # Configure LLM to return BUY_YES decision
    mock_llm_client.generate_json.return_value = {
        "recommendation": "BUY_YES",
        "size_usd": 100.0,
        "rationale": "Strong edge detected"
    }

    stage = ActionStage(llm_client=mock_llm_client, min_size_usd=1.0)

    tick_ctx = MagicMock()
    tick_ctx.run_id = "test_run"
    tick_ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    tick_ctx.cash = Decimal("10000.0")
    tick_ctx.candidates = [
        CandidateMarket(
            market_id="market_1",
            question="Will X happen?",
            description="Description",
            resolution_time=datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC)
        )
    ]

    previous_results = {
        "forecast": StageResult(
            stage_name="forecast",
            success=True,
            data={
                "forecasts": {
                    "market_1": {
                        "p_yes": 0.75,  # Higher than market ask of 0.55
                        "rationale": "Strong bullish signals"
                    }
                }
            }
        )
    }

    result = stage.execute(tick_ctx, previous_results)

    assert result.success is True
    assert len(result.data["intents"]) == 1

    intent = result.data["intents"][0]
    assert intent["action"] == "BUY"
    assert intent["side"] == "YES"
    assert intent["market_id"] == "market_1"
    assert float(intent["shares"]) > 0
