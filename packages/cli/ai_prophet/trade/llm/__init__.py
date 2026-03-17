"""LLM client implementations.

Three client types:

- :class:`AnthropicClient` — Anthropic Messages API (Claude)
- :class:`OpenAIClient` — OpenAI Responses API (GPT-5 family)
- :class:`OpenAICompatibleClient` — OpenAI Chat Completions API
  (xAI, Together, Fireworks, Groq, Ollama, and any compatible provider)
- :class:`GeminiClient` — Google Generative AI REST API

Use :func:`create_llm_client` to get the right client for a provider string.
"""

import logging
import os

from ai_prophet.trade.core.config import LLMConfig

from .anthropic_client import AnthropicClient
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
from .gemini_client import GeminiClient
from .openai_client import OpenAIClient
from .openai_compat_client import OpenAICompatibleClient

logger = logging.getLogger(__name__)

# Known providers that use the OpenAI Chat Completions API.
# Unknown providers also route here — set {PROVIDER}_BASE_URL.
_OPENAI_COMPAT_BASE_URLS: dict[str, str] = {
    "xai": "https://api.x.ai/v1",
    "grok": "https://api.x.ai/v1",
}


def create_llm_client(
    provider: str,
    model: str,
    api_key: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    verbose: bool = False,
    base_url: str | None = None,
    config: LLMConfig | None = None,
) -> LLMClient:
    """Create an LLM client for the given provider.

    Built-in providers:

    =========== =============================== ===========================
    Provider    Client                          Protocol
    =========== =============================== ===========================
    anthropic   :class:`AnthropicClient`        Anthropic Messages API
    openai      :class:`OpenAIClient`           OpenAI Responses API
    gemini      :class:`GeminiClient`           Google GenAI REST API
    google      :class:`GeminiClient`           (alias for gemini)
    xai / grok  :class:`OpenAICompatibleClient` Chat Completions at x.ai
    =========== =============================== ===========================

    Any **other** provider string (e.g. ``together``, ``fireworks``,
    ``groq``, ``ollama``) is assumed to be OpenAI Chat Completions-
    compatible.  The base URL is resolved in order:

    1. Explicit ``base_url`` argument
    2. ``{PROVIDER}_BASE_URL`` env var (e.g. ``TOGETHER_BASE_URL``)
    3. Falls back to ``https://api.openai.com/v1`` with a warning

    Args:
        provider: Provider name
        model: Model identifier
        api_key: API key for the provider
        temperature: Sampling temperature (ignored for OpenAI GPT-5)
        max_tokens: Maximum tokens to generate (defaults from config.yaml)
        verbose: If True, print prompts and responses
        base_url: API base URL override (for OpenAI-compatible providers)
        config: Optional explicit LLM configuration

    Returns:
        Configured LLM client

    Examples:
        create_llm_client("anthropic", "claude-sonnet-4-20250514", api_key="...")
        create_llm_client("openai", "gpt-5.2", api_key="...")
        create_llm_client("xai", "grok-3", api_key="...")
        create_llm_client("gemini", "gemini-2.5-flash", api_key="...")
        create_llm_client("together", "meta-llama/...", api_key="...",
                          base_url="https://api.together.xyz/v1")
    """
    llm_config = config or LLMConfig()

    if temperature is None:
        temperature = llm_config.temperature
    if max_tokens is None:
        max_tokens = llm_config.max_tokens

    provider_lower = provider.lower()
    max_retries = llm_config.max_retries
    retry_delay = llm_config.retry_delay
    http_timeout = llm_config.http_timeout

    # --- Anthropic --------------------------------------------------------
    if provider_lower == "anthropic":
        return AnthropicClient(
            model, api_key, temperature, max_tokens,
            max_retries, retry_delay, verbose=verbose,
        )

    # --- OpenAI Responses API (GPT-5 family) ------------------------------
    if provider_lower == "openai":
        return OpenAIClient(
            model, api_key,
            max_tokens=max_tokens,
            max_retries=max_retries,
            retry_delay=retry_delay,
            verbose=verbose,
            reasoning_effort="medium",
            verbosity="medium",
        )

    # --- Gemini / Google --------------------------------------------------
    if provider_lower in ("gemini", "google"):
        return GeminiClient(
            model, api_key, temperature, max_tokens,
            max_retries, retry_delay, verbose=verbose,
        )

    # --- OpenAI-compatible (xAI, Together, Fireworks, Groq, …) -----------
    resolved_url = _resolve_base_url(provider_lower, base_url)

    return OpenAICompatibleClient(
        model, api_key, temperature, max_tokens,
        max_retries, retry_delay, verbose=verbose,
        base_url=resolved_url,
        http_timeout=http_timeout,
    )


def _resolve_base_url(provider: str, explicit: str | None) -> str:
    """Resolve the Chat Completions base URL for a provider."""
    # 1. Explicit argument
    if explicit:
        return explicit

    # 2. Known provider
    if provider in _OPENAI_COMPAT_BASE_URLS:
        return _OPENAI_COMPAT_BASE_URLS[provider]

    # 3. Environment variable ({PROVIDER}_BASE_URL)
    env_key = f"{provider.upper()}_BASE_URL"
    env_url = os.environ.get(env_key)
    if env_url:
        logger.info(f"Using {env_key}={env_url} for provider '{provider}'")
        return env_url

    # 4. Fall back to OpenAI default — will fail with an auth error that
    #    clearly tells the user something is misconfigured.
    logger.warning(
        f"Unknown provider '{provider}' with no base URL configured. "
        f"Set {env_key} or pass base_url explicitly. "
        f"Falling back to https://api.openai.com/v1"
    )
    return "https://api.openai.com/v1"


# Backwards compat: XAIClient is now OpenAICompatibleClient
XAIClient = OpenAICompatibleClient


__all__ = [
    # Base classes
    "LLMClient",
    "LLMMessage",
    "LLMRequest",
    "LLMResponse",
    "ToolSchema",
    # Implementations
    "AnthropicClient",
    "OpenAIClient",
    "OpenAICompatibleClient",
    "GeminiClient",
    "XAIClient",  # backwards compat alias
    # Factory
    "create_llm_client",
    # Exceptions
    "LLMError",
    "LLMRateLimitError",
    "LLMAuthenticationError",
    "LLMInvalidRequestError",
    "LLMServerError",
]
