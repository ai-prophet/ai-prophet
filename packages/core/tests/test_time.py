"""Tests for time utilities.

Note: These tests adapt to the configured TICK_INTERVAL_SECONDS.
Currently configured for 15-minute ticks (testing mode).
"""

from datetime import UTC, datetime, timedelta, timezone

from ai_prophet_core.ruleset import TICK_INTERVAL_SECONDS
from ai_prophet_core.time import (
    get_current_tick,
    get_next_tick,
    get_previous_tick,
    is_tick_boundary,
    normalize_tick,
)

# Calculate tick interval in minutes for test assertions
TICK_MINUTES = TICK_INTERVAL_SECONDS // 60


class TestNormalizeTick:
    """Tests for normalize_tick function."""

    def test_normalizes_to_tick_boundary(self):
        """Should round down to tick boundary."""
        dt = datetime(2024, 1, 15, 14, 37, 23, 123456, tzinfo=UTC)
        result = normalize_tick(dt)

        # Should be on a valid tick boundary
        assert result.minute % TICK_MINUTES == 0
        assert result.second == 0
        assert result.microsecond == 0
        # For 15-min ticks, 14:37 -> 14:30
        if TICK_MINUTES == 15:
            assert result == datetime(2024, 1, 15, 14, 30, 0, tzinfo=UTC)
        elif TICK_MINUTES == 60:
            assert result == datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)

    def test_already_on_boundary(self):
        """Should return same time if already on boundary."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)
        result = normalize_tick(dt)

        assert result == dt

    def test_adds_utc_timezone_if_naive(self):
        """Should add UTC timezone to naive datetime."""
        dt = datetime(2024, 1, 15, 14, 37, 23)
        result = normalize_tick(dt)

        assert result.tzinfo == UTC
        assert result.minute % TICK_MINUTES == 0

    def test_converts_non_utc_to_utc(self):
        """Should convert non-UTC timezones to UTC."""
        # Create a timezone with offset (e.g., EST = UTC-5)
        est = timezone(timedelta(hours=-5))
        dt = datetime(2024, 1, 15, 9, 37, 23, tzinfo=est)  # 9 AM EST = 2 PM UTC
        result = normalize_tick(dt)

        assert result.tzinfo == UTC
        # 14:37 UTC normalized - depends on tick interval
        assert result.minute % TICK_MINUTES == 0

    def test_determinism(self):
        """Same input should always produce same output."""
        dt = datetime(2024, 1, 15, 14, 37, 23, tzinfo=UTC)

        result1 = normalize_tick(dt)
        result2 = normalize_tick(dt)
        result3 = normalize_tick(dt)

        assert result1 == result2 == result3


class TestIsTickBoundary:
    """Tests for is_tick_boundary function."""

    def test_exact_hour_boundary(self):
        """Hour boundary should always be a valid tick boundary."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)
        assert is_tick_boundary(dt) is True

    def test_quarter_hour_boundaries(self):
        """Test 15-minute boundaries (valid for 15/30/60 min intervals)."""
        for minute in [0, 15, 30, 45]:
            dt = datetime(2024, 1, 15, 14, minute, 0, tzinfo=UTC)
            expected = (minute % TICK_MINUTES == 0)
            assert is_tick_boundary(dt) is expected, f"minute={minute}"

    def test_with_seconds(self):
        """Should return False if seconds are non-zero."""
        dt = datetime(2024, 1, 15, 14, 0, 1, tzinfo=UTC)
        assert is_tick_boundary(dt) is False

    def test_with_microseconds(self):
        """Should return False if microseconds are non-zero."""
        dt = datetime(2024, 1, 15, 14, 0, 0, 1, tzinfo=UTC)
        assert is_tick_boundary(dt) is False

    def test_midnight(self):
        """Midnight should be a valid tick boundary."""
        dt = datetime(2024, 1, 15, 0, 0, 0, tzinfo=UTC)
        assert is_tick_boundary(dt) is True

    def test_naive_datetime(self):
        """Should work with naive datetimes."""
        dt = datetime(2024, 1, 15, 14, 0, 0)
        assert is_tick_boundary(dt) is True


