"""File-based prediction cache for the ensemble forecasting agent.

The evaluation harness polls our endpoint repeatedly with the same events.
Without caching every poll re-runs research + three strategies + deliberation,
burning API credits for no new information. With a small TTL we research each
event once per window and serve repeated calls from disk.

The cache is stored as a single JSON object in the current working directory
(``prediction_cache.json`` by default; overridable via
``PREDICTION_CACHE_PATH``). Each entry is keyed by ``market_ticker`` and has:

    {"p_yes": float, "rationale": str,
     "timestamp": iso8601, "expires_at": iso8601}

All operations are best-effort: a missing file, corrupted JSON, write error,
or unparseable timestamp produces a log line and a soft fall-through rather
than a raised exception. The cache must never be the reason the agent fails.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_CACHE_PATH = Path("prediction_cache.json")
DEFAULT_TTL_HOURS = 6.0

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def cache_enabled() -> bool:
    """Read ``ENABLE_CACHE`` from the env; default ``True``.

    Explicit disables: ``false``, ``0``, ``no``, ``off``, empty string.
    """
    raw = os.environ.get("ENABLE_CACHE", "true").strip().lower()
    return raw not in {"false", "0", "no", "off", ""}


def _ttl_hours() -> float:
    raw = os.environ.get("CACHE_TTL_HOURS", str(DEFAULT_TTL_HOURS))
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        logger.warning("invalid CACHE_TTL_HOURS=%r; using default %s", raw, DEFAULT_TTL_HOURS)
        return DEFAULT_TTL_HOURS


def _cache_path() -> Path:
    raw = os.environ.get("PREDICTION_CACHE_PATH")
    return Path(raw) if raw else DEFAULT_CACHE_PATH


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


def _load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — corruption shouldn't break the agent
        logger.warning("cache.load failed path=%s err=%s; treating as empty", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("cache.load unexpected shape at %s; treating as empty", path)
        return {}
    return data


def _save_cache(path: Path, data: dict[str, dict[str, Any]]) -> None:
    try:
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("cache.save failed path=%s err=%s", path, exc)


def _is_expired(entry: dict[str, Any], now: datetime | None = None) -> bool:
    """An entry is expired if it has no ``expires_at`` or it's in the past."""
    now = now or _now()
    exp = entry.get("expires_at")
    if not isinstance(exp, str):
        return True
    try:
        return datetime.fromisoformat(exp.replace("Z", "+00:00")) <= now
    except (TypeError, ValueError):
        return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_cached_prediction(market_ticker: str | None) -> dict[str, Any] | None:
    """Return the cached **binary** prediction for ``market_ticker`` or ``None``.

    Only entries that contain ``p_yes`` are returned — a multi-outcome entry
    (distribution shape) sharing the same ticker is ignored to avoid handing
    the wrong shape to a binary caller. ``None`` for empty tickers, missing
    entries, expired entries, or wrong-shape entries.
    """
    if not market_ticker:
        return None
    path = _cache_path()
    with _lock:
        cache = _load_cache(path)
        entry = cache.get(market_ticker)
        if entry is None:
            return None
        if _is_expired(entry):
            return None
        if "p_yes" not in entry:
            return None
        return dict(entry)


def cache_prediction(
    market_ticker: str | None,
    p_yes: float,
    rationale: str,
    *,
    ttl_hours: float | None = None,
) -> None:
    """Write a fresh binary entry for ``market_ticker``. No-op if empty."""
    if not market_ticker:
        return
    ttl = ttl_hours if ttl_hours is not None else _ttl_hours()
    now = _now()
    entry = {
        "p_yes": float(p_yes),
        "rationale": str(rationale),
        "timestamp": now.isoformat(),
        "expires_at": (now + timedelta(hours=ttl)).isoformat(),
    }
    path = _cache_path()
    with _lock:
        cache = _load_cache(path)
        cache[market_ticker] = entry
        _save_cache(path, cache)


def get_cached_multi_outcome(market_ticker: str | None) -> dict[str, Any] | None:
    """Return the cached **multi-outcome** prediction for ``market_ticker`` or ``None``.

    Mirror of :func:`get_cached_prediction` for distribution-shape entries.
    Only entries that contain ``probabilities`` (a list of
    ``{market, probability}`` dicts) are returned.
    """
    if not market_ticker:
        return None
    path = _cache_path()
    with _lock:
        cache = _load_cache(path)
        entry = cache.get(market_ticker)
        if entry is None:
            return None
        if _is_expired(entry):
            return None
        probs = entry.get("probabilities")
        if not isinstance(probs, list) or not probs:
            return None
        return dict(entry)


def cache_multi_outcome(
    market_ticker: str | None,
    probabilities: list[dict[str, Any]],
    rationale: str,
    *,
    p_yes: float | None = None,
    ttl_hours: float | None = None,
) -> None:
    """Write a multi-outcome entry.

    ``probabilities`` is the array shape we return to callers:
    ``[{"market": str, "probability": float}, ...]``. ``p_yes`` is the
    headline probability (usually that of ``outcomes[0]``) cached so
    callers can recover the same response shape on hits without
    recomputing it. Defaults to the first entry's probability if
    omitted.
    """
    if not market_ticker:
        return
    if p_yes is None:
        p_yes = (
            float(probabilities[0]["probability"]) if probabilities else 0.5
        )
    ttl = ttl_hours if ttl_hours is not None else _ttl_hours()
    now = _now()
    entry = {
        "p_yes": float(p_yes),
        "probabilities": list(probabilities),
        "rationale": str(rationale),
        "timestamp": now.isoformat(),
        "expires_at": (now + timedelta(hours=ttl)).isoformat(),
    }
    path = _cache_path()
    with _lock:
        cache = _load_cache(path)
        cache[market_ticker] = entry
        _save_cache(path, cache)


def clear_expired() -> int:
    """Remove all expired entries from the cache. Returns count removed."""
    path = _cache_path()
    with _lock:
        cache = _load_cache(path)
        now = _now()
        keys_to_remove = [k for k, v in cache.items() if _is_expired(v, now)]
        if not keys_to_remove:
            return 0
        for k in keys_to_remove:
            del cache[k]
        _save_cache(path, cache)
        return len(keys_to_remove)


def clear_all() -> None:
    """Delete every entry in the cache (removes the file if it exists)."""
    path = _cache_path()
    with _lock:
        if path.exists():
            try:
                path.unlink()
            except Exception as exc:  # noqa: BLE001
                logger.warning("cache.clear_all failed path=%s err=%s", path, exc)


__all__ = [
    "DEFAULT_CACHE_PATH",
    "DEFAULT_TTL_HOURS",
    "cache_enabled",
    "cache_multi_outcome",
    "cache_prediction",
    "clear_all",
    "clear_expired",
    "get_cached_multi_outcome",
    "get_cached_prediction",
]
