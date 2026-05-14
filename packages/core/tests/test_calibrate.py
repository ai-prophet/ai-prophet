"""Tests for the forecast calibration module."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_prophet_core.forecast.calibrate import (
    apply_calibration,
    fit_calibration,
    load_calibration,
    save_calibration,
)
from ai_prophet_core.forecast.schemas import Prediction


def _preds(*pairs: tuple[str, float]) -> list[Prediction]:
    return [Prediction(market_ticker=t, p_yes=p, rationale="") for t, p in pairs]


# ---- fit_calibration -----------------------------------------------------


def test_fit_groups_predictions_into_buckets():
    preds = _preds(
        ("M1", 0.05), ("M2", 0.08),     # bucket [0.0, 0.1)
        ("M3", 0.55), ("M4", 0.59),     # bucket [0.5, 0.6)
    )
    actuals = {"M1": 0.0, "M2": 0.0, "M3": 1.0, "M4": 0.0}
    table = fit_calibration(preds, actuals, n_bins=10)

    assert len(table) == 2
    b0 = table[0]
    assert b0["bucket_lo"] == 0.0
    assert b0["bucket_hi"] == 0.1
    assert b0["n"] == 2
    assert b0["mean_actual"] == pytest.approx(0.0)

    b1 = table[1]
    assert b1["bucket_lo"] == 0.5
    assert b1["bucket_hi"] == 0.6
    assert b1["mean_actual"] == pytest.approx(0.5)


def test_fit_skips_predictions_without_actuals():
    preds = _preds(("M1", 0.5), ("M2", 0.5), ("M3", 0.5))
    actuals = {"M1": 1.0}  # only one matched
    table = fit_calibration(preds, actuals, n_bins=10)
    assert len(table) == 1
    assert table[0]["n"] == 1


def test_fit_returns_empty_table_with_no_matches():
    table = fit_calibration(_preds(("M1", 0.5)), {"OTHER": 1.0}, n_bins=10)
    assert table == []


def test_fit_handles_extremes_correctly():
    # p=0.99 falls in last bucket (0.9-1.0), p=0.0 in first.
    preds = _preds(("HIGH", 0.99), ("LOW", 0.01))
    actuals = {"HIGH": 1.0, "LOW": 0.0}
    table = fit_calibration(preds, actuals, n_bins=10)
    los = [b["bucket_lo"] for b in table]
    assert 0.0 in los  # low bucket
    assert 0.9 in los  # high bucket


def test_fit_rejects_too_few_bins():
    with pytest.raises(ValueError):
        fit_calibration(_preds(("M1", 0.5)), {"M1": 1.0}, n_bins=1)


# ---- apply_calibration --------------------------------------------------


def test_apply_replaces_p_with_bucket_actual_rate():
    table = [
        {"bucket_lo": 0.3, "bucket_hi": 0.4, "n": 10, "mean_p": 0.35, "mean_actual": 0.7},
    ]
    # 0.35 falls in [0.3, 0.4), gets mapped to 0.7
    assert apply_calibration(0.35, table) == pytest.approx(0.7)


def test_apply_passes_through_when_no_bucket_matches():
    table = [
        {"bucket_lo": 0.3, "bucket_hi": 0.4, "n": 10, "mean_p": 0.35, "mean_actual": 0.7},
    ]
    # 0.55 doesn't fall in any bucket
    assert apply_calibration(0.55, table) == pytest.approx(0.55)


def test_apply_handles_last_bucket_inclusive():
    table = [
        {"bucket_lo": 0.9, "bucket_hi": 1.0, "n": 5, "mean_p": 0.95, "mean_actual": 0.85},
    ]
    # 1.0 is at the inclusive boundary
    assert apply_calibration(0.99, table) == pytest.approx(0.85)
    assert apply_calibration(1.0, table) == pytest.approx(0.85)


def test_apply_clamps_corrected_values_to_contract_range():
    table = [
        {"bucket_lo": 0.5, "bucket_hi": 0.6, "n": 5, "mean_p": 0.55, "mean_actual": 0.0},
    ]
    # mean_actual=0.0 gets clamped to 0.01
    assert apply_calibration(0.55, table) == 0.01


def test_apply_empty_table_passes_through():
    assert apply_calibration(0.5, []) == 0.5


# ---- save / load roundtrip ---------------------------------------------


def test_save_load_roundtrip(tmp_path: Path):
    table = [
        {"bucket_lo": 0.0, "bucket_hi": 0.1, "n": 3, "mean_p": 0.05, "mean_actual": 0.0},
        {"bucket_lo": 0.5, "bucket_hi": 0.6, "n": 2, "mean_p": 0.55, "mean_actual": 0.5},
    ]
    out = tmp_path / "cal.json"
    save_calibration(table, out)
    loaded = load_calibration(out)
    assert loaded == table


def test_load_rejects_unknown_version(tmp_path: Path):
    p = tmp_path / "bad.json"
    p.write_text('{"version": 999, "buckets": []}')
    with pytest.raises(ValueError):
        load_calibration(p)
