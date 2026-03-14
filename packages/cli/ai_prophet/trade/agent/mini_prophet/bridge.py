"""Bridge between ai-prophet LLMClient and mini-prophet Model protocol."""

from __future__ import annotations

import json
import time
from typing import Any

from ai_prophet.trade.llm.base import LLMClient, LLMRequest, ToolSchema


FORMAT_ERROR_TEMPLATE = "Error: {error}. Please make a valid tool call."


def _build_action(tc: dict[str, Any]) -> dict[str, Any]:
    """Build a mini-prophet action dict from our internal tool-call representation."""
    args = tc["arguments"]
    return {
        "name": tc["name"],
        "arguments": json.dumps(args) if not isinstance(args, str) else args,
        "tool_call_id": tc["id"],
    }


class LLMClientBridge:
    """Wraps an ai-prophet LLMClient to satisfy the mini-prophet Model protocol.

    The mini-prophet agent calls ``model.query(messages, tools)`` where
    *messages* are OpenAI-format dicts and *tools* are OpenAI function-calling
    schemas.  This bridge converts them into an :class:`LLMRequest` with
    ``raw_messages`` / ``tools`` and translates the response back.
    """

    def __init__(self, llm_client: LLMClient) -> None:
        self._client = llm_client
        self.config: dict[str, Any] = {
            "provider": "ai-prophet-bridge",
            "model": llm_client.model,
        }

    # ------------------------------------------------------------------
    # Model protocol
    # ------------------------------------------------------------------

    def query(self, messages: list[dict], tools: list[dict]) -> dict:
        """Send a chat-completion request and return a mini-prophet message."""
        # Convert OpenAI tool schemas → ToolSchema dataclasses
        tool_schemas = _convert_tool_schemas(tools) if tools else None

        # Strip 'extra' key that mini-prophet attaches to messages
        clean_messages = [
            {k: v for k, v in m.items() if k != "extra"}
            for m in messages
        ]

        request = LLMRequest(
            messages=[],
            raw_messages=clean_messages,
            tools=tool_schemas,
            temperature=0.7,
        )

        response = self._client.generate(request)

        # Build mini-prophet response dict
        result: dict[str, Any] = {
            "role": "assistant",
            "content": response.content or "",
        }

        # Enforce single tool call per step (same as LitellmModel).
        # Raises FormatError (InterruptAgentFlow) on 0 or 2+ tool calls,
        # which the agent loop catches and retries.
        from miniprophet.models.utils import parse_single_action

        actions = parse_single_action(
            response.tool_calls,
            FORMAT_ERROR_TEMPLATE,
            _build_action,
        )
        # parse_single_action guarantees exactly 1 action here
        result["tool_calls"] = _rebuild_openai_tool_calls(
            [response.tool_calls[0]]  # type: ignore[index]
        )

        result["extra"] = {
            "actions": actions,
            "cost": 0.0,
            "timestamp": time.time(),
        }

        return result

    def format_message(self, **kwargs: Any) -> dict:
        """Build a plain message dict (pass-through)."""
        return dict(kwargs)

    def format_observation_messages(
        self, message: dict, outputs: list[dict]
    ) -> list[dict]:
        """Format tool results into observation messages."""
        from miniprophet.models.utils import format_observation_messages

        return format_observation_messages(message, outputs)

    def serialize(self) -> dict:
        """Serialize model metadata."""
        return {
            "info": {
                "config": {
                    "model": self._client.model,
                    "bridge": "LLMClientBridge",
                },
            },
        }


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _convert_tool_schemas(tools: list[dict]) -> list[ToolSchema]:
    """Convert OpenAI function-calling tool dicts to ToolSchema dataclasses."""
    schemas = []
    for tool in tools:
        func = tool.get("function", tool)
        schemas.append(
            ToolSchema(
                name=func["name"],
                description=func.get("description", ""),
                parameters=func.get("parameters", {}),
            )
        )
    return schemas


def _rebuild_openai_tool_calls(
    tool_calls: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rebuild OpenAI-format tool_calls from our internal representation."""
    result = []
    for tc in tool_calls:
        args = tc["arguments"]
        result.append({
            "id": tc["id"],
            "type": "function",
            "function": {
                "name": tc["name"],
                "arguments": json.dumps(args) if not isinstance(args, str) else args,
            },
        })
    return result
