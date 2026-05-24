"""Tests for the file-based prediction cache."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from ai_prophet.forecast import cache as cache_mod


@pytest.fixture
def cache_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the cache at a per-test tmp file and return the path."""
    path = tmp_path / "test_cache.json"
    monkeypatch.setenv("PREDICTION_CACHE_PATH", str(path))
    # Default TTL stays at 6h but anchor it to the env so test config is explicit.
    monkeypatch.setenv("CACHE_TTL_HOURS", "6")
    return path


# ---------------------------------------------------------------------------
# cache_enabled()
# ---------------------------------------------------------------------------


def test_cache_enabled_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_CACHE", raising=False)
    assert cache_mod.cache_enabled() is True


@pytest.mark.parametrize("val", ["false", "FALSE", "0", "no", "off", ""])
def test_cache_enabled_disables(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("ENABLE_CACHE", val)
    assert cache_mod.cache_enabled() is False


@pytest.mark.parametrize("val", ["true", "TRUE", "1", "yes", "on", "anything"])
def test_cache_enabled_truthy(monkeypatch: pytest.MonkeyPatch, val: str) -> None:
    monkeypatch.setenv("ENABLE_CACHE", val)
    assert cache_mod.cache_enabled() is True


# ---------------------------------------------------------------------------
# get / set roundtrip
# ---------------------------------------------------------------------------


def test_get_returns_none_when_file_missing(cache_path: Path) -> None:
    assert not cache_path.exists()
    assert cache_mod.get_cached_prediction("ANY") is None


def test_get_returns_none_for_empty_ticker(cache_path: Path) -> None:
    cache_mod.cache_prediction("TICKER", 0.7, "ok")
    assert cache_mod.get_cached_prediction("") is None
    assert cache_mod.get_cached_prediction(None) is None


def test_set_then_get_roundtrip(cache_path: Path) -> None:
    cache_mod.cache_prediction("BTC-150K", 0.42, "research-based")
    got = cache_mod.get_cached_prediction("BTC-150K")
    assert got is not None
    assert got["p_yes"] == pytest.approx(0.42)
    assert got["rationale"] == "research-based"
    assert "timestamp" in got
    assert "expires_at" in got


def test_set_overwrites_previous_entry(cache_path: Path) -> None:
    cache_mod.cache_prediction("X", 0.4, "first")
    cache_mod.cache_prediction("X", 0.7, "second")
    got = cache_mod.get_cached_prediction("X")
    assert got["p_yes"] == pytest.approx(0.7)
    assert got["rationale"] == "second"


def test_set_no_ops_on_empty_ticker(cache_path: Path) -> None:
    cache_mod.cache_prediction("", 0.5, "r")
    cache_mod.cache_prediction(None, 0.5, "r")
    assert not cache_path.exists() or cache_path.read_text() in ("{}", '{\n}', '{\n}\n')


# ---------------------------------------------------------------------------
# TTL / expiry
# ---------------------------------------------------------------------------


def test_expired_entry_returns_none(cache_path: Path) -> None:
    # Write an entry that's already expired by 1 hour.
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    cache_path.write_text(
        json.dumps(
            {
                "STALE": {
                    "p_yes": 0.8,
                    "rationale": "old",
                    "timestamp": past,
                    "expires_at": past,
                }
            }
        ),
        encoding="utf-8",
    )
    assert cache_mod.get_cached_prediction("STALE") is None


def test_entry_with_no_expires_at_returns_none(cache_path: Path) -> None:
    cache_path.write_text(
        json.dumps({"X": {"p_yes": 0.5, "rationale": "r"}}),
        encoding="utf-8",
    )
    assert cache_mod.get_cached_prediction("X") is None


def test_entry_with_malformed_expires_at_returns_none(cache_path: Path) -> None:
    cache_path.write_text(
        json.dumps(
            {
                "X": {
                    "p_yes": 0.5,
                    "rationale": "r",
                    "timestamp": "now",
                    "expires_at": "not-a-date",
                }
            }
        ),
        encoding="utf-8",
    )
    assert cache_mod.get_cached_prediction("X") is None


def test_custom_ttl_hours_overrides_default(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CACHE_TTL_HOURS", "0.001")  # 3.6 seconds
    cache_mod.cache_prediction("SHORT", 0.7, "r")
    got = cache_mod.get_cached_prediction("SHORT")
    assert got is not None
    # The expires_at should be very close to now.
    expires = datetime.fromisoformat(got["expires_at"].replace("Z", "+00:00"))
    delta = (expires - datetime.now(UTC)).total_seconds()
    assert -1 <= delta <= 5  # roughly 3.6 seconds, with slack


def test_explicit_ttl_hours_kwarg_overrides_env(
    cache_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CACHE_TTL_HOURS", "6")
    cache_mod.cache_prediction("X", 0.5, "r", ttl_hours=24)
    got = cache_mod.get_cached_prediction("X")
    expires = datetime.fromisoformat(got["expires_at"].replace("Z", "+00:00"))
    delta_h = (expires - datetime.now(UTC)).total_seconds() / 3600
    assert 23 < delta_h <= 24


# ---------------------------------------------------------------------------
# clear_expired / clear_all
# ---------------------------------------------------------------------------


def test_clear_expired_removes_only_stale_entries(cache_path: Path) -> None:
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    cache_path.write_text(
        json.dumps(
            {
                "OLD": {"p_yes": 0.1, "rationale": "old", "expires_at": past},
                "FRESH": {"p_yes": 0.9, "rationale": "new", "expires_at": future},
            }
        ),
        encoding="utf-8",
    )
    removed = cache_mod.clear_expired()
    assert removed == 1
    on_disk = json.loads(cache_path.read_text())
    assert "OLD" not in on_disk
    assert "FRESH" in on_disk


def test_clear_expired_returns_zero_when_all_fresh(cache_path: Path) -> None:
    cache_mod.cache_prediction("A", 0.5, "r")
    cache_mod.cache_prediction("B", 0.6, "r")
    assert cache_mod.clear_expired() == 0


def test_clear_all_removes_file(cache_path: Path) -> None:
    cache_mod.cache_prediction("A", 0.5, "r")
    assert cache_path.exists()
    cache_mod.clear_all()
    assert not cache_path.exists()
    assert cache_mod.get_cached_prediction("A") is None


def test_clear_all_when_no_file(cache_path: Path) -> None:
    # Should not raise.
    assert not cache_path.exists()
    cache_mod.clear_all()


# ---------------------------------------------------------------------------
# Resilience: corrupted file shouldn't raise
# ---------------------------------------------------------------------------


def test_corrupted_cache_file_treated_as_empty(cache_path: Path) -> None:
    cache_path.write_text("not valid json{{{", encoding="utf-8")
    assert cache_mod.get_cached_prediction("ANY") is None
    # Subsequent write should still succeed and produce a valid file.
    cache_mod.cache_prediction("X", 0.5, "r")
    got = cache_mod.get_cached_prediction("X")
    assert got is not None
    assert got["p_yes"] == pytest.approx(0.5)


def test_non_dict_cache_file_treated_as_empty(cache_path: Path) -> None:
    cache_path.write_text("[1, 2, 3]", encoding="utf-8")
    assert cache_mod.get_cached_prediction("ANY") is None
