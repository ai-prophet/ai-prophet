"""Tests for MiniProphetForecastStage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from ai_prophet.trade.agent.mini_prophet.stage import MiniProphetForecastStage
from ai_prophet.trade.agent.stages.base import StageResult
from ai_prophet.trade.core.config import MiniProphetConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config():
    return MiniProphetConfig(
        enabled=True,
        step_limit=5,
        search_limit=2,
        cost_limit=0.5,
        context_window=4,
    )


@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    client.model = "test-model"
    return client


@pytest.fixture
def stage(mock_llm_client, config):
    return MiniProphetForecastStage(llm_client=mock_llm_client, config=config)


@pytest.fixture
def sample_market():
    m = MagicMock()
    m.market_id = "market_123"
    m.question = "Will it rain tomorrow?"
    m.yes_bid = 0.45
    m.yes_ask = 0.55
    m.yes_mark = 0.50
    m.no_bid = 0.45
    m.no_ask = 0.55
    m.no_mark = 0.50
    m.volume_24h = 1000.0
    m.quote_ts = datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC)
    return m


@pytest.fixture
def tick_ctx(sample_market):
    ctx = MagicMock()
    ctx.tick_ts = datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC)
    ctx.candidates = [sample_market]
    ctx.get_candidate.return_value = sample_market
    return ctx


@pytest.fixture
def review_result():
    return StageResult(
        stage_name="review",
        success=True,
        data={
            "review": [
                {
                    "market_id": "market_123",
                    "priority": 1,
                    "queries": ["rain forecast tomorrow"],
                    "rationale": "Weather market",
                }
            ]
        },
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMiniProphetForecastStage:
    def test_stage_name_is_forecast(self, stage):
        """Stage name must be 'forecast' for ActionStage compatibility."""
        assert stage.name == "forecast"

    def test_missing_review_fails(self, stage, tick_ctx):
        """Stage should fail if review result is missing."""
        result = stage.execute(tick_ctx, {})
        assert result.success is False
        assert "Review stage" in result.error

    def test_failed_review_fails(self, stage, tick_ctx):
        """Stage should fail if review result was unsuccessful."""
        bad_review = StageResult(
            stage_name="review",
            success=False,
            data={},
            error="LLM error",
        )
        result = stage.execute(tick_ctx, {"review": bad_review})
        assert result.success is False

    @patch(
        "ai_prophet.trade.agent.mini_prophet.stage.MiniProphetForecastStage._run_agent_for_market"
    )
    def test_produces_forecast_format(
        self, mock_run, stage, tick_ctx, review_result
    ):
        """Verify output has the expected {p_yes, rationale} shape."""
        mock_run.return_value = (
            {"submission": {"Yes": 0.72, "No": 0.28}},
            "Evidence strongly favors Yes.",
        )

        result = stage.execute(tick_ctx, {"review": review_result})

        assert result.success is True
        forecasts = result.data["forecasts"]
        assert "market_123" in forecasts
        assert forecasts["market_123"]["p_yes"] == 0.72
        assert "Evidence strongly favors" in forecasts["market_123"]["rationale"]

    @patch(
        "ai_prophet.trade.agent.mini_prophet.stage.MiniProphetForecastStage._run_agent_for_market"
    )
    def test_handles_agent_error_gracefully(
        self, mock_run, stage, tick_ctx, review_result
    ):
        """When the agent raises, fallback to market price."""
        mock_run.side_effect = RuntimeError("Agent crashed")

        result = stage.execute(tick_ctx, {"review": review_result})

        assert result.success is True
        forecasts = result.data["forecasts"]
        assert forecasts["market_123"]["p_yes"] == 0.50  # market.yes_mark
        assert "Agent error" in forecasts["market_123"]["rationale"]

    @patch(
        "ai_prophet.trade.agent.mini_prophet.stage.MiniProphetForecastStage._run_agent_for_market"
    )
    def test_handles_missing_submission(
        self, mock_run, stage, tick_ctx, review_result
    ):
        """When agent returns empty result (limits exceeded), use default."""
        mock_run.return_value = ({}, "")

        result = stage.execute(tick_ctx, {"review": review_result})

        assert result.success is True
        # Default p_yes when "Yes" not in submission
        assert result.data["forecasts"]["market_123"]["p_yes"] == 0.5

    def test_skips_unknown_market(self, stage, tick_ctx, review_result):
        """Markets not found in tick context are silently skipped."""
        tick_ctx.get_candidate.return_value = None

        result = stage.execute(tick_ctx, {"review": review_result})

        assert result.success is True
        assert len(result.data["forecasts"]) == 0
