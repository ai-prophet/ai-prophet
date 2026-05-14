"""Snapshot capture for backtest-fixture building.

`select_events` is good for "pull the markets we're going to predict on
this round." `capture_snapshots` is for "snapshot the current state of
those markets so we can score our forecast against the eventual outcome."

Run periodically (e.g., daily) over the eval window. Markets accumulate
in the output JSONL with their current book state; a separate resolver
(see `resolve_captures` below) walks the file later, queries Kalshi for
each market's resolution, and emits a clean (snapshot, outcome) fixture.

Why not just use `select_events`? Two reasons:
  1. `select_events` strips most fields from the market dict to fit the
     forecasting submission contract. Backtest fixtures want the full
     book state (bid, ask, sizes, volume) at capture time.
  2. The capture flow is append-only; selection is per-prediction-round.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from .kalshi_client import KalshiForecastClient

logger = logging.getLogger(__name__)

# Fields we copy from the Kalshi market response into the snapshot.
# Intentionally a superset of what the predict() contract uses, so the
# fixture is general-purpose.
SNAPSHOT_FIELDS = (
    "ticker",
    "event_ticker",
    "status",
    "open_time",
    "close_time",
    "expiration_time",
    "yes_bid_dollars",
    "yes_ask_dollars",
    "no_bid_dollars",
    "no_ask_dollars",
    "yes_bid_size_fp",
    "yes_ask_size_fp",
    "last_price_dollars",
    "previous_yes_bid_dollars",
    "previous_yes_ask_dollars",
    "previous_price_dollars",
    "volume_fp",
    "volume_24h_fp",
    "open_interest_fp",
    "liquidity_dollars",
    "updated_time",
    "result",
)


def _f(d: dict, key: str) -> float:
    try:
        return float(d.get(key, "0") or 0)
    except (ValueError, TypeError):
        return 0.0


def capture_snapshots(
    client: "KalshiForecastClient",
    *,
    close_window_hours: tuple[int, int] = (24, 168),
    top_per_category: int = 5,
    categories: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Snapshot the current state of high-volume open markets across categories.

    Args:
        client: Kalshi API client.
        close_window_hours: (min, max) hours-from-now for market close.
            Default (24, 168) covers ~next-day to one-week markets.
        top_per_category: Take this many markets per category, ranked by
            24h volume.
        categories: If given, restrict to these categories. Otherwise
            include any category Kalshi returns.

    Returns:
        List of snapshot dicts:
        {
          "captured_at": ISO timestamp,
          "event": { event_ticker, market_ticker, title, ..., category },
          "market_snapshot": { all SNAPSHOT_FIELDS from the Kalshi response },
          "result": "" (to be filled in by the resolver)
        }
    """
    captured_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    now = datetime.now(UTC)
    min_ts = int((now + timedelta(hours=close_window_hours[0])).timestamp())
    max_ts = int((now + timedelta(hours=close_window_hours[1])).timestamp())

    markets = client.get_markets(
        status="open", min_close_ts=min_ts, max_close_ts=max_ts, limit=1000
    )
    events_list = client.get_events(status="open")

    event_cat_map: dict[str, str] = {}
    for ev in events_list:
        et = ev.get("event_ticker", "")
        cat = ev.get("category", "")
        if et and cat:
            event_cat_map[et] = cat

    by_cat: dict[str, list[tuple[float, dict]]] = {}
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker or ticker.startswith("KXMVE"):
            # MVE = multivariate / parlay markets; mostly zero-volume noise.
            continue
        vol = _f(m, "volume_24h_fp")
        if vol <= 0:
            continue
        event_ticker = m.get("event_ticker", "")
        cat = event_cat_map.get(event_ticker, "")
        if not cat:
            continue
        if categories and cat not in categories:
            continue
        by_cat.setdefault(cat, []).append((vol, m))

    snapshots: list[dict[str, Any]] = []
    for cat, items in by_cat.items():
        items.sort(key=lambda t: t[0], reverse=True)
        for _, m in items[:top_per_category]:
            snapshots.append(
                {
                    "captured_at": captured_at,
                    "event": {
                        "event_ticker": m.get("event_ticker", ""),
                        "market_ticker": m["ticker"],
                        "title": m.get("title", ""),
                        "subtitle": m.get("subtitle"),
                        "description": m.get("description"),
                        "category": cat,
                        "rules": m.get("rules_primary") or m.get("rules"),
                        "close_time": m.get("close_time", ""),
                    },
                    "market_snapshot": {k: m.get(k) for k in SNAPSHOT_FIELDS},
                    "result": "",
                }
            )
    return snapshots


def append_snapshots_jsonl(snapshots: Iterable[dict[str, Any]], path: str | Path) -> int:
    """Append snapshots to a JSONL file (one per line). Returns count written."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with p.open("a") as f:
        for snap in snapshots:
            f.write(json.dumps(snap) + "\n")
            n += 1
    return n
