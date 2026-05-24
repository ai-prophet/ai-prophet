"""Tests for the calibration tool's pure helpers and top-level orchestration."""

from __future__ import annotations

import pytest
from ai_prophet.forecast.calibrate import (
    actual_outcome,
    brier,
    calibrate,
    calibration_curve,
    expected_calibration_error,
    overconfidence_assessment,
    per_strategy_brier,
    tune_shrinkage,
)
from ai_prophet.forecast.strategies.base import Estimate


def _est(p: float, conf: float = 0.7, name: str = "s") -> Estimate:
    return Estimate(p_yes=p, rationale="", strategy=name, confidence=conf)


# ---------------------------------------------------------------------------
# actual_outcome
# ---------------------------------------------------------------------------


def test_actual_outcome_string_yes() -> None:
    event = {"outcomes": ["Alice", "Bob"], "resolved_outcome": "Alice"}
    assert actual_outcome(event) == 1.0


def test_actual_outcome_string_no() -> None:
    event = {"outcomes": ["Alice", "Bob"], "resolved_outcome": "Bob"}
    assert actual_outcome(event) == 0.0


def test_actual_outcome_dict_yes() -> None:
    event = {
        "outcomes": ["Alice", "Bob"],
        "resolved_outcome": {"value": ["Alice"], "source": "X"},
    }
    assert actual_outcome(event) == 1.0


def test_actual_outcome_dict_no() -> None:
    event = {
        "outcomes": ["Alice", "Bob"],
        "resolved_outcome": {"value": ["Bob"]},
    }
    assert actual_outcome(event) == 0.0


def test_actual_outcome_none_when_unresolved() -> None:
    event = {"outcomes": ["Alice", "Bob"], "resolved_outcome": None}
    assert actual_outcome(event) is None


def test_actual_outcome_none_when_outcomes_missing() -> None:
    assert actual_outcome({"resolved_outcome": "Alice"}) is None
    assert actual_outcome({"outcomes": ["Alice"], "resolved_outcome": "Alice"}) is None


def test_actual_outcome_none_for_unknown_winner() -> None:
    event = {"outcomes": ["Alice", "Bob"], "resolved_outcome": "Carol"}
    assert actual_outcome(event) is None


# ---------------------------------------------------------------------------
# brier
# ---------------------------------------------------------------------------


def test_brier_basic() -> None:
    assert brier(0.5, 1.0) == pytest.approx(0.25)
    assert brier(0.5, 0.0) == pytest.approx(0.25)
    assert brier(1.0, 1.0) == pytest.approx(0.0)
    assert brier(0.0, 1.0) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# calibration_curve
# ---------------------------------------------------------------------------


def test_calibration_curve_empty_input() -> None:
    curve = calibration_curve([])
    assert len(curve) == 10
    assert all(b["n"] == 0 for b in curve)


def test_calibration_curve_groups_correctly() -> None:
    rows = [
        {"p_yes": 0.05, "actual": 0.0},
        {"p_yes": 0.08, "actual": 0.0},
        {"p_yes": 0.55, "actual": 1.0},
        {"p_yes": 0.95, "actual": 1.0},
    ]
    curve = calibration_curve(rows)
    # bucket 0.0-0.1: 2 entries, all NO
    assert curve[0]["n"] == 2
    assert curve[0]["mean_actual"] == 0.0
    # bucket 0.5-0.6: 1 entry, YES
    assert curve[5]["n"] == 1
    assert curve[5]["mean_actual"] == 1.0
    # bucket 0.9-1.0: 1 entry, YES
    assert curve[9]["n"] == 1
    assert curve[9]["mean_actual"] == 1.0


def test_calibration_curve_includes_p_yes_equal_one() -> None:
    rows = [{"p_yes": 1.0, "actual": 1.0}]
    curve = calibration_curve(rows)
    assert curve[9]["n"] == 1


# ---------------------------------------------------------------------------
# expected_calibration_error
# ---------------------------------------------------------------------------


def test_ece_is_zero_when_predictions_match_outcomes() -> None:
    # In every populated bucket, mean_p exactly equals mean_actual.
    rows = [{"p_yes": 0.0, "actual": 0.0}, {"p_yes": 1.0, "actual": 1.0}]
    assert expected_calibration_error(rows) == 0.0


