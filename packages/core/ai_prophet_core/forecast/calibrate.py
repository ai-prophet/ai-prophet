"""Post-hoc calibration for forecast submissions.

A submission's overall Brier score doesn't tell you WHERE you're
miscalibrated. The agent might be systematically over-confident in the
0.7-0.8 bucket while perfectly calibrated elsewhere, or under-predict
YES in low-probability buckets.

This module does the binning analysis (`fit_calibration`) and lets you
apply the correction back to a new submission (`apply_calibration`).
Useful as the eval window progresses and you accumulate resolved data:
fit on what's resolved so far, apply to your live forecasts going
forward.

Implementation is intentionally simple: bucket the predictions by
predicted probability, compute the actual yes-rate per bucket, and use
that as the corrected probability for predictions landing in that
bucket. No scipy/sklearn dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import Prediction


CALIBRATION_VERSION = 1


def fit_calibration(
    predictions: list[Prediction],
    actuals: dict[str, float],
    *,
    n_bins: int = 10,
) -> list[dict[str, Any]]:
    """Bin predictions and compute the actual yes-rate per bucket.

    Args:
        predictions: List of Prediction objects (.market_ticker, .p_yes).
        actuals: {market_ticker: 0.0 or 1.0}.
        n_bins: Number of equal-width probability buckets in [0, 1).

    Returns:
        List of bucket dicts (only buckets with samples are included):
        {
          "bucket_lo": float,   # inclusive
          "bucket_hi": float,   # exclusive (except the last)
          "n": int,             # samples in bucket
          "mean_p": float,      # mean predicted probability
          "mean_actual": float, # observed yes rate
        }
    """
    if n_bins < 2:
        raise ValueError("n_bins must be >= 2")

    buckets: list[list[tuple[float, float]]] = [[] for _ in range(n_bins)]
    for pred in predictions:
        actual = actuals.get(pred.market_ticker)
        if actual is None:
            continue
        p = max(0.0, min(0.9999, float(pred.p_yes)))
        idx = min(n_bins - 1, int(p * n_bins))
        buckets[idx].append((p, float(actual)))

    table: list[dict[str, Any]] = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            continue
        n = len(bucket)
        mean_p = sum(b[0] for b in bucket) / n
        mean_actual = sum(b[1] for b in bucket) / n
        table.append(
            {
                "bucket_lo": round(i / n_bins, 4),
                "bucket_hi": round((i + 1) / n_bins, 4),
                "n": n,
                "mean_p": round(mean_p, 5),
                "mean_actual": round(mean_actual, 5),
            }
        )
    return table


def apply_calibration(p_yes: float, table: list[dict[str, Any]]) -> float:
    """Apply a fitted calibration to a single predicted probability.

    Step-function mapping: predictions landing in a bucket are replaced
    by that bucket's observed yes rate. Buckets with no data are passed
    through unchanged (we'd rather under-correct than over-correct on
    sparse data).
    """
    if not table:
        return p_yes
    p = max(0.0, min(0.9999, float(p_yes)))
    for bucket in table:
        lo = float(bucket["bucket_lo"])
        hi = float(bucket["bucket_hi"])
        # Last bucket: include the upper bound
        if hi >= 0.9999:
            if p >= lo:
                return _clamp(float(bucket["mean_actual"]))
        elif lo <= p < hi:
            return _clamp(float(bucket["mean_actual"]))
    return p_yes


def _clamp(p: float) -> float:
    return max(0.01, min(0.99, p))


def save_calibration(table: list[dict[str, Any]], path: str | Path) -> None:
    """Persist a calibration table to disk."""
    payload = {"version": CALIBRATION_VERSION, "buckets": table}
    Path(path).write_text(json.dumps(payload, indent=2))


def load_calibration(path: str | Path) -> list[dict[str, Any]]:
    """Load a calibration table from disk."""
    data = json.loads(Path(path).read_text())
    if data.get("version") != CALIBRATION_VERSION:
        raise ValueError(
            f"Unsupported calibration version {data.get('version')}; expected {CALIBRATION_VERSION}"
        )
    return data["buckets"]
