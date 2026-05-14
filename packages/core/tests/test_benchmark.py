"""Tests for the forecast benchmark harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_prophet_core.forecast.benchmark import (
    BucketReport,
    CategoryReport,
    format_json,
    format_text,
    load_fixture,
    run_benchmark,
)


def _entry(
    market_ticker: str = "M1",
    category: str = "Politics",
    title: str = "?",
    result: str = "yes",
) -> dict:
    return {
        "event": {
            "event_ticker": market_ticker.split("-")[0],
            "market_ticker": market_ticker,
            "title": title,
            "category": category,
        },
        "result": result,
    }


def _const_agent(p: float):
    def _agent(event: dict) -> dict:
        return {"p_yes": p, "rationale": f"constant {p}"}

    return _agent


def _category_agent(rates: dict[str, float]):
    """Returns p_yes based on the event's category."""
    def _agent(event: dict) -> dict:
        cat = event.get("category", "")
        return {"p_yes": rates.get(cat, 0.5), "rationale": f"by-category {cat}"}

    return _agent


# ---- load_fixture --------------------------------------------------------


def test_load_fixture_filters_invalid_rows(tmp_path: Path):
    out = tmp_path / "fix.jsonl"
    out.write_text(
        "\n".join(
            [
                json.dumps(_entry()),
                "not json",
                json.dumps({"event": {}}),  # missing result
                json.dumps({"result": "yes"}),  # missing event
                json.dumps({"event": {}, "result": "huh"}),  # bad result
                json.dumps(_entry(market_ticker="M2", result="no")),
                "",
            ]
        )
    )
    entries = load_fixture(out)
    assert len(entries) == 2
    assert {e["event"]["market_ticker"] for e in entries} == {"M1", "M2"}


# ---- run_benchmark basics ------------------------------------------------


def test_perfect_agent_zero_brier():
    fixture = [
        _entry("M1", result="yes"),
        _entry("M2", result="no"),
    ]

    def _perfect(event: dict) -> dict:
        # Cheat: match the result via ticker.
        return {"p_yes": 0.99 if event["market_ticker"] == "M1" else 0.01, "rationale": ""}

    report = run_benchmark(_perfect, fixture)
    assert report.n == 2
    assert report.brier == pytest.approx(0.0001, abs=1e-5)


def test_uniform_agent_brier_is_0_25():
    fixture = [_entry("M1", result="yes"), _entry("M2", result="no")]
    report = run_benchmark(_const_agent(0.5), fixture)
    assert report.brier == pytest.approx(0.25, abs=1e-6)


def test_baseline_comparison_populates_delta():
    fixture = [_entry("M1", result="yes")]
    report = run_benchmark(
        _const_agent(0.9), fixture, baseline_fn=_const_agent(0.5)
    )
    # Brier: agent (0.9-1)^2=0.01; baseline (0.5-1)^2=0.25
    assert report.brier == pytest.approx(0.01)
    assert report.baseline_brier == pytest.approx(0.25)
    assert report.delta == pytest.approx(-0.24)


def test_by_category_breakdown():
    fixture = [
        _entry("P1", category="Politics", result="yes"),
        _entry("P2", category="Politics", result="yes"),
        _entry("S1", category="Sports", result="no"),
    ]
    agent = _category_agent({"Politics": 0.9, "Sports": 0.1})
    report = run_benchmark(agent, fixture)
    cats = {c.category: c for c in report.by_category}
    assert cats["Politics"].n == 2
    assert cats["Politics"].brier == pytest.approx(0.01)
    assert cats["Sports"].n == 1
    assert cats["Sports"].brier == pytest.approx(0.01)


def test_calibration_buckets():
    # 10 markets predicted at 0.55. 7 resolve YES, 3 resolve NO.
    fixture = [_entry(f"M{i}", result="yes") for i in range(7)] + [
        _entry(f"M{i}", result="no") for i in range(7, 10)
    ]
    report = run_benchmark(_const_agent(0.55), fixture, n_bins=10)
    # All 10 fall in the [0.5, 0.6) bucket
    bucket = next(b for b in report.calibration if b.bucket == "[0.5-0.6)")
    assert bucket.n == 10
    assert bucket.mean_p == pytest.approx(0.55)
    assert bucket.mean_actual == pytest.approx(0.7)


