"""Tests for the temporal-reasoning helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from ai_prophet.forecast.temporal import (
    FACTOR_FAR,
    FACTOR_IMMINENT,
    FACTOR_MEDIUM,
    FACTOR_NEAR_TERM,
    FACTOR_UNKNOWN,
    adjusted_shrinkage,
    hours_until_close,
    temporal_context_string,
    temporal_factor,
)

# ---------------------------------------------------------------------------
# hours_until_close
# ---------------------------------------------------------------------------


def test_hours_until_close_iso_with_z_suffix() -> None:
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    target = (now + timedelta(hours=5)).isoformat().replace("+00:00", "Z")
    assert hours_until_close(target, now=now) == pytest.approx(5.0, abs=1e-6)


def test_hours_until_close_iso_with_offset() -> None:
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    target = (now + timedelta(hours=48)).isoformat()  # +00:00 form
    assert hours_until_close(target, now=now) == pytest.approx(48.0, abs=1e-6)


def test_hours_until_close_accepts_datetime() -> None:
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    target = now + timedelta(days=3)
    assert hours_until_close(target, now=now) == pytest.approx(72.0)


def test_hours_until_close_naive_datetime_treated_as_utc() -> None:
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    naive = datetime(2026, 5, 16, 18, 0)  # no tz
    assert hours_until_close(naive, now=now) == pytest.approx(6.0)


def test_hours_until_close_past_returns_negative() -> None:
    now = datetime(2026, 5, 16, 12, 0, tzinfo=UTC)
    past = (now - timedelta(hours=2)).isoformat()
    h = hours_until_close(past, now=now)
    assert h is not None and h < 0


@pytest.mark.parametrize("bad", [None, "", "   ", "not-a-date", 12345, object()])
def test_hours_until_close_unparseable_returns_none(bad) -> None:
    assert hours_until_close(bad) is None


# ---------------------------------------------------------------------------
# temporal_factor — bucket boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hours,expected",
    [
        (0.0, FACTOR_IMMINENT),
        (1.0, FACTOR_IMMINENT),
        (23.99, FACTOR_IMMINENT),
        (24.0, FACTOR_NEAR_TERM),
        (100.0, FACTOR_NEAR_TERM),
        (167.99, FACTOR_NEAR_TERM),
        (168.0, FACTOR_MEDIUM),
        (500.0, FACTOR_MEDIUM),
        (719.99, FACTOR_MEDIUM),
        (720.0, FACTOR_FAR),
        (10000.0, FACTOR_FAR),
    ],
)
def test_temporal_factor_buckets(hours: float, expected: float) -> None:
    assert temporal_factor(hours) == expected


def test_temporal_factor_none_returns_unknown() -> None:
    assert temporal_factor(None) == FACTOR_UNKNOWN


def test_temporal_factor_negative_treats_as_imminent() -> None:
    assert temporal_factor(-1.0) == FACTOR_IMMINENT


# ---------------------------------------------------------------------------
# temporal_context_string
# ---------------------------------------------------------------------------


def test_temporal_context_none_returns_none() -> None:
    assert temporal_context_string(None) is None


def test_temporal_context_imminent_mentions_decisive() -> None:
    s = temporal_context_string(5.0)
    assert s is not None
    assert "TIME HORIZON" in s
    assert "imminent" in s.lower()
    assert "decisive" in s.lower()


def test_temporal_context_near_term_mentions_recent_evidence() -> None:
    s = temporal_context_string(72.0)
    assert s is not None
    assert "near-term" in s.lower()


def test_temporal_context_medium_mentions_moderate_uncertainty() -> None:
    s = temporal_context_string(400.0)
    assert s is not None
    assert "moderate" in s.lower()


def test_temporal_context_far_mentions_substantial_uncertainty() -> None:
    s = temporal_context_string(2000.0)
    assert s is not None
    assert "months out" in s.lower() or "substantial" in s.lower()


def test_temporal_context_negative_says_treated_as_imminent() -> None:
    s = temporal_context_string(-3.0)
    assert s is not None
    assert "imminent" in s.lower()


# ---------------------------------------------------------------------------
# adjusted_shrinkage
# ---------------------------------------------------------------------------


def test_adjusted_shrinkage_imminent_reduces_to_half() -> None:
    # factor=1.0 → base * (1 - 0.5) = base * 0.5
    assert adjusted_shrinkage(0.20, FACTOR_IMMINENT) == pytest.approx(0.10)


def test_adjusted_shrinkage_far_barely_reduces() -> None:
    # factor=0.3 → base * (1 - 0.15) = base * 0.85
    assert adjusted_shrinkage(0.20, FACTOR_FAR) == pytest.approx(0.17)


def test_adjusted_shrinkage_monotonic_in_factor() -> None:
    base = 0.15
    s_imminent = adjusted_shrinkage(base, FACTOR_IMMINENT)
    s_near = adjusted_shrinkage(base, FACTOR_NEAR_TERM)
    s_medium = adjusted_shrinkage(base, FACTOR_MEDIUM)
    s_far = adjusted_shrinkage(base, FACTOR_FAR)
    # Higher factor → smaller shrinkage
    assert s_imminent < s_near < s_medium < s_far <= base


def test_adjusted_shrinkage_clamps_out_of_range_factor() -> None:
    base = 0.15
    # Negative factor clamped to 0 → no reduction.
    assert adjusted_shrinkage(base, -1.0) == pytest.approx(base)
    # Factor > 1 clamped to 1 → 50% reduction.
    assert adjusted_shrinkage(base, 2.0) == pytest.approx(base * 0.5)
