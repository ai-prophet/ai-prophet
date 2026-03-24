"""Anthropic (Claude) LLM client implementation."""

from __future__ import annotations

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
        max_retries: int = 1,  # Changed: No retries, run once only
        retry_delay: float = 1.0,
        verbose: bool = False,
    ):
        """Initialize Anthropic client.

        Args:
            model: Model identifier (e.g. "claude-sonnet-4-20250514")
            api_key: Anthropic API key
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate (required for Claude)
            max_retries: Maximum retry attempts (1 = no retries, run once only)
            retry_delay: Initial retry delay in seconds (unused when max_retries=1)
            verbose: If True, print prompts and responses
        """
        super().__init__(model, api_key, temperature, max_tokens or 4096, verbose)
        self.max_retries = 1  # Force no retries - run once only
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

        # Add tool if specified
        if request.tool:
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

                # Extract response content and tool output
                content = ""
                tool_output = None

                for block in response.content:
                    if block.type == "text":
                        content += block.text
                    elif block.type == "tool_use":
                        tool_output = block.input
                        logger.debug(f"Tool output received: {list(tool_output.keys())}")

                llm_response = LLMResponse(
                    content=content,
                    model=response.model,
                    prompt_tokens=response.usage.input_tokens,
                    completion_tokens=response.usage.output_tokens,
                    total_tokens=response.usage.input_tokens + response.usage.output_tokens,
                    finish_reason=response.stop_reason or "stop",
                    tool_output=tool_output,
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

    def close(self) -> None:
        """Close underlying SDK client if supported."""
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
