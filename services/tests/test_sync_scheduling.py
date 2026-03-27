from __future__ import annotations

from datetime import datetime, timedelta, timezone

from kalshi_sync_service import (
    _sync_defer_until_for_worker,
    _next_sync_boundary,
)

UTC = timezone.utc


def test_sync_defers_in_pre_worker_buffer(monkeypatch):
    worker_boundary = datetime(2026, 3, 27, 0, 0, tzinfo=UTC)
    now = worker_boundary - timedelta(minutes=10)

    monkeypatch.setattr("kalshi_sync_service._worker_poll_interval", lambda instance: 4 * 60 * 60)
    monkeypatch.setattr("kalshi_sync_service._worker_sync_buffer_sec", lambda instance: 15 * 60)
    monkeypatch.setattr("kalshi_sync_service._latest_worker_cycle_state", lambda db, instance: (None, None))

    defer_until, reason = _sync_defer_until_for_worker(object(), "Haifeng", now)

    assert reason == "inside pre-worker buffer window"
    assert defer_until == worker_boundary + timedelta(minutes=15)


def test_sync_defers_while_worker_cycle_is_running(monkeypatch):
    now = datetime(2026, 3, 27, 0, 5, tzinfo=UTC)
    start = now - timedelta(minutes=2)

    monkeypatch.setattr("kalshi_sync_service._worker_poll_interval", lambda instance: 4 * 60 * 60)
    monkeypatch.setattr("kalshi_sync_service._worker_sync_buffer_sec", lambda instance: 15 * 60)
    monkeypatch.setattr("kalshi_sync_service._latest_worker_cycle_state", lambda db, instance: (start, None))

    defer_until, reason = _sync_defer_until_for_worker(object(), "Haifeng", now)

    assert reason == "worker cycle is currently running"
    assert defer_until == now + timedelta(seconds=60)


def test_next_sync_boundary_remains_interval_aligned():
    now = datetime(2026, 3, 27, 0, 7, 11, tzinfo=UTC)
    boundary = _next_sync_boundary(now, 600)
    assert boundary == datetime(2026, 3, 27, 0, 10, 0, tzinfo=UTC)
