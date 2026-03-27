from __future__ import annotations

from datetime import datetime, timezone

from schedule_utils import next_interval_boundary

UTC = timezone.utc


def test_next_interval_boundary_defaults_to_top_of_hour() -> None:
    now = datetime(2026, 3, 27, 0, 7, 11, tzinfo=UTC)
    boundary = next_interval_boundary(now, 3600)
    assert boundary == datetime(2026, 3, 27, 1, 0, 0, tzinfo=UTC)


def test_next_interval_boundary_supports_half_hour_offset() -> None:
    now = datetime(2026, 3, 27, 0, 7, 11, tzinfo=UTC)
    boundary = next_interval_boundary(now, 3600, 1800)
    assert boundary == datetime(2026, 3, 27, 0, 30, 0, tzinfo=UTC)


def test_next_interval_boundary_rolls_forward_from_exact_offset_boundary() -> None:
    now = datetime(2026, 3, 27, 0, 30, 0, tzinfo=UTC)
    boundary = next_interval_boundary(now, 3600, 1800)
    assert boundary == datetime(2026, 3, 27, 1, 30, 0, tzinfo=UTC)
