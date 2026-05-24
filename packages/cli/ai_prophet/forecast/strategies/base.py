"""Base types shared by every strategy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Estimate:
    """A single strategy's probability estimate."""

    p_yes: float
    """Estimated probability the event resolves YES, in [0.01, 0.99]."""

    rationale: str
    """2-3 sentence explanation tailored to the strategy."""

    strategy: str
    """Strategy identifier (e.g. ``"evidence_weighted"``)."""

    confidence: float
    """Self-assessed confidence in [0.1, 1.0]. Used as the ensemble weight."""


class Strategy(Protocol):
    """Common protocol for callable strategies."""

    name: str

    def estimate(
        self,
        *,
        title: str,
        description: str | None,
        category: str | None,
        rules: str | None,
        close_time: str | None,
        research: str,
        outcomes: list[str] | None = None,
        temporal_context: str | None = None,
    ) -> Estimate: ...


def clamp_probability(p: float) -> float:
    """Clamp ``p`` to the legal forecast range ``[0.01, 0.99]``."""
    if p != p:  # NaN
        return 0.5
    return max(0.01, min(0.99, float(p)))


def clamp_confidence(c: float) -> float:
    """Clamp confidence to ``[0.1, 1.0]``."""
    if c != c:  # NaN
        return 0.1
    return max(0.1, min(1.0, float(c)))


def failed_estimate(strategy: str, reason: str) -> Estimate:
    """Build an ``Estimate`` representing a failed strategy run."""
    return Estimate(
        p_yes=0.5,
        rationale=f"Strategy {strategy} failed: {reason}",
        strategy=strategy,
        confidence=0.1,
    )
