from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from services.api.main import _is_cycle_running


def _row(created_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(created_at=created_at)


def test_cycle_running_true_when_recent_start_has_no_end():
    now = datetime(2026, 3, 27, 8, 30, tzinfo=UTC)

    assert _is_cycle_running(
        _row(now - timedelta(minutes=20)),
        None,
        now=now,
    ) is True


def test_cycle_running_false_when_start_older_than_one_hour():
    now = datetime(2026, 3, 27, 8, 30, tzinfo=UTC)

    assert _is_cycle_running(
        _row(now - timedelta(minutes=61)),
        None,
        now=now,
    ) is False


def test_cycle_running_false_when_newer_start_has_aged_out_past_one_hour():
    now = datetime(2026, 3, 27, 8, 30, tzinfo=UTC)

    assert _is_cycle_running(
        _row(now - timedelta(minutes=70)),
        _row(now - timedelta(minutes=80)),
        now=now,
    ) is False
