"""Credentials and deployment config for PA Client.

Secrets (API keys) and deployment overrides (server URL) come from the process
environment. CLI entrypoints may choose to load a `.env` file before building
``Credentials``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Hosted Prophet Arena API — default benchmark endpoint.
DEFAULT_API_URL = "https://api.prophetarena.co"
_PROVIDER_ALIASES = {
    "google": "gemini",
    "grok": "xai",
}


def normalize_provider_name(provider: str) -> str:
    """Return the canonical provider key used for env var lookup."""
    provider_lower = provider.lower()
    return _PROVIDER_ALIASES.get(provider_lower, provider_lower)


def load_dotenv_file(dotenv_path: str | None = None) -> None:
    """Load dotenv data into the process environment for CLI usage."""
    if dotenv_path:
        load_dotenv(dotenv_path)
    else:
        load_dotenv()


@dataclass
class Credentials:
    """API keys and deployment overrides."""

    server_url: str = DEFAULT_API_URL
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    xai_api_key: str | None = None
    brave_api_key: str | None = None
    verbose: bool = False

    @classmethod
    def from_env(cls, dotenv_path: str | None = None) -> Credentials:
        """Load credentials from the current process environment.

        ``dotenv_path`` remains as an explicit opt-in convenience for callers
        that want to load a specific dotenv file before reading env vars.
        """
        if dotenv_path is not None:
            load_dotenv_file(dotenv_path)

        return cls(
            server_url=os.getenv("PA_SERVER_URL", DEFAULT_API_URL),
            anthropic_api_key=os.getenv("ANTHROPIC_API_KEY"),
            openai_api_key=os.getenv("OPENAI_API_KEY"),
            gemini_api_key=(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")),
            xai_api_key=os.getenv("XAI_API_KEY"),
            brave_api_key=os.getenv("BRAVE_API_KEY"),
            verbose=os.getenv("PA_VERBOSE", "").lower() in ("true", "1", "yes"),
        )

    def get_api_key(self, provider: str) -> str | None:
        """Get API key for a provider alias.

        Resolution order:
        1) Known provider aliases from structured fields
        2) Generic {PROVIDER}_API_KEY env var for OpenAI-compatible providers
        """
        keys: dict[str, str | None] = {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "xai": self.xai_api_key,
        }
        provider_name = normalize_provider_name(provider)
        direct = keys.get(provider_name)
        if direct:
            return direct

        # Unknown providers route through OpenAI-compatible client paths.
        # Support provider-specific API keys like TOGETHER_API_KEY.
        env_key = f"{provider_name.upper()}_API_KEY"
        return os.getenv(env_key)

    def has_api_key(self, provider: str) -> bool:
        """True if a provider-specific API key is configured."""
        return self.get_api_key(provider) is not None

    def has_any_llm_key(self) -> bool:
        """True if at least one built-in provider API key is configured."""
        return any(
            [
                self.anthropic_api_key,
                self.openai_api_key,
                self.gemini_api_key,
                self.xai_api_key,
            ]
        )

    def __repr__(self) -> str:
        """Mask secrets in repr."""

        def _mask(v: str | None) -> str:
            return "***" if v else "None"

        return (
            f"Credentials("
            f"server_url={self.server_url!r}, "
            f"anthropic={_mask(self.anthropic_api_key)}, "
            f"openai={_mask(self.openai_api_key)}, "
            f"gemini={_mask(self.gemini_api_key)}, "
            f"xai={_mask(self.xai_api_key)}, "
            f"brave={_mask(self.brave_api_key)}, "
            f"verbose={self.verbose})"
        )


__all__ = [
    "Credentials",
    "DEFAULT_API_URL",
    "load_dotenv_file",
    "normalize_provider_name",
]