def test_agent_errors_counted_and_defaulted_to_half():
    fixture = [_entry("M1", result="yes")]

    def _broken(event: dict) -> dict:
        raise RuntimeError("boom")

    report = run_benchmark(_broken, fixture)
    assert report.agent_errors == 1
    # 0.5 default → Brier (0.5-1)^2 = 0.25
    assert report.brier == pytest.approx(0.25)


def test_baseline_errors_counted_separately():
    fixture = [_entry("M1", result="yes")]

    def _broken(event: dict) -> dict:
        raise RuntimeError("boom")

    report = run_benchmark(_const_agent(0.7), fixture, baseline_fn=_broken)
    assert report.agent_errors == 0
    assert report.baseline_errors == 1


def test_clamps_out_of_range_p():
    fixture = [_entry("M1", result="yes")]

    def _bad(event: dict) -> dict:
        return {"p_yes": 1.5, "rationale": "bad agent"}

    report = run_benchmark(_bad, fixture)
    # 1.5 clamped to 0.99 → Brier (0.99-1)^2 = 0.0001
    assert report.brier == pytest.approx(0.0001, abs=1e-6)
    assert report.agent_errors == 0


def test_empty_fixture_returns_empty_report():
    report = run_benchmark(_const_agent(0.5), [])
    assert report.n == 0
    assert report.brier == 0.0
    assert report.by_category == []
    assert report.calibration == []
    assert report.worst_rows == []


def test_worst_rows_sorted_by_brier_desc():
    fixture = [
        _entry("M1", result="yes"),  # agent says 0.2 → Brier 0.64 (worst)
        _entry("M2", result="yes"),  # agent says 0.8 → Brier 0.04 (best)
    ]

    def _per_ticker(event: dict) -> dict:
        return {"p_yes": 0.2 if event["market_ticker"] == "M1" else 0.8, "rationale": ""}

    report = run_benchmark(_per_ticker, fixture, worst_n=2)
    assert len(report.worst_rows) == 2
    assert report.worst_rows[0]["market_ticker"] == "M1"
    assert report.worst_rows[1]["market_ticker"] == "M2"


def test_n_bins_validated():
    fixture = [_entry("M1", result="yes")]
    with pytest.raises(ValueError):
        run_benchmark(_const_agent(0.5), fixture, n_bins=1)


# ---- formatters ----------------------------------------------------------


def test_format_text_includes_headline_numbers():
    fixture = [_entry("M1", result="yes")]
    report = run_benchmark(_const_agent(0.7), fixture)
    s = format_text(report)
    assert "N predictions" in s
    assert "Overall Brier" in s
    # No baseline → no "Baseline Brier" line
    assert "Baseline Brier" not in s


def test_format_text_with_baseline_shows_delta():
    fixture = [_entry("M1", result="yes")]
    report = run_benchmark(
        _const_agent(0.9), fixture, baseline_fn=_const_agent(0.5)
    )
    s = format_text(report)
    assert "Baseline Brier" in s
    assert "delta" in s


def test_format_json_is_machine_readable():
    fixture = [_entry("M1", result="yes")]
    report = run_benchmark(_const_agent(0.7), fixture)
    js = format_json(report)
    parsed = json.loads(js)
    assert parsed["n"] == 1
    assert parsed["brier"] == report.brier
    assert "by_category" in parsed
    assert "calibration" in parsed


def test_format_text_flags_biased_buckets():
    # 10 markets predicted at 0.4. 9 resolve YES → mean_p=0.4, actual=0.9
    # Bias ~0.5, should flag.
    fixture = [_entry(f"M{i}", result="yes") for i in range(9)] + [_entry("M10", result="no")]
    report = run_benchmark(_const_agent(0.4), fixture)
    s = format_text(report)
    assert "BIAS" in s
