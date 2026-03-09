"""OpenAI LLM client implementation using GPT-5.2 Responses API."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

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


class OpenAIClient(LLMClient):
    """OpenAI GPT-5.x client using the Responses API.

    Uses the new Responses API (/v1/responses) for GPT-5 family models.
    Key differences from Chat Completions:
    - Uses 'input' with messages array
    - Uses reasoning.effort instead of temperature
    - Uses text.verbosity for output length control
    - Tool calling uses function type with structured outputs

    Supported models:
    - gpt-5.2: Best for complex reasoning and agentic tasks
    - gpt-5.2-pro: For tough problems requiring harder thinking
    - gpt-5-mini: Cost-optimized reasoning
    - gpt-5-nano: High-throughput simple tasks

    Example:
        client = OpenAIClient("gpt-5.2", api_key="...")
        response = client.generate(request)
    """

    BASE_URL = "https://api.openai.com/v1"

    # Reasoning effort levels (none -> xhigh)
    REASONING_EFFORTS = ["none", "low", "medium", "high", "xhigh"]

    def __init__(
        self,
        model: str,
        api_key: str,
        temperature: float = 0.7,  # Ignored for GPT-5 (uses reasoning effort)
        max_tokens: int | None = None,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        verbose: bool = False,
        reasoning_effort: str = "none",  # none, low, medium, high, xhigh
        verbosity: str = "medium",  # low, medium, high
    ):
        """Initialize OpenAI GPT-5 client.

        Args:
            model: Model identifier (e.g. "gpt-5.2", "gpt-5-mini")
            api_key: OpenAI API key
            temperature: Ignored for GPT-5 (use reasoning_effort instead)
            max_tokens: Maximum output tokens
            max_retries: Maximum retry attempts on rate limits
            retry_delay: Initial retry delay in seconds
            verbose: If True, print prompts and responses
            reasoning_effort: How much the model should reason (none/low/medium/high/xhigh)
            verbosity: Output length control (low/medium/high)
        """
        super().__init__(model, api_key, temperature, max_tokens, verbose)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.reasoning_effort = reasoning_effort
        self.verbosity = verbosity
        self.client = httpx.Client(
            base_url=self.BASE_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=120.0,
        )

    def _add_additional_properties(self, schema: dict) -> dict:
        """Recursively add additionalProperties: false to all object schemas.

        GPT-5 strict mode requires this on all object definitions.
        """
        if not isinstance(schema, dict):
            return schema

        result = dict(schema)

        # If this is an object schema, add additionalProperties: false
        if result.get("type") == "object":
            if "additionalProperties" not in result:
                result["additionalProperties"] = False

        # Recursively process properties
        if "properties" in result:
            result["properties"] = {
                k: self._add_additional_properties(v)
                for k, v in result["properties"].items()
            }

        # Recursively process items (for arrays)
        if "items" in result:
            result["items"] = self._add_additional_properties(result["items"])

        return result

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate completion using GPT-5 Responses API.

        Args:
            request: LLM generation request

        Returns:
            LLM response (with tool_output if tool calling used)

        Raises:
            LLMError: On API errors
        """
        self._log_request(request)

        # Build request body for Responses API
        if request.raw_messages is not None:
            # raw_messages are in OpenAI format — use directly, stripping extra keys
            input_messages = [
                {k: v for k, v in msg.items() if k != "extra"}
                for msg in request.raw_messages
            ]
        else:
            input_messages = [
                {"role": msg.role, "content": msg.content}
                for msg in request.messages
            ]

        body: dict[str, Any] = {
            "model": self.model,
            "input": input_messages,
            "reasoning": {
                "effort": self.reasoning_effort,
            },
            "text": {
                "verbosity": self.verbosity,
            },
        }

        # Add max output tokens if specified
        if request.max_tokens or self.max_tokens:
            body["max_output_tokens"] = request.max_tokens or self.max_tokens

        # Tool preparation
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": self._add_additional_properties(dict(t.parameters)),
                    "strict": True,
                }
                for t in request.tools
            ]
            # No tool_choice — let model choose
        elif request.tool:
            # Recursively add additionalProperties: false to all object schemas for strict mode
            params = self._add_additional_properties(dict(request.tool.parameters))

            body["tools"] = [{
                "type": "function",
                "name": request.tool.name,
                "description": request.tool.description,
                "parameters": params,
                "strict": True,  # Enable structured outputs
            }]
            # Use tool_choice to require the function
            body["tool_choice"] = {
                "type": "function",
                "name": request.tool.name,
            }

        # Retry loop for rate limits and transient errors
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            try:
                response = self.client.post("/responses", json=body)

                # Handle error responses
                if response.status_code == 401:
                    raise LLMAuthenticationError(f"Authentication failed: {response.text}")

                if response.status_code == 429:
                    last_error = LLMRateLimitError(f"Rate limit exceeded: {response.text}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.warning(f"Rate limit hit, retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    raise last_error

                if response.status_code == 400:
                    raise LLMInvalidRequestError(f"Invalid request: {response.text}")

                if response.status_code >= 500:
                    last_error = LLMServerError(f"OpenAI API error: {response.text}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.warning(f"Server error, retrying in {delay}s...")
                        time.sleep(delay)
                        continue
                    raise last_error

                if response.status_code != 200:
                    raise LLMError(f"Unexpected status {response.status_code}: {response.text}")

                data = response.json()

                # Extract response content and tool calls
                tool_calls: list[dict[str, Any]] = []
                content = ""
                finish_reason = "stop"

                # GPT-5 Responses API returns output array
                output = data.get("output", [])

                for item in output:
                    item_type = item.get("type", "")

                    # Handle message output (content is an array of parts)
                    if item_type == "message":
                        item_content = item.get("content", [])
                        if isinstance(item_content, list):
                            for part in item_content:
                                if part.get("type") == "output_text":
                                    content += part.get("text", "")
                        elif isinstance(item_content, str):
                            content += item_content

                    # Handle text output (direct text field)
                    elif item_type == "text":
                        content += item.get("text", "")

                    # Handle function/tool calls
                    elif item_type == "function_call":
                        try:
                            args_raw = item.get("arguments", "{}")
                            parsed_args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                            tool_calls.append({
                                "name": item.get("name", ""),
                                "arguments": parsed_args,
                                "id": item.get("call_id", item.get("id", "")),
                            })
                            logger.debug(f"Tool call: {item.get('name')}")
                        except json.JSONDecodeError as e:
                            logger.warning(f"Failed to parse function arguments: {e}")
                            content = args_raw  # Fall back to raw string

                tool_output = tool_calls[0]["arguments"] if tool_calls else None

                # Get usage stats
                usage = data.get("usage", {})
                prompt_tokens = usage.get("input_tokens", 0)
                completion_tokens = usage.get("output_tokens", 0)
                total_tokens = prompt_tokens + completion_tokens

                # Check for incomplete responses
                status = data.get("status", "completed")
                if status == "incomplete":
                    finish_reason = data.get("incomplete_details", {}).get("reason", "length")

                llm_response = LLMResponse(
                    content=content,
                    model=data.get("model", self.model),
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    finish_reason=finish_reason,
                    tool_output=tool_output,
                    tool_calls=tool_calls or None,
                )

                self._log_response(llm_response)
                return llm_response

            except httpx.TimeoutException:
                last_error = LLMError("Request timed out")
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Timeout, retrying in {delay}s...")
                    time.sleep(delay)
                    continue

            except httpx.RequestError as e:
                last_error = LLMError(f"Request error: {e}")
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(f"Request error, retrying in {delay}s...")
                    time.sleep(delay)
                    continue

            except (LLMAuthenticationError, LLMInvalidRequestError):
                raise

            except Exception as e:
                raise LLMError(f"Unexpected error: {e}") from e

        raise last_error or LLMError("Request failed after all retries")

    def close(self) -> None:
        """Close underlying HTTP client."""
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
