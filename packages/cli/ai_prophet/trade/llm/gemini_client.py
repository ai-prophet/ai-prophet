"""Google Gemini LLM client implementation.

Uses the Google Generative AI REST API.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

import httpx

from .base import (
    LLMAuthenticationError,
    LLMClient,
    LLMError,
    LLMInvalidRequestError,
    LLMMessage,
    LLMRateLimitError,
    LLMRequest,
    LLMResponse,
    LLMServerError,
    ToolSchema,
)

logger = logging.getLogger(__name__)


class GeminiClient(LLMClient):
    """Google Gemini API client.

    Uses the Generative AI REST API directly.

    Supports:
    - Gemini 2.x and 3.x models
    - Tool/function calling for structured output
    - Automatic retries on rate limits

    Example:
        client = GeminiClient("gemini-3-flash-preview", api_key="...")
        response = client.generate(request)
    """

    BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


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
        """Initialize Gemini client.

        Args:
            model: Model identifier (e.g. "gemini-2.0-flash", "gemini-2.5-pro")
            api_key: Google AI API key
            temperature: Sampling temperature
            max_tokens: Maximum tokens to generate
            max_retries: Maximum retry attempts on rate limits
            retry_delay: Initial retry delay in seconds
            verbose: If True, print prompts and responses
        """
        super().__init__(model, api_key, temperature, max_tokens, verbose)
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.http_client = httpx.Client(timeout=120.0)

    def _convert_messages_to_gemini(self, messages: list[LLMMessage]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI-style messages to Gemini format.

        Returns:
            Tuple of (system_instruction, contents)
        """
        system_instruction = None
        contents = []

        for msg in messages:
            if msg.role == "system":
                system_instruction = msg.content
            elif msg.role == "user":
                contents.append({
                    "role": "user",
                    "parts": [{"text": msg.content}]
                })
            elif msg.role == "assistant":
                contents.append({
                    "role": "model",
                    "parts": [{"text": msg.content}]
                })

        return system_instruction, contents

    def _build_tool_config(self, tool: ToolSchema) -> dict:
        """Build Gemini tool configuration from ToolSchema."""
        return {
            "functionDeclarations": [{
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters
            }]
        }

    def generate(self, request: LLMRequest) -> LLMResponse:
        """Generate completion using Gemini API.

        Args:
            request: LLM generation request

        Returns:
            LLM response (with tool_output if tool calling used)

        Raises:
            LLMError: On API errors
        """
        self._log_request(request)

        # Convert messages to Gemini format
        system_instruction, contents = self._convert_messages_to_gemini(request.messages)

        # Build request body (let model use its default temperature)
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {}
        }

        if request.max_tokens or self.max_tokens:
            body["generationConfig"]["maxOutputTokens"] = request.max_tokens or self.max_tokens

        if system_instruction:
            body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        # Add thinking config for Gemini 3 models (use "low" for faster responses)
        if "gemini-3" in self.model:
            body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}

        # Add tool if specified
        if request.tool:
            body["tools"] = [self._build_tool_config(request.tool)]
            # Use ANY mode with allowed function names for strict function calling
            body["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "ANY",
                    "allowedFunctionNames": [request.tool.name]
                }
            }

        # Build URL
        url = f"{self.BASE_URL}/models/{self.model}:generateContent?key={self.api_key}"

        # Retry loop for rate limits
        last_error: Exception | None = None
        prompt_chars = sum(len(p.get("text", "")) for c in contents for p in c.get("parts", []))
        logger.info(
            f"[GEMINI] generate START model={self.model}, "
            f"prompt_chars={prompt_chars}, has_tool={request.tool is not None}"
        )
        call_start = time.monotonic()

        for attempt in range(self.max_retries):
            try:
                attempt_start = time.monotonic()
                response = self.http_client.post(url, json=body)
                attempt_duration = time.monotonic() - attempt_start
                logger.info(
                    f"[GEMINI] HTTP response status={response.status_code} "
                    f"in {attempt_duration:.1f}s (attempt {attempt+1}/{self.max_retries})"
                )

                if response.status_code == 429:
                    last_error = LLMRateLimitError(f"Rate limit exceeded: {response.text}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.warning(f"API rate limit (attempt {attempt + 1}/{self.max_retries}): {response.text[:100]}")
                        time.sleep(delay)
                        continue
                    raise last_error

                if response.status_code == 401 or response.status_code == 403:
                    raise LLMAuthenticationError(f"Authentication failed: {response.text}")

                if response.status_code == 400:
                    raise LLMInvalidRequestError(f"Invalid request: {response.text}")

                if response.status_code >= 500:
                    last_error = LLMServerError(f"Gemini API error: {response.text}")
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2 ** attempt)
                        logger.warning(f"API error (attempt {attempt + 1}/{self.max_retries}): {response.text[:100]}")
                        time.sleep(delay)
                        continue
                    raise last_error

                if response.status_code != 200:
                    raise LLMError(f"Unexpected status {response.status_code}: {response.text}")

                data = response.json()

                # Extract response - handle various Gemini response structures
                candidates = data.get("candidates", [])
                if not candidates:
                    # Check for prompt feedback (content blocked)
                    feedback = data.get("promptFeedback", {})
                    block_reason = feedback.get("blockReason")
                    if block_reason:
                        raise LLMError(f"Gemini blocked request: {block_reason}")
                    logger.warning(f"Gemini returned no candidates: {data}")
                    raise LLMError("Gemini returned no candidates")

                candidate = candidates[0]

                # Check for finish reason issues
                finish_reason = candidate.get("finishReason", "STOP")
                if finish_reason == "SAFETY":
                    safety = candidate.get("safetyRatings", [])
                    raise LLMError(f"Gemini safety filter triggered: {safety}")

                # Gemini occasionally returns MALFORMED_FUNCTION_CALL when the model
                # generates syntactically invalid tool output (e.g. Python-style kwargs
                # instead of JSON). We attempt to salvage the output by parsing the
                # raw text, since the underlying forecast data is usually correct.
                if finish_reason == "MALFORMED_FUNCTION_CALL":
                    finish_msg = candidate.get("finishMessage", "")
                    logger.warning(f"Gemini MALFORMED_FUNCTION_CALL, attempting to parse: {finish_msg[:200]}")
                    salvaged = _try_salvage_malformed_review(finish_msg)
                    if salvaged is not None:
                        usage = data.get("usageMetadata", {})
                        return LLMResponse(
                            content="",
                            model=self.model,
                            prompt_tokens=usage.get("promptTokenCount", 0),
                            completion_tokens=0,
                            total_tokens=usage.get("totalTokenCount", 0),
                            finish_reason="STOP",
                            tool_output=salvaged,
                        )

                content_obj = candidate.get("content", {})
                content_parts = content_obj.get("parts", [])

                # Debug: log raw response structure when verbose
                if self.verbose:
                    logger.info(f"Gemini raw response - finish_reason: {finish_reason}, parts count: {len(content_parts)}")
                    for i, part in enumerate(content_parts):
                        logger.info(f"  Part {i} keys: {list(part.keys())}")

                # Check for tool calls - handle various Gemini response formats
                tool_output = None
                content = ""

                for part in content_parts:
                    # Standard function call format
                    if "functionCall" in part:
                        fc = part["functionCall"]
                        tool_output = fc.get("args", {})
                        logger.debug(f"Tool output from functionCall: {list(tool_output.keys()) if tool_output else 'empty'}")
                    # Alternative: function_call (snake_case)
                    elif "function_call" in part:
                        fc = part["function_call"]
                        tool_output = fc.get("args", {})
                        logger.debug(f"Tool output from function_call: {list(tool_output.keys()) if tool_output else 'empty'}")
                    elif "text" in part:
                        content += part["text"]

                # If no tool output but we have text, try to parse it as JSON
                if tool_output is None and content and request.tool:
                    # Try to extract JSON from text (Gemini sometimes wraps in markdown)
                    json_content = content.strip()
                    if json_content.startswith("```json"):
                        json_content = json_content[7:]
                    if json_content.startswith("```"):
                        json_content = json_content[3:]
                    if json_content.endswith("```"):
                        json_content = json_content[:-3]
                    json_content = json_content.strip()

                    try:
                        tool_output = json.loads(json_content)
                        logger.debug("Parsed tool output from text content")
                        content = ""  # Clear content since we extracted the JSON
                    except json.JSONDecodeError:
                        pass  # Keep as text content

                # If we still have no output and tool was requested, log error with full details
                if tool_output is None and not content and request.tool:
                    error_msg = f"Gemini empty response - finish_reason: {finish_reason}, parts: {content_parts}, raw: {json.dumps(data)[:1000]}"
                    logger.error(error_msg)

                # Get usage metadata
                usage = data.get("usageMetadata", {})
                prompt_tokens = usage.get("promptTokenCount", 0)
                completion_tokens = usage.get("candidatesTokenCount", 0)
                total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)

                finish_reason = candidate.get("finishReason", "STOP")

                llm_response = LLMResponse(
                    content=content,
                    model=self.model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    finish_reason=finish_reason,
                    tool_output=tool_output,
                )

                total_duration = time.monotonic() - call_start
                logger.info(
                    f"[GEMINI] generate OK in {total_duration:.1f}s — "
                    f"tokens: {prompt_tokens}→{completion_tokens} "
                    f"(total={total_tokens}), finish={finish_reason}"
                )
                self._log_response(llm_response)
                if not tool_output and not content:
                    logger.warning(f"Gemini returned empty response. Finish reason: {finish_reason}, Parts: {content_parts}")

                return llm_response

            except (LLMRateLimitError, LLMServerError):
                raise
            except LLMError:
                raise
            except httpx.TimeoutException as e:
                attempt_duration = time.monotonic() - attempt_start
                total_duration = time.monotonic() - call_start
                last_error = LLMServerError(f"Timeout: {e}")
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2 ** attempt)
                    logger.warning(
                        f"[GEMINI] TIMEOUT after {attempt_duration:.1f}s "
                        f"(attempt {attempt + 1}/{self.max_retries}, "
                        f"total elapsed {total_duration:.1f}s): {e}"
                    )
                    time.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"[GEMINI] TIMEOUT on FINAL attempt after {attempt_duration:.1f}s "
                        f"(total elapsed {total_duration:.1f}s, {self.max_retries} attempts exhausted): {e}"
                    )
            except Exception as e:
                raise LLMError(f"Unexpected error: {e}") from e

        raise last_error or LLMError("Request failed after all retries")

    def close(self) -> None:
        """Close underlying HTTP client."""
        try:
            close = getattr(self.http_client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass


def _try_salvage_malformed_review(finish_msg: str) -> dict | None:
    """Try to extract a review array from Gemini's malformed function call output.

    Gemini sometimes emits Python-style kwargs (``submit_review(review=[...])``).
    We bracket-match the JSON array and return ``{"review": [...]}`` if it parses.
    Returns None on failure.
    """
    if "review=" not in finish_msg:
        return None

    try:
        match = re.search(r'review=\s*(\[[\s\S]*)', finish_msg)
        if not match:
            return None

        json_str = match.group(1)
        depth, end = 0, 0
        for i, ch in enumerate(json_str):
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        if end == 0:
            return None

        review_data = json.loads(json_str[:end])
        return {"review": review_data}
    except (json.JSONDecodeError, AttributeError) as e:
        logger.warning(f"Failed to parse malformed function call: {e}")
        return None
