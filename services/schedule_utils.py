from __future__ import annotations

from datetime import UTC, datetime


def next_interval_boundary(now: datetime, interval_sec: int, offset_sec: int = 0) -> datetime:
    """Return the next UTC-aligned interval boundary with an optional phase offset."""
    interval = max(1, int(interval_sec))
    offset = int(offset_sec) % interval
    now_ts = int(now.timestamp())
    next_ts = (((now_ts - offset) // interval) + 1) * interval + offset
    return datetime.fromtimestamp(next_ts, tz=UTC)
