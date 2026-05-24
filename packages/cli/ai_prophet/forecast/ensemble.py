"""Confidence-weighted ensemble with adaptive shrinkage calibration.

Probabilities are combined in log-odds space (not linear) because linear
averaging is biased near the extremes — and the Brier score penalty grows
quadratically in the extremes, which is where it matters most.

After ensembling we apply adaptive shrinkage toward 0.5:

* When all strategies agree, shrinkage is small — trust the ensemble.
* When strategies disagree, shrinkage is larger — the disagreement itself
  is evidence that we don't know enough to be confident.

The final probability is clamped to ``[0.01, 0.99]`` to stay within the
``Prediction`` schema's allowed range.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass

from .strategies.base import Estimate

DEFAULT_SHRINKAGE = 0.10
"""Maximum shrinkage applied when strategies disagree completely.

Empirically tuned via :mod:`ai_prophet.forecast.calibrate` on a 19-event
resolved sample: a sweep across ``{0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30,
0.40}`` produced a flat minimum of Brier ~0.0976 at both 0.10 and 0.15.
The lower value is preferred because the per-call overconfidence assessment
flagged YES predictions as slightly under-confident (bias ~−0.07), so less
shrinkage helps the agent commit when it has a real read."""

CONFIDENCE_FLOOR = 0.15
"""Estimates with confidence at or below this are treated as failed."""

P_MIN, P_MAX = 0.01, 0.99
"""Schema-mandated probability bounds for ``Prediction.p_yes``."""

# A "spread" of 0.5 (one estimate at 0 and one at 1) means total disagreement.
# We normalize stdev against this so ``agreement`` lands in [0, 1].
_MAX_SPREAD = 0.5


@dataclass
class FinalPrediction:
    """Output of :func:`ensemble_predict`."""

    p_yes: float
    rationale: str
    raw_p_yes: float
    """The pre-shrinkage probability."""
    agreement: float
    """How much strategies agreed (0-1, higher = more agreement)."""
    shrinkage: float
    """Shrinkage factor actually applied."""
    estimates: list[Estimate]
    """The estimates that fed the ensemble (after filtering)."""


def logit(p: float) -> float:
    """Natural-log odds of ``p``."""
    p = max(P_MIN, min(P_MAX, p))
    return math.log(p / (1.0 - p))


def inv_logit(x: float) -> float:
    """Inverse of :func:`logit`. Numerically stable for large |x|."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def calibrate(p: float, shrinkage: float) -> float:
    """Shrink ``p`` toward 0.5 by factor ``shrinkage`` in [0, 1]."""
    s = max(0.0, min(1.0, shrinkage))
    return p * (1.0 - s) + 0.5 * s


def confidence_weighted_ensemble(estimates: Iterable[Estimate]) -> float:
    """Confidence-weighted average of probabilities in log-odds space.

    Falls back to 0.5 if there are no estimates or if all weights are zero.
    """
    estimates = list(estimates)
    if not estimates:
        return 0.5

    total_w = 0.0
    weighted_logit = 0.0
    for est in estimates:
        w = max(0.0, float(est.confidence))
        if w == 0.0:
            continue
        weighted_logit += w * logit(est.p_yes)
        total_w += w

    if total_w == 0.0:
        return 0.5
    return inv_logit(weighted_logit / total_w)


def compute_agreement(estimates: Iterable[Estimate]) -> float:
    """Return an agreement score in [0, 1] based on the spread of estimates.

    Uses the population standard deviation of ``p_yes`` values, normalized
    against the maximum possible spread of 0.5 (one estimate at 0, one at 1).

    * 1.0 means all strategies returned the same probability.
    * 0.0 means strategies are spread across the full range.
    * Single estimates return 1.0 (no disagreement is possible).
    """
    estimates = list(estimates)
    if len(estimates) <= 1:
        return 1.0

    ps = [float(e.p_yes) for e in estimates]
    mean = sum(ps) / len(ps)
    variance = sum((p - mean) ** 2 for p in ps) / len(ps)
    stdev = math.sqrt(variance)
    return max(0.0, min(1.0, 1.0 - stdev / _MAX_SPREAD))


def _format_rationale(
    estimates: list[Estimate],
    raw_p: float,
    final_p: float,
    agreement: float,
    shrinkage: float,
) -> str:
    """Produce a multi-strategy rationale string for the final prediction."""
    header = (
        f"Ensemble p_yes={final_p:.3f} "
        f"(raw={raw_p:.3f}, agreement={agreement:.2f}, shrinkage={shrinkage:.2f})."
    )
    lines = [header, "Per-strategy estimates:"]
    for est in estimates:
        lines.append(
            f"  - {est.strategy}: p={est.p_yes:.3f} conf={est.confidence:.2f} — "
            f"{est.rationale}"
        )
    return "\n".join(lines)


def ensemble_predict(
    estimates: Iterable[Estimate],
    *,
    base_shrinkage: float = DEFAULT_SHRINKAGE,
    temporal_factor: float | None = None,
) -> FinalPrediction:
    """Combine strategy estimates into a final calibrated prediction.

    Steps:
        1. Drop estimates with confidence ≤ :data:`CONFIDENCE_FLOOR`.
        2. Confidence-weighted average in log-odds space.
        3. Apply adaptive shrinkage. If ``temporal_factor`` is provided
           (in ``[0, 1]``, where 1.0 means imminent and 0.3 means far
           future), the base shrinkage is first scaled down by
           ``(1 - temporal_factor * 0.5)`` — imminent events get less
           shrinkage so the agent is more decisive; far events get nearly
           full base shrinkage so the agent hedges. The agreement adjustment
           then multiplies that effective base by ``(1 - agreement * 0.5)``.
        4. Clamp to ``[P_MIN, P_MAX]``.
    """
    estimates = list(estimates)
    usable = [e for e in estimates if e.confidence > CONFIDENCE_FLOOR]

    effective_base = base_shrinkage
    if temporal_factor is not None:
        f = max(0.0, min(1.0, temporal_factor))
        effective_base = base_shrinkage * (1.0 - f * 0.5)

    # If everything was filtered out, fall back to maximum-uncertainty.
    if not usable:
        rationale = (
            "All strategies failed or returned low confidence. "
            "Defaulting to p_yes=0.5."
        )
        if estimates:
            rationale += " Failed estimates:"
            for est in estimates:
                rationale += (
                    f"\n  - {est.strategy}: p={est.p_yes:.3f} "
                    f"conf={est.confidence:.2f} — {est.rationale}"
                )
        return FinalPrediction(
            p_yes=0.5,
            rationale=rationale,
            raw_p_yes=0.5,
            agreement=0.0,
            shrinkage=effective_base,
            estimates=[],
        )

    raw_p = confidence_weighted_ensemble(usable)
    agreement = compute_agreement(usable)
    shrinkage = effective_base * (1.0 - agreement * 0.5)
    calibrated = calibrate(raw_p, shrinkage)
    final_p = max(P_MIN, min(P_MAX, calibrated))

    rationale = _format_rationale(usable, raw_p, final_p, agreement, shrinkage)
    return FinalPrediction(
        p_yes=final_p,
        rationale=rationale,
        raw_p_yes=raw_p,
        agreement=agreement,
        shrinkage=shrinkage,
        estimates=usable,
    )


__all__ = [
    "DEFAULT_SHRINKAGE",
    "CONFIDENCE_FLOOR",
    "FinalPrediction",
    "logit",
    "inv_logit",
    "calibrate",
    "confidence_weighted_ensemble",
    "compute_agreement",
    "ensemble_predict",
]
