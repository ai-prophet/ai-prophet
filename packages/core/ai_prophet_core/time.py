"""Time utilities for tick normalization and validation.

All tick operations use UTC boundaries derived from TICK_INTERVAL_SECONDS
in the ruleset. Currently 15-minute ticks (900s).
"""

from datetime import UTC, datetime, timedelta

from ai_prophet_core.ruleset import TICK_INTERVAL_SECONDS


def normalize_tick(dt: datetime) -> datetime:
    """Normalize datetime to the nearest UTC tick boundary (floor).

    Examples:
        >>> normalize_tick(datetime(2024, 1, 15, 14, 37, 23))
        datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)

    tick_minutes = TICK_INTERVAL_SECONDS // 60
    tick_boundary_minute = (dt.minute // tick_minutes) * tick_minutes
    return dt.replace(minute=tick_boundary_minute, second=0, microsecond=0)


def is_tick_boundary(dt: datetime) -> bool:
    """Check if datetime is exactly on a tick boundary.

    Examples:
        >>> is_tick_boundary(datetime(2024, 1, 15, 14, 15, 0))
        True
        >>> is_tick_boundary(datetime(2024, 1, 15, 14, 7, 0))
        False
    """
    tick_minutes = TICK_INTERVAL_SECONDS // 60
    return (dt.minute % tick_minutes == 0) and dt.second == 0 and dt.microsecond == 0


def get_current_tick() -> datetime:
    """Get current tick boundary (floor of now)."""
    return normalize_tick(datetime.now(UTC))


def get_next_tick(dt: datetime) -> datetime:
    """Get the next tick boundary after dt."""
    return normalize_tick(dt) + timedelta(seconds=TICK_INTERVAL_SECONDS)


def get_previous_tick(dt: datetime) -> datetime:
    """Get the tick boundary strictly before dt."""
    return normalize_tick(dt) - timedelta(seconds=TICK_INTERVAL_SECONDS)
