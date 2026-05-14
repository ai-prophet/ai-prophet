"""End-to-end benchmark harness for forecast agents.

Given an agent function and a resolved-markets fixture (the kind `capture`
produces once you've added outcomes), produce a structured report:
  - overall Brier score
  - by-category breakdown
  - calibration buckets (predicted-probability bin → observed yes-rate)
  - worst-Brier rows for inspection
  - optional baseline-agent comparison with per-category Brier deltas

Use cases:
  - Iterating on an agent during development: does your new prompt
    actually beat the prior version?
  - Validating a submission before the eval window starts.
  - Reporting calibration bias to know which buckets need correction.

Agent function contract matches `prophet forecast predict --local`:
    def predict(event: dict) -> dict:  # {"p_yes": float, "rationale": str}
"""

from __future__ import annotations

import json
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class BucketReport:
    bucket: str          # e.g. "[0.0-0.1)"
    n: int
    mean_p: float
    mean_actual: float
    brier: float

    @property
    def bias(self) -> float:
        return self.mean_actual - self.mean_p


@dataclass
class CategoryReport:
    category: str
    n: int
    brier: float
    baseline_brier: float | None = None

    @property
    def delta(self) -> float | None:
        if self.baseline_brier is None:
            return None
        return self.brier - self.baseline_brier


@dataclass
class BenchmarkReport:
    n: int
    brier: float
    baseline_brier: float | None
    by_category: list[CategoryReport]
    calibration: list[BucketReport]
    worst_rows: list[dict[str, Any]]
    agent_errors: int = 0  # how many calls raised
    baseline_errors: int = 0

    @property
    def delta(self) -> float | None:
        if self.baseline_brier is None:
            return None
        return self.brier - self.baseline_brier


def load_fixture(path: str | Path) -> list[dict[str, Any]]:
    """Load a JSONL fixture. Each line must have `event` and `result` ∈ {yes, no}."""
    entries: list[dict[str, Any]] = []
    with Path(path).open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("result") not in ("yes", "no"):
                continue
            if "event" not in entry:
                continue
            entries.append(entry)
    return entries


def _safe_call(agent_fn: Callable[..., dict], event: dict) -> tuple[float, str, bool]:
    """Call agent_fn(event) and pull a clamped p_yes. Returns (p, rationale, errored)."""
    try:
        out = agent_fn(event)
        p_raw = float(out["p_yes"])
        p = max(0.01, min(0.99, p_raw))
        rationale = str(out.get("rationale", ""))
        return p, rationale, False
    except Exception:
        # On any failure return a neutral 0.5 so this row doesn't kill the run.
        return 0.5, "agent error: defaulted to 0.5", True


def run_benchmark(
    agent_fn: Callable[..., dict],
    fixture: list[dict[str, Any]],
    *,
    baseline_fn: Callable[..., dict] | None = None,
    n_bins: int = 10,
    worst_n: int = 10,
) -> BenchmarkReport:
    """Score `agent_fn` over `fixture`, optionally vs. `baseline_fn`."""
    rows: list[dict[str, Any]] = []
    agent_errors = 0
    baseline_errors = 0
    for entry in fixture:
        event = entry["event"]
        result = entry["result"]
        actual = 1.0 if result == "yes" else 0.0

        p, rationale, errored = _safe_call(agent_fn, event)
        if errored:
            agent_errors += 1

        baseline_p: float | None = None
        baseline_rationale: str = ""
        if baseline_fn is not None:
            bp, brat, berr = _safe_call(baseline_fn, event)
            baseline_p = bp
            baseline_rationale = brat
            if berr:
                baseline_errors += 1

        rows.append(
            {
                "event_ticker": event.get("event_ticker", ""),
                "market_ticker": event.get("market_ticker", ""),
                "title": (event.get("title") or "")[:80],
                "category": event.get("category", "?"),
                "p": p,
                "rationale": rationale,
                "actual": actual,
                "brier": (p - actual) ** 2,
                "baseline_p": baseline_p,
                "baseline_rationale": baseline_rationale,
                "baseline_brier": ((baseline_p - actual) ** 2) if baseline_p is not None else None,
            }
        )

    if not rows:
        return BenchmarkReport(
            n=0, brier=0.0, baseline_brier=None,
            by_category=[], calibration=[], worst_rows=[],
        )

    overall_brier = statistics.mean(r["brier"] for r in rows)
    baselines = [r["baseline_brier"] for r in rows if r["baseline_brier"] is not None]
    baseline_brier = statistics.mean(baselines) if baselines else None

    # By category
    by_cat_map: dict[str, list[dict]] = {}
    for r in rows:
        by_cat_map.setdefault(r["category"], []).append(r)
    by_category: list[CategoryReport] = []
    for cat, items in sorted(by_cat_map.items()):
        cat_brier = statistics.mean(r["brier"] for r in items)
        cat_baselines = [r["baseline_brier"] for r in items if r["baseline_brier"] is not None]
        cat_baseline = statistics.mean(cat_baselines) if cat_baselines else None
        by_category.append(
            CategoryReport(
                category=cat,
                n=len(items),
                brier=round(cat_brier, 5),
                baseline_brier=round(cat_baseline, 5) if cat_baseline is not None else None,
            )
        )

    # Calibration buckets
    if n_bins < 2:
        raise ValueError("n_bins must be >= 2")
    buckets: list[list[dict]] = [[] for _ in range(n_bins)]
    for r in rows:
        idx = min(n_bins - 1, max(0, int(r["p"] * n_bins)))
        buckets[idx].append(r)
    calibration: list[BucketReport] = []
    for i, b in enumerate(buckets):
        if not b:
            continue
        lo, hi = i / n_bins, (i + 1) / n_bins
        calibration.append(
            BucketReport(
                bucket=f"[{lo:.1f}-{hi:.1f})",
                n=len(b),
                mean_p=round(statistics.mean(r["p"] for r in b), 4),
                mean_actual=round(statistics.mean(r["actual"] for r in b), 4),
                brier=round(statistics.mean(r["brier"] for r in b), 5),
            )
        )

    # Worst rows
    rows_sorted = sorted(rows, key=lambda r: r["brier"], reverse=True)
    worst = [
        {
            "market_ticker": r["market_ticker"],
            "category": r["category"],
            "title": r["title"],
            "p": r["p"],
            "actual": r["actual"],
            "brier": round(r["brier"], 5),
            "rationale": r["rationale"][:200],
        }
        for r in rows_sorted[:worst_n]
    ]

    return BenchmarkReport(
        n=len(rows),
        brier=round(overall_brier, 5),
        baseline_brier=round(baseline_brier, 5) if baseline_brier is not None else None,
        by_category=by_category,
        calibration=calibration,
        worst_rows=worst,
        agent_errors=agent_errors,
        baseline_errors=baseline_errors,
    )


