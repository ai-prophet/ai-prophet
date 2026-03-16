"""Evaluation module for the forecasting track."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .schemas import Prediction, Submission


def load_submission(path: str | Path) -> Submission:
    """Load and validate a submission file."""
    data = json.loads(Path(path).read_text())
    return Submission.model_validate(data)


def load_actuals(path: str | Path) -> dict[str, float]:
    """Load actual outcomes.

    Expected format: {"market_ticker": resolved_value, ...}
    where resolved_value is 1.0 (YES) or 0.0 (NO).
    """
    data = json.loads(Path(path).read_text())
    return {str(k): float(v) for k, v in data.items()}


def score(predictions: list[Prediction], actuals: dict[str, float]) -> dict[str, Any]:
    """Score predictions against actual outcomes using Brier score.

    Brier score = (1/N) * sum((p_yes - actual)^2)
    Lower is better: 0.0 = perfect, 0.25 = random baseline.
    """
    matched = [p for p in predictions if p.market_ticker in actuals]
    if not matched:
        return {
            "n_predictions": len(predictions),
            "n_matched": 0,
            "brier_score": None,
        }
    brier = sum((p.p_yes - actuals[p.market_ticker]) ** 2 for p in matched) / len(matched)
    return {
        "n_predictions": len(predictions),
        "n_matched": len(matched),
        "brier_score": round(brier, 6),
    }