def test_ece_positive_when_systematically_overconfident() -> None:
    # Agent says 0.95 for events that resolve NO 50% of the time.
    rows = [
        {"p_yes": 0.95, "actual": 1.0},
        {"p_yes": 0.95, "actual": 0.0},
    ]
    # mean_p=0.95, mean_actual=0.5 in the 0.9-1.0 bucket → ECE = 0.45
    assert expected_calibration_error(rows) == pytest.approx(0.45, abs=1e-4)


def test_ece_empty_input() -> None:
    assert expected_calibration_error([]) == 0.0


# ---------------------------------------------------------------------------
# overconfidence_assessment
# ---------------------------------------------------------------------------


def test_overconfidence_yes_side_only() -> None:
    rows = [
        {"p_yes": 0.8, "actual": 1.0},
        {"p_yes": 0.7, "actual": 1.0},
        {"p_yes": 0.9, "actual": 0.0},
    ]
    out = overconfidence_assessment(rows)
    assert "yes_calls" in out and "no_calls" not in out
    yc = out["yes_calls"]
    assert yc["n"] == 3
    assert yc["mean_predicted_p"] == pytest.approx(0.8)
    assert yc["actual_yes_rate"] == pytest.approx(2 / 3, abs=1e-3)
    # mean_p (0.8) - hit_rate (~0.667) = ~0.133 → overconfident
    assert yc["bias"] > 0


def test_overconfidence_no_side_only() -> None:
    rows = [
        {"p_yes": 0.2, "actual": 0.0},
        {"p_yes": 0.1, "actual": 1.0},
        {"p_yes": 0.15, "actual": 0.0},
    ]
    out = overconfidence_assessment(rows)
    assert "no_calls" in out and "yes_calls" not in out
    nc = out["no_calls"]
    assert nc["n"] == 3


def test_overconfidence_well_calibrated_returns_low_bias() -> None:
    # Predictions 0.8, hit rate 0.8 → bias ~ 0
    rows = [
        {"p_yes": 0.8, "actual": 1.0},
        {"p_yes": 0.8, "actual": 1.0},
        {"p_yes": 0.8, "actual": 1.0},
        {"p_yes": 0.8, "actual": 1.0},
        {"p_yes": 0.8, "actual": 0.0},
    ]
    out = overconfidence_assessment(rows)
    assert abs(out["yes_calls"]["bias"]) < 0.01


def test_overconfidence_ignores_exactly_half() -> None:
    rows = [{"p_yes": 0.5, "actual": 1.0}]
    out = overconfidence_assessment(rows)
    # p_yes==0.5 lands in neither yes_side nor no_side
    assert out == {}


# ---------------------------------------------------------------------------
# per_strategy_brier
# ---------------------------------------------------------------------------


def test_per_strategy_brier_basic() -> None:
    rows = [
        {
            "actual": 1.0,
            "estimates_raw": [_est(0.8, name="A"), _est(0.4, name="B")],
        },
        {
            "actual": 0.0,
            "estimates_raw": [_est(0.2, name="A"), _est(0.6, name="B")],
        },
    ]
    out = per_strategy_brier(rows)
    # A: brier=(0.2)^2 + (0.2)^2 = 0.04+0.04 → mean 0.04
    assert out["A"]["n"] == 2
    assert out["A"]["brier"] == pytest.approx(0.04)
    # B: brier=(0.6)^2 + (0.6)^2 = 0.36+0.36 → mean 0.36
    assert out["B"]["brier"] == pytest.approx(0.36)


# ---------------------------------------------------------------------------
# tune_shrinkage
# ---------------------------------------------------------------------------


