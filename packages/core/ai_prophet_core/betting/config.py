"""Configuration helpers for the live betting system."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from dotenv import load_dotenv

from ai_prophet_core.kalshi_config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_BASE_URL_ENV,
    KALSHI_PRIVATE_KEY_B64_ENV,
)

MAX_SPREAD = 1.03

MAX_MARKETS_PER_TICK = 10

KALSHI_BASE_URL = DEFAULT_KALSHI_BASE_URL

LIVE_BETTING_ENABLED_ENV = "LIVE_BETTING_ENABLED"
LIVE_BETTING_DRY_RUN_ENV = "LIVE_BETTING_DRY_RUN"
LIVE_BETTING_DOTENV_PATH_ENV = "LIVE_BETTING_DOTENV_PATH"
LIVE_BETTING_LOAD_DOTENV_ENV = "LIVE_BETTING_LOAD_DOTENV"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    return default


def load_live_betting_dotenv(
    dotenv_path: str | None = None,
    *,
    load_default: bool = False,
    override: bool = False,
) -> None:
    """Load dotenv values for live-betting configuration on demand."""
    if dotenv_path:
        load_dotenv(dotenv_path, override=override)
        return
    if load_default:
        load_dotenv(override=override)


@dataclass(frozen=True)
class KalshiConfig:
    """Explicit Kalshi connection settings."""

    api_key_id: str = ""
    private_key_base64: str = ""
    base_url: str = DEFAULT_KALSHI_BASE_URL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KalshiConfig":
        env_map = os.environ if env is None else env
        private_key_base64 = env_map.get(KALSHI_PRIVATE_KEY_B64_ENV, "")
        return cls(
            api_key_id=env_map.get(KALSHI_API_KEY_ID_ENV, ""),
            private_key_base64=private_key_base64,
            base_url=env_map.get(KALSHI_BASE_URL_ENV, DEFAULT_KALSHI_BASE_URL),
        )


@dataclass(frozen=True)
class LiveBettingSettings:
    """Runtime settings for live betting integration."""

    enabled: bool = False
    paper: bool = True
    kalshi: KalshiConfig = field(default_factory=KalshiConfig)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        dotenv_path: str | None = None,
        load_default_dotenv: bool | None = None,
    ) -> "LiveBettingSettings":
        if env is None:
            resolved_dotenv_path = dotenv_path or os.getenv(LIVE_BETTING_DOTENV_PATH_ENV)
            should_load_default = (
                _parse_bool(os.getenv(LIVE_BETTING_LOAD_DOTENV_ENV), default=False)
                if load_default_dotenv is None
                else load_default_dotenv
            )
            load_live_betting_dotenv(
                dotenv_path=resolved_dotenv_path,
                load_default=should_load_default and resolved_dotenv_path is None,
            )
            env_map: Mapping[str, str] = os.environ
        else:
            env_map = env

        return cls(
            enabled=_parse_bool(env_map.get(LIVE_BETTING_ENABLED_ENV), default=False),
            paper=_parse_bool(env_map.get(LIVE_BETTING_DRY_RUN_ENV), default=True),
            kalshi=KalshiConfig.from_env(env_map),
        )