class TestGetCurrentTick:
    """Tests for get_current_tick function."""

    def test_returns_tick_boundary(self):
        """Should always return a datetime on tick boundary."""
        result = get_current_tick()

        assert is_tick_boundary(result) is True
        assert result.tzinfo == UTC

    def test_is_in_past_or_present(self):
        """Should return current or past tick, never future."""
        result = get_current_tick()
        now = datetime.now(UTC)

        assert result <= now

    def test_multiple_calls_stable(self):
        """Multiple calls within same tick should return same value."""
        result1 = get_current_tick()
        result2 = get_current_tick()

        # Should be same if called within same tick
        assert result1 == result2 or (result2 - result1).total_seconds() == TICK_INTERVAL_SECONDS


class TestGetNextTick:
    """Tests for get_next_tick function."""

    def test_returns_next_tick(self):
        """Should return next tick boundary."""
        dt = datetime(2024, 1, 15, 14, 37, 23, tzinfo=UTC)
        result = get_next_tick(dt)

        # Should be exactly TICK_INTERVAL_SECONDS after the normalized tick
        normalized = normalize_tick(dt)
        assert result == normalized + timedelta(seconds=TICK_INTERVAL_SECONDS)

    def test_from_boundary(self):
        """From boundary should return next tick."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)
        result = get_next_tick(dt)

        expected = dt + timedelta(seconds=TICK_INTERVAL_SECONDS)
        assert result == expected

    def test_day_boundary(self):
        """Should handle day boundaries correctly."""
        # Use a time that will cross midnight after adding interval
        dt = datetime(2024, 1, 15, 23, 45, 0, tzinfo=UTC)
        result = get_next_tick(dt)

        # Should wrap to next day if interval crosses midnight
        assert is_tick_boundary(result)
        assert result > dt

    def test_month_boundary(self):
        """Should handle month boundaries correctly."""
        dt = datetime(2024, 1, 31, 23, 45, 0, tzinfo=UTC)
        result = get_next_tick(dt)

        assert is_tick_boundary(result)
        assert result > dt


class TestGetPreviousTick:
    """Tests for get_previous_tick function."""

    def test_returns_previous_tick(self):
        """Should return previous tick boundary."""
        dt = datetime(2024, 1, 15, 14, 37, 23, tzinfo=UTC)
        result = get_previous_tick(dt)

        # Should be exactly TICK_INTERVAL_SECONDS before the normalized tick
        normalized = normalize_tick(dt)
        assert result == normalized - timedelta(seconds=TICK_INTERVAL_SECONDS)

    def test_from_boundary_goes_back(self):
        """From exact boundary should still go back one tick."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)
        result = get_previous_tick(dt)

        expected = dt - timedelta(seconds=TICK_INTERVAL_SECONDS)
        assert result == expected

    def test_day_boundary(self):
        """Should handle day boundaries correctly."""
        dt = datetime(2024, 1, 16, 0, 0, 0, tzinfo=UTC)
        result = get_previous_tick(dt)

        expected = dt - timedelta(seconds=TICK_INTERVAL_SECONDS)
        assert result == expected
        assert is_tick_boundary(result)

    def test_month_boundary(self):
        """Should handle month boundaries correctly."""
        dt = datetime(2024, 2, 1, 0, 0, 0, tzinfo=UTC)
        result = get_previous_tick(dt)

        expected = dt - timedelta(seconds=TICK_INTERVAL_SECONDS)
        assert result == expected
        assert is_tick_boundary(result)


class TestTickInvariants:
    """Tests for important invariants across tick functions."""

    def test_normalize_is_idempotent(self):
        """Normalizing a normalized tick should return same value."""
        dt = datetime(2024, 1, 15, 14, 37, 23, tzinfo=UTC)

        normalized = normalize_tick(dt)
        double_normalized = normalize_tick(normalized)

        assert normalized == double_normalized

    def test_next_previous_roundtrip(self):
        """Going to next then previous should return to start."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)

        next_tick = get_next_tick(dt)
        back_to_start = get_previous_tick(next_tick)

        assert back_to_start == dt

    def test_normalized_is_boundary(self):
        """All normalized ticks should pass is_tick_boundary."""
        test_dates = [
            datetime(2024, 1, 15, 14, 37, 23, tzinfo=UTC),
            datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 0, 1, tzinfo=UTC),
        ]

        for dt in test_dates:
            normalized = normalize_tick(dt)
            assert is_tick_boundary(normalized) is True

    def test_tick_spacing(self):
        """Adjacent ticks should be exactly TICK_INTERVAL_SECONDS apart."""
        dt = datetime(2024, 1, 15, 14, 0, 0, tzinfo=UTC)

        next_tick = get_next_tick(dt)
        diff = (next_tick - dt).total_seconds()

        assert diff == TICK_INTERVAL_SECONDS
