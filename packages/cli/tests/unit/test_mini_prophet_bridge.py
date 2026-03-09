"""Tests for the LLMClientBridge (ai-prophet LLMClient → mini-prophet Model)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from ai_prophet.trade.agent.mini_prophet.bridge import (
    LLMClientBridge,
    _convert_tool_schemas,
    _rebuild_openai_tool_calls,
)
from ai_prophet.trade.llm.base import LLMResponse, ToolSchema


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_client():
    client = MagicMock()
    client.model = "test-model"
    return client


@pytest.fixture
def bridge(mock_llm_client):
    return LLMClientBridge(mock_llm_client)


SAMPLE_OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Search the web",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit",
            "description": "Submit forecast",
            "parameters": {
                "type": "object",
                "properties": {
                    "probabilities": {"type": "object"},
                },
                "required": ["probabilities"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConvertToolSchemas:
    def test_converts_openai_dicts_to_tool_schemas(self):
        schemas = _convert_tool_schemas(SAMPLE_OPENAI_TOOLS)
        assert len(schemas) == 2
        assert all(isinstance(s, ToolSchema) for s in schemas)
        assert schemas[0].name == "search"
        assert schemas[1].name == "submit"
        assert schemas[0].description == "Search the web"
        assert "query" in schemas[0].parameters["properties"]


class TestRebuildOpenAIToolCalls:
    def test_rebuilds_correctly(self):
        internal = [
            {"name": "search", "arguments": {"query": "test"}, "id": "call_1"},
        ]
        rebuilt = _rebuild_openai_tool_calls(internal)
        assert len(rebuilt) == 1
        tc = rebuilt[0]
        assert tc["id"] == "call_1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "search"
        assert json.loads(tc["function"]["arguments"]) == {"query": "test"}


class TestBridgeQuery:
    def test_query_returns_actions_with_tool_calls(self, bridge, mock_llm_client):
        """When the LLM returns tool_calls, the bridge returns actions."""
        mock_llm_client.generate.return_value = LLMResponse(
            content="",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            finish_reason="tool_use",
            tool_output={"query": "test"},
            tool_calls=[
                {"name": "search", "arguments": {"query": "test"}, "id": "call_1"},
            ],
        )

        messages = [
            {"role": "system", "content": "You are a forecaster."},
            {"role": "user", "content": "Forecast this market."},
        ]

        result = bridge.query(messages, SAMPLE_OPENAI_TOOLS)

        assert result["role"] == "assistant"
        assert "tool_calls" in result
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["function"]["name"] == "search"

        actions = result["extra"]["actions"]
        assert len(actions) == 1
        assert actions[0]["name"] == "search"
        assert actions[0]["tool_call_id"] == "call_1"
        assert json.loads(actions[0]["arguments"]) == {"query": "test"}

    def test_query_no_tool_calls(self, bridge, mock_llm_client):
        """When the LLM returns text only, actions is empty."""
        mock_llm_client.generate.return_value = LLMResponse(
            content="I need more info.",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            finish_reason="stop",
        )

        result = bridge.query(
            [{"role": "user", "content": "hi"}],
            SAMPLE_OPENAI_TOOLS,
        )

        assert result["role"] == "assistant"
        assert result["content"] == "I need more info."
        assert result["extra"]["actions"] == []

    def test_strips_extra_from_raw_messages(self, bridge, mock_llm_client):
        """The 'extra' key on messages is not passed to the LLM provider."""
        mock_llm_client.generate.return_value = LLMResponse(
            content="ok",
            model="test-model",
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            finish_reason="stop",
        )

        messages = [
            {"role": "user", "content": "test", "extra": {"actions": []}},
        ]

        bridge.query(messages, [])

        call_args = mock_llm_client.generate.call_args
        request = call_args[0][0]
        for msg in request.raw_messages:
            assert "extra" not in msg


class TestBridgeFormatMessage:
    def test_format_message_passthrough(self, bridge):
        msg = bridge.format_message(role="user", content="hello")
        assert msg == {"role": "user", "content": "hello"}


class TestBridgeSerialize:
    def test_serialize_includes_model(self, bridge):
        data = bridge.serialize()
        assert data["info"]["config"]["model"] == "test-model"
        assert data["info"]["config"]["bridge"] == "LLMClientBridge"
