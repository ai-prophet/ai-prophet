"""Self-calibration tool for the ensemble forecasting agent.

Given a list of events with ``resolved_outcome`` populated, run the agent
blind (force-clearing ``resolved_outcome`` so the shortcut path doesn't fire)
and score the result against the actual outcome. The report includes:

* Overall Brier score vs the always-0.5 baseline.
* Per-strategy Brier (run each strategy alone on the same research brief).
* A 10-bucket calibration curve (predicted vs observed yes-rate).
* An Expected Calibration Error and an overconfidence breakdown.
* Shrinkage tuning: re-runs the ensemble on the captured estimates with
  several candidate ``base_shrinkage`` values and recommends the one with
  the lowest Brier on the resolved fixture.

Run as a CLI::

    python -m ai_prophet.forecast.calibrate \\
        --events sample_resolved.json \\
        --output calibration_report.json
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .ensemble import DEFAULT_SHRINKAGE, ensemble_predict
from .ensemble_agent import _coerce_event, _resolved_outcome_value
from .researcher import research_event
from .strategies import (
    BaseRateStrategy,
    ContrarianStrategy,
    EvidenceWeightedStrategy,
)
from .strategies.base import Estimate, failed_estimate
from .temporal import (
    hours_until_close,
    temporal_context_string,
    temporal_factor,
)

logger = logging.getLogger(__name__)

SHRINKAGE_CANDIDATES: tuple[float, ...] = (0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.40)
"""Shrinkage values swept in :func:`tune_shrinkage`. Includes ``0.0`` to test
'no shrinkage at all' as a baseline."""

N_CALIBRATION_BUCKETS = 10


# ---------------------------------------------------------------------------
# Pure scoring helpers
# ---------------------------------------------------------------------------


def actual_outcome(event: dict[str, Any]) -> float | None:
    """Return 1.0 if outcomes[0] won, 0.0 if outcomes[1] won, else None.

    Accepts both shapes of ``resolved_outcome``: a bare string (live format)
    or a dict with a ``"value"`` key (dataset registry format).
    """
    outcomes = event.get("outcomes") or []
    if not isinstance(outcomes, list) or len(outcomes) < 2:
        return None
    resolved = _resolved_outcome_value(event.get("resolved_outcome"))
    if resolved is None:
        return None
    if resolved == outcomes[0]:
        return 1.0
    if resolved == outcomes[1]:
        return 0.0
    return None


def brier(p: float, y: float) -> float:
    """Single-prediction Brier contribution: ``(p - y) ** 2``."""
    return (p - y) ** 2


def calibration_curve(
    rows: list[dict], n_buckets: int = N_CALIBRATION_BUCKETS
) -> list[dict]:
    """Bucket predictions by ``p_yes`` and report predicted vs observed yes-rate.

    Each row in ``rows`` must have ``p_yes`` (float) and ``actual`` (0.0 or 1.0).
    The last bucket is closed on the right so ``p_yes == 1.0`` is counted.
    """
    buckets: list[dict] = []
    for i in range(n_buckets):
        lo = i / n_buckets
        hi = (i + 1) / n_buckets
        last = i == n_buckets - 1
        in_bucket = [
            r
            for r in rows
            if (lo <= r["p_yes"] < hi) or (last and r["p_yes"] >= hi)
        ]
        if not in_bucket:
            buckets.append(
                {
                    "range": [round(lo, 2), round(hi, 2)],
                    "n": 0,
                    "mean_p": None,
                    "mean_actual": None,
                    "delta": None,
                }
            )
            continue
        mean_p = sum(r["p_yes"] for r in in_bucket) / len(in_bucket)
        mean_actual = sum(r["actual"] for r in in_bucket) / len(in_bucket)
        buckets.append(
            {
                "range": [round(lo, 2), round(hi, 2)],
                "n": len(in_bucket),
                "mean_p": round(mean_p, 4),
                "mean_actual": round(mean_actual, 4),
                "delta": round(mean_p - mean_actual, 4),
            }
        )
    return buckets


def expected_calibration_error(rows: list[dict]) -> float:
    """Weighted average of ``|mean_p - mean_actual|`` across calibration buckets.

    Lower is better. 0 means the model's stated probabilities exactly match
    the observed frequencies in every bucket.
    """
    curve = calibration_curve(rows)
    n_total = sum(b["n"] for b in curve)
    if n_total == 0:
        return 0.0
    ece = (
        sum(b["n"] * abs(b["delta"]) for b in curve if b["delta"] is not None)
        / n_total
    )
    return round(ece, 4)


def overconfidence_assessment(rows: list[dict]) -> dict:
    """Compare predicted confidence to actual hit rate on each side of 0.5.

    Returns ``yes_calls`` / ``no_calls`` blocks with:
        * ``mean_predicted_p`` — average ``p_yes`` (yes-side) or ``1 - p_yes`` (no-side)
        * ``actual_yes_rate`` / ``actual_no_rate`` — observed hit rate
        * ``bias`` — positive means overconfident, negative means too conservative
    """
    out: dict[str, Any] = {}
    yes_side = [r for r in rows if r["p_yes"] > 0.5]
    no_side = [r for r in rows if r["p_yes"] < 0.5]

    if yes_side:
        mean_p = sum(r["p_yes"] for r in yes_side) / len(yes_side)
        hit_rate = sum(r["actual"] for r in yes_side) / len(yes_side)
        out["yes_calls"] = {
            "n": len(yes_side),
            "mean_predicted_p": round(mean_p, 4),
            "actual_yes_rate": round(hit_rate, 4),
            "bias": round(mean_p - hit_rate, 4),
        }
    if no_side:
        mean_p = sum(r["p_yes"] for r in no_side) / len(no_side)
        hit_rate = sum(1.0 - r["actual"] for r in no_side) / len(no_side)
        out["no_calls"] = {
            "n": len(no_side),
            "mean_predicted_p": round(1 - mean_p, 4),
            "actual_no_rate": round(hit_rate, 4),
            "bias": round((1 - mean_p) - hit_rate, 4),
        }
    return out


def per_strategy_brier(rows_full: list[dict]) -> dict[str, dict]:
    """Brier score for each strategy run alone (using its own ``p_yes``)."""
    strategy_briers: dict[str, list[float]] = {}
    for row in rows_full:
        for est in row["estimates_raw"]:
            strategy_briers.setdefault(est.strategy, []).append(
                brier(est.p_yes, row["actual"])
            )
    return {
        name: {
            "n": len(briers),
            "brier": round(sum(briers) / len(briers), 4),
        }
        for name, briers in strategy_briers.items()
    }


def tune_shrinkage(
    rows_full: list[dict],
    *,
    candidates: tuple[float, ...] = SHRINKAGE_CANDIDATES,
) -> dict:
    """Sweep candidate shrinkage values and pick the lowest-Brier one.

    For each candidate, re-runs ``ensemble_predict`` on the captured raw
    estimates with ``base_shrinkage=s``, computes Brier across all rows, and
    returns the full sweep plus the best value.
    """
    sweep = []
    for s in candidates:
        briers = []
        for row in rows_full:
            re_ensemble = ensemble_predict(row["estimates_raw"], base_shrinkage=s)
            briers.append(brier(re_ensemble.p_yes, row["actual"]))
        mean_brier = sum(briers) / len(briers) if briers else 0.0
        sweep.append({"shrinkage": s, "brier": round(mean_brier, 4)})
    best = min(sweep, key=lambda x: x["brier"]) if sweep else None
    return {
        "tested": sweep,
        "recommended_shrinkage": best["shrinkage"] if best else None,
        "recommended_brier": best["brier"] if best else None,
        "default_shrinkage": DEFAULT_SHRINKAGE,
    }


# ---------------------------------------------------------------------------
# Per-event pipeline (research + strategies + ensemble)
# ---------------------------------------------------------------------------


def _run_one_strategy(
    strategy: Any, event: Any, research: str, temporal_ctx: str | None = None
) -> Estimate:
    try:
        return strategy.estimate(
            title=event.title,
            description=event.description,
            category=event.category,
            rules=event.rules,
            close_time=event.close_time,
            research=research,
            outcomes=event.outcomes,
            temporal_context=temporal_ctx,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "strategy %s crashed: %s", getattr(strategy, "name", "?"), exc
        )
        return failed_estimate(getattr(strategy, "name", "unknown"), str(exc))


def predict_event_full(event_dict: dict[str, Any]) -> dict[str, Any]:
    """Run research + all three strategies + ensemble on one event.

    The event's ``resolved_outcome`` is force-cleared so the agent runs
    blind — we want to measure how it *would have* performed if the answer
    weren't already known. Temporal context is computed from
    ``close_time`` and threaded through strategies + ensemble identically
    to the production pipeline.
    """
    blind = {**event_dict, "resolved_outcome": None}
    event_req = _coerce_event(blind)

    hours = hours_until_close(event_req.close_time)
    factor = temporal_factor(hours)
    temporal_ctx = temporal_context_string(hours)

    research = research_event(
        title=event_req.title,
        description=event_req.description,
        category=event_req.category,
        rules=event_req.rules,
    )

    strategies = [
        EvidenceWeightedStrategy(),
        BaseRateStrategy(),
        ContrarianStrategy(),
    ]
    estimates: list[Estimate] = []
    with ThreadPoolExecutor(max_workers=len(strategies)) as pool:
        futures = [
            pool.submit(_run_one_strategy, s, event_req, research, temporal_ctx)
            for s in strategies
        ]
        for fut in futures:
            estimates.append(fut.result())

    final = ensemble_predict(estimates, temporal_factor=factor)
    return {
        "estimates": [
            {
                "strategy": e.strategy,
                "p_yes": round(e.p_yes, 4),
                "confidence": round(e.confidence, 4),
                "rationale": e.rationale,
            }
            for e in estimates
        ],
        "estimates_raw": estimates,
        "final_p_yes": final.p_yes,
        "raw_p_yes": final.raw_p_yes,
        "agreement": final.agreement,
        "shrinkage_used": final.shrinkage,
        "rationale": final.rationale,
    }


# ---------------------------------------------------------------------------
# Top-level orchestration
# ---------------------------------------------------------------------------


def calibrate(
    events: list[dict[str, Any]],
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    """Score the agent on all resolved events in ``events`` and assemble a report."""
    resolved = [e for e in events if actual_outcome(e) is not None]
    logger.info(
        "calibrate.start  total=%d  resolved=%d  skipped=%d",
        len(events),
        len(resolved),
        len(events) - len(resolved),
    )
    if not resolved:
        return {
            "n_resolved": 0,
            "error": "no events have a resolved_outcome that maps to outcomes[0]/outcomes[1]",
        }

    rows_full: list[dict[str, Any]] = []
    t0 = time.perf_counter()

    for i, event in enumerate(resolved, 1):
        ticker = event.get("market_ticker", "?")
        actual = actual_outcome(event)
        logger.info("[%d/%d] %s", i, len(resolved), ticker)
        t_start = time.perf_counter()
        try:
            result = predict_event_full(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("predict crashed on %s: %s", ticker, exc)
            continue
        elapsed = time.perf_counter() - t_start

        p = result["final_p_yes"]
        b = brier(p, actual)
        logger.info(
            "  -> p_yes=%.3f actual=%.0f brier=%.3f elapsed=%.1fs",
            p,
            actual,
            b,
            elapsed,
        )
        rows_full.append(
            {
                "market_ticker": ticker,
                "title": event.get("title"),
                "category": event.get("category"),
                "outcomes": event.get("outcomes"),
                "actual": actual,
                "p_yes": p,
                "raw_p_yes": result["raw_p_yes"],
                "estimates_raw": result["estimates_raw"],
                "estimates": result["estimates"],
                "brier": round(b, 4),
                "elapsed_s": round(elapsed, 2),
            }
        )

    if not rows_full:
        return {
            "n_resolved": 0,
            "error": "every resolved event failed during prediction",
        }

    overall_brier = sum(r["brier"] for r in rows_full) / len(rows_full)
    baseline_brier = (
        sum(brier(0.5, r["actual"]) for r in rows_full) / len(rows_full)
    )
    simple_rows = [{"p_yes": r["p_yes"], "actual": r["actual"]} for r in rows_full]

    report: dict[str, Any] = {
        "n_resolved": len(rows_full),
        "wall_time_s": round(time.perf_counter() - t0, 1),
        "overall_brier": round(overall_brier, 4),
        "baseline_brier_at_half": round(baseline_brier, 4),
        "delta_vs_baseline": round(baseline_brier - overall_brier, 4),
        "per_strategy_brier": per_strategy_brier(rows_full),
        "calibration_curve": calibration_curve(simple_rows),
        "expected_calibration_error": expected_calibration_error(simple_rows),
        "overconfidence_assessment": overconfidence_assessment(simple_rows),
        "shrinkage_tuning": tune_shrinkage(rows_full),
        "per_event": [
            {k: v for k, v in r.items() if k != "estimates_raw"}
            for r in rows_full
        ],
    }

    if output_path is not None:
        output_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info("wrote %s", output_path)
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_summary(report: dict[str, Any], output: Path) -> None:
    if "error" in report:
        print(f"ERROR: {report['error']}")
        return
    print()
    print("=" * 72)
    print(f"CALIBRATION REPORT  ({report['n_resolved']} resolved events, "
          f"{report['wall_time_s']}s wall)")
    print("=" * 72)
    print(f"Overall Brier         : {report['overall_brier']}")
    print(f"Always-0.5 baseline   : {report['baseline_brier_at_half']}")
    delta = report["delta_vs_baseline"]
    print(f"Delta vs baseline     : {delta:+.4f}  "
          f"({'better' if delta > 0 else 'worse'} than always-0.5)")
    print(f"Expected Calib Error  : {report['expected_calibration_error']}")
    print()
    print("Per-strategy Brier:")
    for name, info in report["per_strategy_brier"].items():
        print(f"  {name:<22} n={info['n']:<3} Brier={info['brier']}")
    print()
    print("Overconfidence assessment:")
    oc = report["overconfidence_assessment"]
    for side, block in oc.items():
        bias = block["bias"]
        flag = ("OVERCONFIDENT" if bias > 0.05 else
                "TOO CAUTIOUS" if bias < -0.05 else "well calibrated")
        print(f"  {side}: n={block['n']}  predicted={block.get('mean_predicted_p', '?')}  "
              f"actual={block.get('actual_yes_rate', block.get('actual_no_rate', '?'))}  "
              f"bias={bias:+.4f}  [{flag}]")
    print()
    print("Shrinkage tuning sweep:")
    tuning = report["shrinkage_tuning"]
    rec = tuning["recommended_shrinkage"]
    for entry in tuning["tested"]:
        marker = "  <-- recommended" if entry["shrinkage"] == rec else ""
        default_marker = "  (current default)" if entry["shrinkage"] == tuning["default_shrinkage"] else ""
        print(f"  shrinkage={entry['shrinkage']:.2f}  Brier={entry['brier']}{marker}{default_marker}")
    print()
    print(f"Wrote {output}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="ai_prophet.forecast.calibrate",
        description=(
            "Score the ensemble agent on resolved events, report calibration "
            "metrics, and recommend an optimal shrinkage value."
        ),
    )
    parser.add_argument(
        "--events",
        required=True,
        type=Path,
        help="Path to events JSON file (list of events with resolved_outcome populated).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=Path("calibration_report.json"),
        help="Where to write the full JSON report (default: calibration_report.json).",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug-level logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.events.exists():
        sys.exit(f"events file not found: {args.events}")

    try:
        events = json.loads(args.events.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"failed to parse {args.events}: {exc}")

    if not isinstance(events, list):
        sys.exit("events file must contain a JSON list of event dicts")

    report = calibrate(events, output_path=args.output)
    _print_summary(report, args.output)


if __name__ == "__main__":
    main()
