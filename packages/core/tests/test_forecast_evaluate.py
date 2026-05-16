from __future__ import annotations

import json

import pytest
from ai_prophet_core.forecast.evaluate import load_actuals, score
from ai_prophet_core.forecast.schemas import Prediction


def test_score_binary_prediction():
    result = score(
        [Prediction(market_ticker="binary-task", p_yes=0.7)],
        {"binary-task": 1.0},
    )

    assert result["n_predictions"] == 1
    assert result["n_matched"] == 1
    assert result["brier_score"] == 0.09


def test_score_probability_distribution_prediction():
    result = score(
        [
            Prediction(
                market_ticker="multi-task",
                probabilities=[
                    {"market": "Pittsburgh", "probability": 0.68},
                    {"market": "Atlanta", "probability": 0.32},
                ],
            )
        ],
        {"multi-task": {"value": ["Pittsburgh"]}},
    )

    assert result["n_predictions"] == 1
    assert result["n_matched"] == 1
    assert result["brier_score"] == 0.2048


def test_load_actuals_accepts_direct_mapping(tmp_path):
    actuals_path = tmp_path / "actuals.json"
    actuals_path.write_text(json.dumps({"A": 1, "B": "0.0"}))

    actuals = load_actuals(actuals_path)
    assert actuals == {"A": 1, "B": "0.0"}

    result = score(
        [Prediction(market_ticker="A", p_yes=0.7), Prediction(market_ticker="B", p_yes=0.3)],
        actuals,
    )
    assert result["brier_score"] == 0.09


def test_score_binary_prediction_with_resolved_event_list_actuals(tmp_path):
    actuals_path = tmp_path / "resolved-events.json"
    actuals_path.write_text(
        json.dumps(
            [
                {
                    "market_ticker": "YES-WON",
                    "outcomes": ["Yes", "No"],
                    "resolved_outcome": {"value": ["Yes"]},
                },
                {
                    "market_ticker": "YES-LOST",
                    "outcomes": ["Team A", "Team B"],
                    "resolved_outcome": {"value": ["Team B"]},
                },
                {
                    "market_ticker": "MULTI-WINNER",
                    "outcomes": ["Contestant A", "Contestant B", "Contestant C"],
                    "resolved_outcome": {"value": ["Contestant C", "Contestant A"]},
                },
                {
                    "market_ticker": "UNRESOLVED",
                    "outcomes": ["Yes", "No"],
                    "resolved_outcome": None,
                },
            ]
        )
    )

    result = score(
        [
            Prediction(market_ticker="YES-WON", p_yes=0.8),
            Prediction(market_ticker="YES-LOST", p_yes=0.3),
        ],
        load_actuals(actuals_path),
    )

    assert result["n_predictions"] == 2
    assert result["n_matched"] == 2
    assert result["brier_score"] == 0.065


def test_score_distribution_prediction_with_resolved_event_list_actuals(tmp_path):
    actuals_path = tmp_path / "resolved-events.json"
    actuals_path.write_text(
        json.dumps(
            [
                {
                    "market_ticker": "MULTI-WINNER",
                    "outcomes": ["Pittsburgh", "Atlanta"],
                    "resolved_outcome": {"value": ["Pittsburgh"]},
                },
            ]
        )
    )

    result = score(
        [
            Prediction(
                market_ticker="MULTI-WINNER",
                probabilities=[
                    {"market": "Pittsburgh", "probability": 0.68},
                    {"market": "Atlanta", "probability": 0.32},
                ],
            )
        ],
        load_actuals(actuals_path),
    )

    assert result["n_predictions"] == 1
    assert result["n_matched"] == 1
    assert result["brier_score"] == 0.2048


def test_load_actuals_rejects_unknown_shape(tmp_path):
    actuals_path = tmp_path / "actuals.json"
    actuals_path.write_text(json.dumps("not actuals"))

    with pytest.raises(ValueError, match="ticker mapping or a list"):
        load_actuals(actuals_path)