def test_tune_shrinkage_picks_low_brier_value() -> None:
    # Three strongly-disagreeing strategies. The true outcome is YES (1.0).
    # With heavy shrinkage, the ensemble lands closer to 0.5; with no
    # shrinkage, it sits closer to 0.5 anyway (disagreement → middle).
    # The point is the sweep returns one of the candidates.
    rows = [
        {
            "actual": 1.0,
            "estimates_raw": [
                _est(0.9, 0.8, "A"),
                _est(0.9, 0.8, "B"),
                _est(0.9, 0.8, "C"),
            ],
        },
        {
            "actual": 1.0,
            "estimates_raw": [
                _est(0.8, 0.7, "A"),
                _est(0.85, 0.7, "B"),
                _est(0.9, 0.7, "C"),
            ],
        },
    ]
    out = tune_shrinkage(rows)
    assert out["recommended_shrinkage"] in {0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40}
    assert len(out["tested"]) == 8
    # When the agent is correctly confident-YES on YES outcomes, less
    # shrinkage should win.
    assert out["recommended_shrinkage"] <= 0.15


def test_tune_shrinkage_high_shrinkage_when_predictions_wrong() -> None:
    # Strategies are confidently wrong; heavy shrinkage toward 0.5 is best.
    rows = [
        {
            "actual": 0.0,
            "estimates_raw": [
                _est(0.95, 0.9, "A"),
                _est(0.93, 0.9, "B"),
                _est(0.97, 0.9, "C"),
            ],
        },
        {
            "actual": 0.0,
            "estimates_raw": [
                _est(0.9, 0.9, "A"),
                _est(0.92, 0.9, "B"),
                _est(0.88, 0.9, "C"),
            ],
        },
    ]
    out = tune_shrinkage(rows)
    # Pull toward 0.5 helps when confidently wrong.
    assert out["recommended_shrinkage"] >= 0.25


def test_tune_shrinkage_empty_rows() -> None:
    out = tune_shrinkage([])
    assert out["recommended_shrinkage"] is None or out["recommended_brier"] == 0.0


# ---------------------------------------------------------------------------
# calibrate (top-level orchestration)
# ---------------------------------------------------------------------------


def test_calibrate_returns_error_when_no_resolved_events() -> None:
    events = [
        {"market_ticker": "X", "outcomes": ["A", "B"], "resolved_outcome": None},
        {"market_ticker": "Y", "outcomes": ["A", "B"], "resolved_outcome": "Carol"},
    ]
    report = calibrate(events)
    assert report["n_resolved"] == 0
    assert "error" in report


def test_calibrate_runs_pipeline_and_writes_report(monkeypatch, tmp_path) -> None:
    """End-to-end test with the per-event pipeline mocked out."""
    from ai_prophet.forecast import calibrate as cal_mod

    def fake_pipeline(event_dict):
        # Always predict 0.7 with strong strategy agreement at 0.7.
        ests = [
            _est(0.7, 0.8, "evidence_weighted"),
            _est(0.7, 0.8, "base_rate"),
            _est(0.7, 0.8, "contrarian"),
        ]
        return {
            "estimates": [
                {"strategy": e.strategy, "p_yes": e.p_yes, "confidence": e.confidence, "rationale": ""}
                for e in ests
            ],
            "estimates_raw": ests,
            "final_p_yes": 0.7,
            "raw_p_yes": 0.7,
            "agreement": 1.0,
            "shrinkage_used": 0.075,
            "rationale": "stub",
        }

    monkeypatch.setattr(cal_mod, "predict_event_full", fake_pipeline)

    events = [
        {
            "market_ticker": "T1",
            "title": "t1",
            "outcomes": ["X", "Y"],
            "resolved_outcome": "X",
        },
        {
            "market_ticker": "T2",
            "title": "t2",
            "outcomes": ["X", "Y"],
            "resolved_outcome": "X",
        },
        {
            "market_ticker": "T3",
            "title": "t3",
            "outcomes": ["X", "Y"],
            "resolved_outcome": "Y",
        },
    ]
    out_path = tmp_path / "report.json"
    report = calibrate(events, output_path=out_path)

    assert report["n_resolved"] == 3
    # All predictions were 0.7; actuals are [1,1,0].
    # Brier = ((0.3)^2 + (0.3)^2 + (0.7)^2) / 3 = (0.09+0.09+0.49)/3 = 0.2233
    assert report["overall_brier"] == pytest.approx(0.2233, abs=1e-3)
    assert report["baseline_brier_at_half"] == pytest.approx(0.25)
    assert "shrinkage_tuning" in report
    assert report["shrinkage_tuning"]["recommended_shrinkage"] is not None
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8").startswith("{")
