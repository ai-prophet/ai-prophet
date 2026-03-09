"""Anthropic (Claude) LLM client implementation."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import anthropic
from anthropic import Anthropic

from .base import (
    LLMAuthenticationError,
    LLMClient,
    LLMError,
    LLMInvalidRequestError,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMServerError,
)

logger = logging.getLogger(__name__)


class AnthropicClient(LLMClient):
    """Anthropic (Claude) API client.

    Supports:
    - Claude 3.5, Claude 4, and newer models
    - Automatic system message extraction
    - Tool calling for structured output
    - Automatic retries with exponential backoff

    Example:
        client = AnthropicClient("claude-sonnet-4-20250514", api_key="...")
        response = client.generate(request)
    """


    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        verbose: bool = False,
    ):
        """Initialize Anthropic client.

        Args:
            model: Model identifier (e.g. "claude-sonnet-4-20250514")
            api_key: Anthropic API key
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate (required for Claude)
            max_retries: Maximum retry attempts on rate limits
            retry_delay: Initial retry delay in seconds
            verbose: If True, print prompts and responses
        """
        super().__init__(model, api_key, temperature, max_tokens or 4096, verbose)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.client = Anthropic(api_key=api_key)

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate completion using Anthropic API.

        Supports tool calling for structured output when request.tool is set.

        Args:
            request: LLM generation request

        Returns:
            LLM response (with tool_output if tool calling used)

        Raises:
            LLMError: On API errors
        """
        self._log_request(request)

        # Message preparation
        if request.raw_messages is not None:
            system_message, messages = self._convert_openai_messages(request.raw_messages)
        else:
            # Extract system message (Anthropic requires separate system param)
            system_message = None
            messages = []

            for msg in request.messages:
                if msg.role == "system":
                    system_message = msg.content
                else:
                    messages.append({"role": msg.role, "content": msg.content})

        # Build API call kwargs (let model use its default temperature)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": request.max_tokens or self.max_tokens,
        }

        if system_message:
            kwargs["system"] = system_message

        # Tool preparation
        if request.tools:
            kwargs["tools"] = [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.parameters,
                }
                for t in request.tools
            ]
            # No tool_choice — let model choose
        elif request.tool:
            kwargs["tools"] = [{
                "name": request.tool.name,
                "description": request.tool.description,
                "input_schema": request.tool.parameters,
            }]
            kwargs["tool_choice"] = {"type": "tool", "name": request.tool.name}

        # Retry loop for rate limits
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            logger.debug(f"LLM API call attempt {attempt+1}/{self.max_retries}")
            try:
                response = self.client.messages.create(**kwargs)

                # Extract response content and tool calls
                content = ""
                tool_calls: list[dict[str, Any]] = []

                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_calls.append({
                            "name": block.name,
                            "arguments": block.input,
                            "id": block.id,
                        })
                        logger.debug(f"Tool call: {block.name}")

                tool_output = tool_calls[0]["arguments"] if tool_calls else None

                llm_response = LLMResponse(
                    content=content,
                    model=response.model,
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                    finish_reason=response.stop_reason or "stop",
                    tool_output=tool_output,
                    tool_calls=tool_calls or None,
                )

                self._log_response(llm_response)
                return llm_response

            except anthropic.RateLimitError as e:
                logger.warning(f"Rate limit (attempt {attempt+1}/{self.max_retries}): {e}")
                last_error = LLMRateLimitError(f"Rate limit exceeded: {e}")
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue

            except anthropic.AuthenticationError as e:
                raise LLMAuthenticationError(f"Authentication failed: {e}") from e

            except anthropic.BadRequestError as e:
                raise LLMInvalidRequestError(f"Invalid request: {e}") from e

            except anthropic.APIError as e:
                logger.warning(f"API error (attempt {attempt+1}/{self.max_retries}): {e}")
                last_error = LLMServerError(f"Anthropic API error: {e}")
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue

            except Exception as e:
                raise LLMError(f"Unexpected error: {e}") from e

        raise last_error or LLMError("Request failed after all retries")

    @staticmethod
    def _convert_openai_messages(
        raw: list[dict[str, Any]],
    ) -> tuple[str | None, list[dict[str, Any]]]:
        """Convert OpenAI-format messages to Anthropic format.

        Returns (system_message, messages) where messages are Anthropic-native.
        Adjacent same-role messages are merged as required by the Anthropic API.
        """
        system_parts: list[str] = []
        converted: list[dict[str, Any]] = []

        for msg in raw:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                if content:
                    system_parts.append(content)
                continue

            if role == "tool":
                # Tool result → user message with tool_result content block
                entry: dict[str, Any] = {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.get("tool_call_id", ""),
                            "content": content if isinstance(content, str) else json.dumps(content),
                        }
                    ],
                }
                converted.append(entry)
                continue

            if role == "assistant" and msg.get("tool_calls"):
                # Assistant with tool_calls → tool_use content blocks
                blocks: list[dict[str, Any]] = []
                if content:
                    blocks.append({"type": "text", "text": content})
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    args_raw = func.get("arguments", "{}")
                    args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                    blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": args,
                    })
                converted.append({"role": "assistant", "content": blocks})
                continue

            # Regular user/assistant text message
            if role in ("user", "assistant"):
                converted.append({"role": role, "content": content})

        # Merge adjacent same-role messages (Anthropic requirement)
        merged: list[dict[str, Any]] = []
        for entry in converted:
            if merged and merged[-1]["role"] == entry["role"]:
                prev_content = merged[-1]["content"]
                cur_content = entry["content"]
                # Normalize both to list-of-blocks form
                if isinstance(prev_content, str):
                    prev_content = [{"type": "text", "text": prev_content}]
                if isinstance(cur_content, str):
                    cur_content = [{"type": "text", "text": cur_content}]
                merged[-1]["content"] = prev_content + cur_content
            else:
                merged.append(entry)

        system_message = "\n\n".join(system_parts) if system_parts else None
        return system_message, merged

    def close(self) -> None:
        """Close underlying SDK client if supported."""
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
