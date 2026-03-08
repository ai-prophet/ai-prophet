"""Compact prompt memory derived from persisted server-side reasoning."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ai_prophet_core.client_models import ReasoningEntry

DEFAULT_MAX_MARKETS = 8
DEFAULT_MAX_CHARS = 1400


@dataclass(frozen=True)
class MemoryContext:
    summary: str
    by_market: dict[str, str]


def build_memory_context(
    entries: list[ReasoningEntry],
    current_market_ids: list[str],
    market_history_limit: int,
    max_markets: int = DEFAULT_MAX_MARKETS,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> MemoryContext:
    """Build compact market memory from prior ticks.

    Keeps only compact structured fields:
    - p_yes from forecast stage
    - yes_mark from candidates snapshot
    - action + size from decisions
    """
    if not entries or not current_market_ids:
        return MemoryContext(summary="", by_market={})

    active = {_normalize_market_id(m) for m in current_market_ids}
    per_market: dict[str, deque[str]] = {}
    last_tick_by_market: dict[str, datetime] = {}

    for entry in sorted(entries, key=lambda e: e.tick_ts):
        reasoning = entry.reasoning or {}
        candidate_marks = _extract_marks(reasoning.get("candidates"), active)
        forecasts = reasoning.get("forecasts") or {}
        decisions = reasoning.get("decisions") or {}

        candidate_markets = set(candidate_marks.keys())
        forecast_markets = {
            _normalize_market_id(m) for m in forecasts.keys() if _normalize_market_id(m) in active
        }
        decision_markets = {
            _normalize_market_id(m) for m in decisions.keys() if _normalize_market_id(m) in active
        }
        markets = candidate_markets | forecast_markets | decision_markets

        for market_id in markets:
            # JSON object keys are strings; tolerate mixed int/str IDs by trying both.
            forecast = forecasts.get(market_id) or forecasts.get(_denormalize_market_id(market_id)) or {}
            decision = decisions.get(market_id) or decisions.get(_denormalize_market_id(market_id)) or {}
            line = _format_history_line(
                tick_id=entry.tick_ts,
                p_yes=_as_float(forecast.get("p_yes")),
                yes_mark=candidate_marks.get(market_id),
                recommendation=decision.get("recommendation"),
                size_usd=_as_float(decision.get("size_usd")),
            )
            per_market.setdefault(market_id, deque(maxlen=market_history_limit)).append(line)
            last_tick_by_market[market_id] = entry.tick_ts

    if not per_market:
        return MemoryContext(summary="", by_market={})

    ordered_markets = sorted(
        per_market.keys(),
        key=lambda m: last_tick_by_market[m],
        reverse=True,
    )

    by_market: dict[str, str] = {}
    for market_id in ordered_markets:
        history_lines = list(per_market[market_id])
        compact = " | ".join(history_lines)
        by_market[market_id] = f"Recent history for {market_id}: {compact}"

    summary_lines = ["Recent memory (distilled):"]
    for market_id in ordered_markets[:max_markets]:
        compact = by_market[market_id].split(": ", 1)[1]
        line = f"- {market_id}: {compact}"
        next_summary = "\n".join(summary_lines + [line])
        if len(next_summary) > max_chars:
            break
        summary_lines.append(line)

    summary = "\n".join(summary_lines) if len(summary_lines) > 1 else ""
    return MemoryContext(summary=summary, by_market=by_market)


def _extract_marks(candidates: Any, active_markets: set[str]) -> dict[str, float]:
    marks: dict[str, float] = {}
    if not isinstance(candidates, list):
        return marks
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        raw_market_id = candidate.get("market_id")
        if raw_market_id is None:
            continue
        market_id = _normalize_market_id(raw_market_id)
        if market_id not in active_markets:
            continue
        yes_mark = _as_float(candidate.get("yes_mark"))
        if yes_mark is not None:
            marks[market_id] = yes_mark
    return marks


def _format_history_line(
    tick_id: datetime,
    p_yes: float | None,
    yes_mark: float | None,
    recommendation: Any,
    size_usd: float | None,
) -> str:
    tick_str = tick_id.strftime("%m-%d %H:%M")
    p_str = f"{p_yes:.2f}" if p_yes is not None else "-"
    m_str = f"{yes_mark:.2f}" if yes_mark is not None else "-"
    rec = recommendation if isinstance(recommendation, str) else "HOLD"
    size_str = f"${size_usd:.0f}" if size_usd is not None else "-"
    return f"{tick_str} p={p_str} m={m_str} a={rec} s={size_str}"


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _normalize_market_id(value: Any) -> str:
    return str(value)


def _denormalize_market_id(value: str) -> int | str:
    # Backward-compat for historical payloads where dict keys were numeric.
    try:
        return int(value)
    except (TypeError, ValueError):
        return value
