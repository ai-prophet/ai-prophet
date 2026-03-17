"""Base LLM client interface."""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Verbose logger for LLM prompts/responses. Users can enable via
# logging.getLogger("ai_prophet.llm.verbose").setLevel(logging.DEBUG)
_verbose_logger = logging.getLogger("ai_prophet.llm.verbose")


def vprint(msg: str) -> None:
    """Log verbose LLM debug output on the ``ai_prophet.llm.verbose`` logger."""
    _verbose_logger.debug(msg)


# One-time setup: honour PA_VERBOSE env var as a convenience shortcut.
if os.environ.get("PA_VERBOSE"):
    _verbose_logger.setLevel(logging.DEBUG)


@dataclass
class LLMMessage:
    """Single message in a conversation."""
    role: str  # "system", "user", "assistant"
    content: str


@dataclass
class ToolSchema:
    """Schema for structured tool output."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema for parameters


@dataclass
class LLMRequest:
    """LLM generation request."""
    messages: list[LLMMessage]
    temperature: float = 0.7
    max_tokens: int | None = None
    response_format: dict[str, Any] | None = None  # For JSON mode
    tool: ToolSchema | None = None  # For tool/function calling (forced)
    tools: list[ToolSchema] | None = None  # Multi-tool choice (LLM picks)
    raw_messages: list[dict[str, Any]] | None = None  # Provider-native dicts (bypass LLMMessage)


@dataclass
class LLMResponse:
    """LLM generation response."""
    content: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    finish_reason: str
    tool_output: dict[str, Any] | None = None  # Parsed tool call result (first call)
    tool_calls: list[dict[str, Any]] | None = None  # All tool calls: [{name, arguments, id}, ...]


class LLMClient(ABC):
    """Abstract base class for LLM clients.

    Implementations must handle:
    - API authentication
    - Request formatting
    - Response parsing
    - Error handling
    - Token counting
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        verbose: bool = False,
    ):
        """Initialize LLM client.

        Args:
            model: Model identifier (e.g. "gpt-4", "claude-3-5-sonnet-20241022")
            api_key: API key for the provider
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Maximum tokens to generate
            verbose: If True, print prompts and responses
        """
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.verbose = verbose

    def _log_request(self, request: LLMRequest) -> None:
        """Log request messages via verbose logger."""
        for msg in request.messages:
            content = msg.content
            if len(content) > 3000:
                content = content[:3000] + f"\n... ({len(msg.content)} chars total)"
            vprint(f"\n{'='*60}\n[{msg.role.upper()}]\n{'='*60}\n{content}")

    def _log_response(self, response: LLMResponse) -> None:
        """Log response content via verbose logger."""
        if response.tool_output:
            vprint(f"\n{'='*60}\n[RESPONSE] {response.total_tokens} tokens\n{'='*60}")
            vprint(json.dumps(response.tool_output, indent=2))
        elif response.content:
            out = response.content[:2000] + "..." if len(response.content) > 2000 else response.content
            vprint(f"\n{'='*60}\n[RESPONSE] {response.total_tokens} tokens\n{'='*60}\n{out}")

    @abstractmethod
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate completion for a request.

        Args:
            request: LLM generation request

        Returns:
            LLM response with content and metadata

        Raises:
            LLMError: On API errors or failures
        """
        pass

    def close(self) -> None:
        """Release any underlying network resources.

        Concrete clients may hold persistent HTTP connections. Call this when
        you're done with a client (especially in long-running benchmarks).
        """
        return None

    def generate_with_tool(
        self,
        messages: list[LLMMessage],
        tool: ToolSchema,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Generate structured output using tool/function calling.

        This is the preferred method for structured output as it guarantees
        schema compliance via the provider's native tool calling.

        Args:
            messages: Conversation messages
            tool: Tool schema defining expected output structure
            temperature: Override default temperature

        Returns:
            Parsed tool output (dict matching tool schema)

        Raises:
            LLMError: On API errors or failures
        """
        request = LLMRequest(
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=self.max_tokens,
            tool=tool,
        )

        response = self.generate(request)

        if response.tool_output is not None:
            return response.tool_output

        # Fallback: parse content as JSON if tool_output not set
        if not response.content or not response.content.strip():
            raise LLMError(f"Empty response from model - no tool_output and no content. finish_reason={response.finish_reason}")

        try:
            return json.loads(response.content)
        except json.JSONDecodeError as e:
            raise LLMError(f"Failed to parse response as JSON: {e}. Content: {response.content[:500]}") from e

    def generate_json(
        self,
        messages: list[LLMMessage],
        temperature: float | None = None,
        tool: ToolSchema | None = None,
    ) -> dict[str, Any]:
        """Generate JSON-structured output.

        If a tool schema is provided, uses tool calling for guaranteed schema.
        Otherwise falls back to JSON mode with text parsing.

        Args:
            messages: Conversation messages
            temperature: Override default temperature
            tool: Optional tool schema for structured output

        Returns:
            Parsed JSON response

        Raises:
            LLMError: On API errors or failures
            JSONDecodeError: If response is not valid JSON
        """
        # Use tool calling if schema provided
        if tool is not None:
            logger.debug(f"Using tool calling with schema: {tool.name}")
            return self.generate_with_tool(messages, tool, temperature)

        # Fallback: JSON mode with text parsing
        request = LLMRequest(
            messages=messages,
            temperature=temperature or self.temperature,
            max_tokens=self.max_tokens,
            response_format={"type": "json_object"}
        )

        response = self.generate(request)
        return json.loads(response.content)


class LLMError(Exception):
    """Base exception for LLM errors."""
    pass


class LLMRateLimitError(LLMError):
    """Rate limit exceeded."""
    pass


class LLMAuthenticationError(LLMError):
    """Authentication failed."""
    pass


class LLMInvalidRequestError(LLMError):
    """Invalid request."""
    pass


class LLMServerError(LLMError):
    """Server error from provider."""
    pass