# ---- Formatters ---------------------------------------------------------


def format_text(report: BenchmarkReport, *, show_worst: int = 5) -> str:
    """Human-readable terminal report."""
    lines: list[str] = []
    lines.append(f"N predictions:       {report.n}")
    lines.append(f"Overall Brier:       {report.brier}")
    if report.baseline_brier is not None:
        delta = report.brier - report.baseline_brier
        lines.append(
            f"Baseline Brier:      {report.baseline_brier}  "
            f"(delta {delta:+.5f}, {'better' if delta < 0 else 'worse'})"
        )
    lines.append(f"Baseline (always-0.5): 0.25")
    if report.agent_errors:
        lines.append(f"Agent errors:        {report.agent_errors}  (defaulted to 0.5)")
    if report.baseline_errors:
        lines.append(f"Baseline errors:     {report.baseline_errors}")
    lines.append("")

    if report.by_category:
        lines.append("By category:")
        for c in sorted(report.by_category, key=lambda c: -c.n):
            base_str = ""
            if c.baseline_brier is not None and c.delta is not None:
                base_str = f"  baseline {c.baseline_brier} Δ {c.delta:+.4f}"
            lines.append(f"  {c.category:<26} n={c.n:<4} brier={c.brier}{base_str}")
        lines.append("")

    if report.calibration:
        lines.append("Calibration (p bucket → actual yes rate):")
        header = f"  {'bucket':<14}{'n':>5}{'mean_p':>10}{'actual':>10}{'brier':>10}"
        lines.append(header)
        for c in report.calibration:
            flag = "  ← BIAS" if c.n >= 5 and abs(c.bias) > 0.10 else ""
            lines.append(
                f"  {c.bucket:<14}{c.n:>5}{c.mean_p:>10.3f}{c.mean_actual:>10.3f}{c.brier:>10.5f}{flag}"
            )
        lines.append("")

    if report.worst_rows and show_worst > 0:
        lines.append(f"Worst-Brier rows ({min(show_worst, len(report.worst_rows))} shown):")
        for r in report.worst_rows[:show_worst]:
            lines.append(
                f"  {r['market_ticker'][:42]:<44} cat={r['category'][:14]:<16} "
                f"p={r['p']:.3f} actual={r['actual']:.0f} brier={r['brier']:.4f}"
            )

    return "\n".join(lines)


def format_json(report: BenchmarkReport) -> str:
    """Machine-readable JSON dump."""
    return json.dumps(
        {
            "n": report.n,
            "brier": report.brier,
            "baseline_brier": report.baseline_brier,
            "delta_vs_baseline": report.delta,
            "agent_errors": report.agent_errors,
            "baseline_errors": report.baseline_errors,
            "by_category": [asdict(c) for c in report.by_category],
            "calibration": [asdict(c) for c in report.calibration],
            "worst_rows": report.worst_rows,
        },
        indent=2,
    )
