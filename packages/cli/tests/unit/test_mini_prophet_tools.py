"""Tests for the trading-specific mini-prophet tools."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from miniprophet.environment.source_board import SourceBoard
from miniprophet.exceptions import Submitted

from ai_prophet.trade.agent.mini_prophet.tools import MarketDataTool, TradingSubmitTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_market():
    m = MagicMock()
    m.question = "Will it rain tomorrow?"
    m.yes_bid = 0.45
    m.yes_ask = 0.55
    m.yes_mark = 0.50
    m.no_bid = 0.45
    m.no_ask = 0.55
    m.no_mark = 0.50
    m.volume_24h = 12345.0
    m.quote_ts = datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC)
    return m


@pytest.fixture
def board():
    return SourceBoard()


# ---------------------------------------------------------------------------
# MarketDataTool
# ---------------------------------------------------------------------------


class TestMarketDataTool:
    def test_schema_is_valid(self, sample_market):
        tool = MarketDataTool(sample_market)
        schema = tool.get_schema()
        assert schema["type"] == "function"
        func = schema["function"]
        assert func["name"] == "get_market_data"
        assert "parameters" in func
        assert func["parameters"]["type"] == "object"

    def test_execute_returns_prices(self, sample_market):
        tool = MarketDataTool(sample_market)
        result = asyncio.run(tool.execute({}))
        output = result["output"]
        assert "Will it rain tomorrow?" in output
        assert "bid=0.450" in output
        assert "ask=0.550" in output
        assert "12345" in output

    def test_name_property(self, sample_market):
        tool = MarketDataTool(sample_market)
        assert tool.name == "get_market_data"


# ---------------------------------------------------------------------------
# TradingSubmitTool
# ---------------------------------------------------------------------------


class TestTradingSubmitTool:
    def test_schema_includes_rationale(self, board):
        tool = TradingSubmitTool(outcomes=["Yes", "No"], board=board)
        schema = tool.get_schema()
        params = schema["function"]["parameters"]
        assert "rationale" in params["properties"]
        assert "rationale" in params["required"]
        assert "probabilities" in params["required"]

    def test_valid_submission_raises_submitted(self, board):
        tool = TradingSubmitTool(outcomes=["Yes", "No"], board=board)
        with pytest.raises(Submitted) as exc_info:
            asyncio.run(tool.execute({
                "probabilities": {"Yes": 0.7, "No": 0.3},
                "rationale": "Strong evidence for Yes.",
            }))
        # Check the exit message
        msg = exc_info.value.messages[0]
        assert msg["extra"]["exit_status"] == "submitted"
        assert msg["extra"]["submission"] == {"Yes": 0.7, "No": 0.3}
        assert msg["extra"]["rationale"] == "Strong evidence for Yes."

    def test_missing_outcome_returns_error(self, board):
        tool = TradingSubmitTool(outcomes=["Yes", "No"], board=board)
        result = asyncio.run(tool.execute({
            "probabilities": {"Yes": 0.7},
            "rationale": "Missing No.",
        }))
        assert result.get("error") is True
        assert "Missing outcomes" in result["output"]

    def test_invalid_probability_returns_error(self, board):
        tool = TradingSubmitTool(outcomes=["Yes", "No"], board=board)
        result = asyncio.run(tool.execute({
            "probabilities": {"Yes": 1.5, "No": -0.5},
            "rationale": "Bad values.",
        }))
        assert result.get("error") is True
        assert "between 0 and 1" in result["output"]

    def test_name_property(self, board):
        tool = TradingSubmitTool(outcomes=["Yes", "No"], board=board)
        assert tool.name == "submit"
