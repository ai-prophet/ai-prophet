"""Event selection logic for the forecasting track."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from .schemas import Event

if TYPE_CHECKING:
    from .kalshi_client import KalshiForecastClient

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    "Economics",
    "Politics",
    "Science and Technology",
    "Climate and Weather",
    "Sports",
    "Entertainment",
    "Financials",
    "World",
]


def _market_score(market: dict) -> float:
    """Heuristic score for market selection (higher = more liquid)."""
    raw = market.get("volume_24h_fp") or market.get("volume_24h") or market.get("volume") or 0
    try:
        volume = float(raw)
    except (ValueError, TypeError):
        volume = 0.0
    volume_score = min(volume / 1000.0, 1.0) if volume > 0 else 0.1
    return volume_score


def _parse_close_time(market: dict) -> datetime | None:
    """Extract and parse close_time from a market dict."""
    close_str = market.get("close_time") or market.get("expiration_time") or ""
    if not close_str:
        return None
    try:
        return datetime.fromisoformat(close_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def select_events(
    client: KalshiForecastClient,
    deadline: datetime,
    *,
    events_per_category: int = 3,
    categories: list[str] | None = None,
) -> list[Event]:
    """Select events distributed across categories, closing before deadline.

    Fetches Kalshi events (which carry category info), then fetches markets
    for matching events to get individual market tickers and close times.

    Args:
        client: Kalshi API client.
        deadline: Only include markets closing before this time.
        events_per_category: Max markets to select per category.
        categories: Category list (defaults to DEFAULT_CATEGORIES).

    Returns:
        Flat list of selected Event objects.
    """
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)

    cats = categories or DEFAULT_CATEGORIES
    selected: list[Event] = []
    seen_tickers: set[str] = set()

    min_close_ts = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
    max_close_ts = int(deadline.timestamp())

    # Fetch all open markets closing between now+24h and the deadline
    all_markets = client.get_markets(
        status="open", min_close_ts=min_close_ts, max_close_ts=max_close_ts, limit=1000,
    )

    # Fetch all open events to build event_ticker -> category map
    all_events = client.get_events(status="open")
    event_cat_map: dict[str, str] = {}
    for ev in all_events:
        et = ev.get("event_ticker", "")
        cat = ev.get("category", "")
        if et and cat:
            event_cat_map[et] = cat

    # Group markets by category (looked up via event_ticker)
    markets_by_cat: dict[str, list[dict]] = {}
    for m in all_markets:
        cat = event_cat_map.get(m.get("event_ticker", ""), "")
        if cat:
            markets_by_cat.setdefault(cat, []).append(m)

    for category in cats:
        candidates = []
        for cat_name, mkts in markets_by_cat.items():
            if cat_name.lower() == category.lower():
                for m in mkts:
                    ticker = m.get("ticker", "")
                    if not ticker or ticker in seen_tickers:
                        continue
                    close_time = _parse_close_time(m)
                    candidates.append((m, close_time, ticker))

        if not candidates:
            logger.warning("No markets found for category: %s", category)
            continue

        # Rank by volume heuristic, take top N
        candidates.sort(key=lambda x: _market_score(x[0]), reverse=True)

        for m, close_time, ticker in candidates[:events_per_category]:
            seen_tickers.add(ticker)
            selected.append(
                Event(
                    event_ticker=m.get("event_ticker", ""),
                    market_ticker=ticker,
                    title=m.get("title", ""),
                    subtitle=m.get("subtitle"),
                    description=m.get("description"),
                    category=category,
                    rules=m.get("rules_primary") or m.get("rules"),
                    close_time=close_time,
                )
            )

    logger.info("Selected %d events across %d categories", len(selected), len(cats))
    return selected
