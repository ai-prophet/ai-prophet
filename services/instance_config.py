"""Helpers for instance-scoped trading deployments."""

from __future__ import annotations

import os
from typing import Mapping

DEFAULT_INSTANCE_NAME = "Haifeng"
TRADING_INSTANCE_NAME_ENV = "TRADING_INSTANCE_NAME"


def normalize_instance_name(instance_name: str | None) -> str:
    value = (instance_name or "").strip()
    return value or DEFAULT_INSTANCE_NAME


def env_suffix(instance_name: str | None) -> str:
    normalized = normalize_instance_name(instance_name)
    return "".join(ch if ch.isalnum() else "_" for ch in normalized.upper())


def get_instance_env(
    key: str,
    instance_name: str | None = None,
    *,
    default: str | None = None,
    env: Mapping[str, str] | None = None,
) -> str | None:
    env_map = os.environ if env is None else env
    suffix = env_suffix(instance_name)
    if f"{key}_{suffix}" in env_map:
        return env_map[f"{key}_{suffix}"]
    return env_map.get(key, default)


def get_current_instance_name(default: str | None = None) -> str:
    return normalize_instance_name(os.getenv(TRADING_INSTANCE_NAME_ENV, default or DEFAULT_INSTANCE_NAME))
