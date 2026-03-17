"""OpenAI-compatible Chat Completions client.

Works with any provider that exposes the ``/v1/chat/completions``
endpoint: xAI, Together, Fireworks, Groq, Ollama, LiteLLM, etc.

This is distinct from :class:`OpenAIClient` which targets the newer
OpenAI *Responses API* (``/v1/responses``) used by GPT-5 family models.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from openai import OpenAI

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


class OpenAICompatibleClient(LLMClient):
    """Client for any OpenAI Chat Completions-compatible API.

    Uses the ``openai`` SDK under the hood with a configurable
    ``base_url``, so it works with xAI, Together, Fireworks, Groq,
    Ollama, and any other provider that implements the same contract.

    Supports:
    - Chat completions (``/v1/chat/completions``)
    - Tool / function calling for structured output
    - JSON mode via ``response_format``
    - Automatic retries with exponential backoff

    Examples:
        # xAI
        client = OpenAICompatibleClient(
            "grok-3", api_key="...",
            base_url="https://api.x.ai/v1",
        )

        # Together
        client = OpenAICompatibleClient(
            "meta-llama/llama-3-70b", api_key="...",
            base_url="https://api.together.xyz/v1",
        )

        # Local Ollama
        client = OpenAICompatibleClient(
            "llama3", api_key="ollama",
            base_url="http://localhost:11434/v1",
        )
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
        base_url: str = "https://api.openai.com/v1",
        http_timeout: float = 120.0,
    ):
        """Initialize OpenAI-compatible client.

        Args:
            model: Model identifier (e.g. "grok-3", "llama-3-70b")
            api_key: API key for the provider
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            max_retries: Maximum retry attempts on rate limits
            retry_delay: Initial retry delay in seconds
            verbose: If True, log prompts and responses
            base_url: Provider API base URL
            http_timeout: Per-request HTTP timeout in seconds
        """
        super().__init__(model, api_key, temperature, max_tokens, verbose)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.base_url = base_url
        # Disable SDK-internal retries so retry behavior is controlled by this wrapper.
        self.client = OpenAI(
            api_key=api_key,
            base_url=self.base_url,
            timeout=http_timeout,
            max_retries=0,
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate completion via OpenAI Chat Completions API.

        Args:
            request: LLM generation request

        Returns:
            LLM response (with tool_output if tool calling was used)

        Raises:
            LLMError: On API errors
        """
        self._log_request(request)

        # Build request kwargs
        if request.raw_messages is not None:
            messages = [
                {k: v for k, v in msg.items() if k != "extra"}
                for msg in request.raw_messages
            ]
        else:
            messages = [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
            ]

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }

        if request.max_tokens:
            kwargs["max_tokens"] = request.max_tokens
        elif self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens

        # Tool calling
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in request.tools
            ]
            # No tool_choice — let model choose
        elif request.tool:
            kwargs["tools"] = [{
                "type": "function",
                "function": {
                    "name": request.tool.name,
                    "description": request.tool.description,
                    "parameters": request.tool.parameters,
                }
            }]
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": request.tool.name},
            }
        elif request.response_format:
            kwargs["response_format"] = request.response_format

        # Retry loop
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(**kwargs)
                choice = response.choices[0]

                # Extract tool calls if present
                tool_calls: list[dict[str, Any]] = []
                content = choice.message.content or ""

                if choice.message.tool_calls:
                    for tc in choice.message.tool_calls:
                        tool_calls.append({
                            "name": tc.function.name,
                            "arguments": json.loads(tc.function.arguments),
                            "id": tc.id,
                        })
                        logger.debug(f"Tool call: {tc.function.name}")

                tool_output = tool_calls[0]["arguments"] if tool_calls else None

                llm_response = LLMResponse(
                    content=content,
                    model=response.model,
                    prompt_tokens=response.usage.prompt_tokens if response.usage else 0,
                    completion_tokens=response.usage.completion_tokens if response.usage else 0,
                    total_tokens=response.usage.total_tokens if response.usage else 0,
                    finish_reason=choice.finish_reason,
                    tool_output=tool_output,
                    tool_calls=tool_calls or None,
                )

                self._log_response(llm_response)
                return llm_response

            except (LLMAuthenticationError, LLMInvalidRequestError):
                raise

            except Exception as e:
                last_error = _classify_error(e, attempt, self.max_retries)
                if isinstance(last_error, (LLMAuthenticationError, LLMInvalidRequestError)):
                    raise last_error from e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Retrying ({attempt + 1}/{self.max_retries}): {e}")
                    time.sleep(delay)
                    continue

        raise last_error or LLMError("Request failed after all retries")

    def close(self) -> None:
        """Close underlying SDK client if supported."""
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


def _classify_error(exc: Exception, attempt: int, max_retries: int) -> LLMError:
    """Map an SDK/HTTP exception to the appropriate LLMError subclass."""
    msg = str(exc).lower()
    if "rate" in msg or "429" in msg:
        return LLMRateLimitError(f"Rate limit exceeded: {exc}")
    if "auth" in msg or "401" in msg:
        return LLMAuthenticationError(f"Authentication failed: {exc}")
    if "bad" in msg or "400" in msg or "invalid" in msg:
        return LLMInvalidRequestError(f"Invalid request: {exc}")
    if any(code in msg for code in ("500", "502", "503")):
        return LLMServerError(f"Server error: {exc}")
    return LLMError(f"Unexpected error: {exc}")
