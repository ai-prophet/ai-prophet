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


def load_actuals(path: str | Path) -> dict[str, Any]:
    """Load actual outcomes.

    Supported formats:
    - ``{"market_ticker": resolved_value, ...}``. Binary forecasts accept
      1.0/0.0 or Yes/No-style labels. Probability-distribution forecasts accept
      labels, lists, or resolved_outcome-style {"value": [...]} payloads.
    - An event list produced by ``prophet forecast retrieve --include-resolved``.
      Binary forecasts score the first listed outcome as YES, while probability
      distributions score against the resolved market label.
    """
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict):
        return {str(k): v for k, v in data.items()}
    if isinstance(data, list):
        return _actuals_from_events(data)
    raise ValueError("actuals must be a ticker mapping or a list of resolved events")


def _actuals_from_events(events: list[Any]) -> dict[str, dict[str, Any]]:
    """Map resolved event objects by ticker while preserving outcome context."""
    actuals: dict[str, dict[str, Any]] = {}
    for index, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise ValueError(f"event at index {index} must be an object")

        market_ticker = str(event.get("market_ticker") or "").strip()
        if not market_ticker:
            raise ValueError(f"event at index {index} is missing market_ticker")

        resolved = event.get("resolved_outcome")
        if not isinstance(resolved, dict):
            continue
        resolved_values = resolved.get("value")
        if not isinstance(resolved_values, list):
            continue

        outcomes = event.get("outcomes")
        if not isinstance(outcomes, list) or len(outcomes) < 2:
            continue

        actuals[market_ticker] = {
            "outcomes": [str(outcome) for outcome in outcomes],
            "resolved_outcome": {"value": [str(value) for value in resolved_values]},
        }
    return actuals


def score(predictions: list[Prediction], actuals: dict[str, Any]) -> dict[str, Any]:
    """Score predictions against actual outcomes using Brier score.

    Binary forecasts use (p_yes - actual)^2. Probability distributions use the
    multiclass Brier score: sum((p_i - outcome_i)^2) across submitted markets.
    """
    matched = [p for p in predictions if p.market_ticker in actuals]
    if not matched:
        return {
            "n_predictions": len(predictions),
            "n_matched": 0,
            "brier_score": None,
        }
    brier = sum(_prediction_brier(p, actuals[p.market_ticker]) for p in matched) / len(matched)
    return {
        "n_predictions": len(predictions),
        "n_matched": len(matched),
        "brier_score": round(brier, 6),
    }


def _prediction_brier(prediction: Prediction, actual: Any) -> float:
    if prediction.probabilities:
        actual_market = _actual_market(actual)
        probabilities = {p.market: p.probability for p in prediction.probabilities}
        if actual_market not in probabilities:
            raise ValueError(
                f"Actual outcome {actual_market!r} missing from "
                f"{prediction.market_ticker} probabilities"
            )
        return sum(
            (probability - (1.0 if market == actual_market else 0.0)) ** 2
            for market, probability in probabilities.items()
        )

    if prediction.p_yes is None:
        raise ValueError(f"Prediction {prediction.market_ticker} has no probability")
    return (prediction.p_yes - _actual_binary(actual)) ** 2


def _actual_market(actual: Any) -> str:
    if isinstance(actual, dict) and "resolved_outcome" in actual:
        return _actual_market(actual["resolved_outcome"])
    if isinstance(actual, dict) and "value" in actual:
        return _actual_market(actual["value"])
    if isinstance(actual, list):
        if not actual:
            raise ValueError("Actual outcome list is empty")
        return str(actual[0])
    return str(actual)


def _actual_binary(actual: Any) -> float:
    if isinstance(actual, dict) and "resolved_outcome" in actual:
        outcomes = actual.get("outcomes")
        if not isinstance(outcomes, list) or not outcomes:
            raise ValueError("Resolved event actual is missing outcomes")
        resolved = actual["resolved_outcome"]
        resolved_market = _actual_market(resolved)
        return 1.0 if resolved_market == str(outcomes[0]) else 0.0
    if isinstance(actual, dict) and "value" in actual:
        return _actual_binary(actual["value"])
    if isinstance(actual, list):
        if not actual:
            raise ValueError("Actual outcome list is empty")
        return _actual_binary(actual[0])
    if isinstance(actual, bool):
        return 1.0 if actual else 0.0
    if isinstance(actual, (int, float)):
        return float(actual)
    normalized = str(actual).strip().lower()
    if normalized in {"1", "true", "yes", "y"}:
        return 1.0
    if normalized in {"0", "false", "no", "n"}:
        return 0.0
    try:
        return float(normalized)
    except ValueError:
        pass
    raise ValueError(f"Cannot convert actual outcome {actual!r} to binary")
