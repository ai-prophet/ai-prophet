"""Temporal reasoning helpers for the ensemble forecaster.

Different time horizons demand different forecasting postures:

* Events resolving within a day reward decisiveness — current evidence is
  effectively the answer; staying near 0.5 just gives up points.
* Events months out reward humility — anything can change before resolution,
  so heavy shrinkage toward 0.5 hedges against overconfidence.

This module exposes three pure helpers that the agent uses to compute a
``temporal_factor`` (close to 1.0 for imminent events, close to 0.3 for far
ones) plus a short natural-language string that strategies inject into their
prompts so the LLM weights its confidence appropriately.

The factor is consumed by :func:`ai_prophet.forecast.ensemble.ensemble_predict`
to scale the base shrinkage; the string is consumed by every strategy and the
deliberation prompt.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

HOURS_PER_DAY = 24.0
HOURS_PER_WEEK = 24.0 * 7  # 168
HOURS_PER_MONTH = 24.0 * 30  # 720

# Bucketed temporal factors. A higher value means "be more decisive."
FACTOR_IMMINENT = 1.0
FACTOR_NEAR_TERM = 0.8
FACTOR_MEDIUM = 0.5
FACTOR_FAR = 0.3

# Used when no close_time is available (treat as medium-term uncertainty).
FACTOR_UNKNOWN = 0.5


def hours_until_close(
    close_time: Any, *, now: datetime | None = None
) -> float | None:
    """Return the number of hours until ``close_time``, or ``None`` if unparseable.

    Accepts:
      * A ``datetime`` object (assumed to be tz-aware UTC if naive).
      * An ISO-8601 string, including the ``Z`` suffix used by Kalshi events.
      * ``None`` / empty / unparseable → ``None``.

    For events already in the past the return value is negative; callers
    typically treat negatives as "imminent" via :func:`temporal_factor`.
    """
    if close_time is None or close_time == "":
        return None

    if isinstance(close_time, datetime):
        target = close_time
    else:
        if not isinstance(close_time, str):
            return None
        text = close_time.strip()
        if not text:
            return None
        # ``fromisoformat`` accepts ``+00:00`` but not ``Z`` until 3.11; we
        # target 3.11+, but the substitution is harmless and future-proof.
        text = text.replace("Z", "+00:00")
        try:
            target = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None

    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)

    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)

    return (target - reference).total_seconds() / 3600.0


def temporal_factor(hours: float | None) -> float:
    """Map ``hours_until_close`` to a decisiveness factor in ``[0.3, 1.0]``.

    Buckets:
      * ``< 24h``: 1.0 (imminent — be decisive)
      * ``< 1 week``: 0.8 (near-term)
      * ``< 1 month``: 0.5 (medium-term)
      * ``>= 1 month``: 0.3 (far future — be uncertain)
      * ``None``: 0.5 (no close_time → treat as medium)
      * ``negative``: 1.0 (already past — about to resolve)
    """
    if hours is None:
        return FACTOR_UNKNOWN
    if hours < 0:
        return FACTOR_IMMINENT
    if hours < HOURS_PER_DAY:
        return FACTOR_IMMINENT
    if hours < HOURS_PER_WEEK:
        return FACTOR_NEAR_TERM
    if hours < HOURS_PER_MONTH:
        return FACTOR_MEDIUM
    return FACTOR_FAR


def temporal_context_string(hours: float | None) -> str | None:
    """Render a one-sentence temporal-context line for strategy prompts.

    Returns ``None`` if ``hours`` is ``None``, so callers can omit the line
    entirely rather than emit a generic placeholder.
    """
    if hours is None:
        return None

    if hours < 0:
        return (
            f"TIME HORIZON: this event closed {abs(hours):.0f} hours ago "
            "(treated as imminent). Current evidence is determinative — "
            "be decisive."
        )
    if hours < HOURS_PER_DAY:
        return (
            f"TIME HORIZON: this event closes in {hours:.0f} hours. "
            "Resolution is imminent — current evidence is likely "
            "determinative. Be decisive; do not hedge to 0.5 without reason."
        )
    if hours < HOURS_PER_WEEK:
        days = hours / 24.0
        return (
            f"TIME HORIZON: this event closes in {days:.1f} days. "
            "Resolution is near-term. Recent evidence weighs heavily; minor "
            "unknowns shouldn't dominate."
        )
    if hours < HOURS_PER_MONTH:
        days = hours / 24.0
        return (
            f"TIME HORIZON: this event closes in {days:.0f} days. "
            "Resolution is weeks away. Account for moderate uncertainty "
            "about intervening developments."
        )
    days = hours / 24.0
    return (
        f"TIME HORIZON: this event closes in {days:.0f} days. "
        "Resolution is months out. Substantial uncertainty about what could "
        "change before then — keep estimates closer to 0.5 unless evidence "
        "is overwhelming."
    )


def adjusted_shrinkage(base_shrinkage: float, factor: float) -> float:
    """Scale ``base_shrinkage`` by the temporal factor.

    Formula: ``base * (1 - factor * 0.5)``. The result is monotonic in
    ``factor``: higher factor (more imminent) → less shrinkage; lower factor
    (further out) → more shrinkage but still capped at ``base``.

    Clamps ``factor`` to ``[0.0, 1.0]`` so out-of-range inputs don't push
    ``base`` outside its sensible operating range.
    """
    f = max(0.0, min(1.0, factor))
    return base_shrinkage * (1.0 - f * 0.5)


__all__ = [
    "FACTOR_IMMINENT",
    "FACTOR_NEAR_TERM",
    "FACTOR_MEDIUM",
    "FACTOR_FAR",
    "FACTOR_UNKNOWN",
    "HOURS_PER_DAY",
    "HOURS_PER_WEEK",
    "HOURS_PER_MONTH",
    "adjusted_shrinkage",
    "hours_until_close",
    "temporal_context_string",
    "temporal_factor",
]
