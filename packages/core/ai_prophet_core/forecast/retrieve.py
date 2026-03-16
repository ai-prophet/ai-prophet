"""Event selection logic for the forecasting track."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .schemas import Event

if TYPE_CHECKING:
    from .kalshi_client import KalshiForecastClient

logger = logging.getLogger(__name__)

DEFAULT_CATEGORIES = [
    "Economics",
    "Politics",
    "Science",
    "Climate",
    "Sports",
    "Culture",
    "Tech",
    "Financial",
]


def _market_score(market: dict) -> float:
    """Heuristic score for event selection."""
    volume = market.get("volume_24h") or market.get("volume") or 0
    volume_score = min(volume / 1000.0, 1.0) if volume > 0 else 0.1
    return volume_score


def select_events(
    client: KalshiForecastClient,
    deadline: datetime,
    *,
    events_per_category: int = 3,
    categories: list[str] | None = None,
) -> list[Event]:
    """Select events distributed across categories, closing before deadline.

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

    # Fetch all open markets once, then filter locally per category
    all_markets = client.get_markets(status="open")

    for category in cats:
        candidates = []
        for m in all_markets:
            # Kalshi may use 'category' or 'event_category' fields
            m_cat = (
                m.get("category", "")
                or m.get("event_category", "")
                or ""
            )
            if m_cat.lower() != category.lower():
                continue

            close_str = m.get("close_time") or m.get("expiration_time") or ""
            if not close_str:
                continue

            try:
                close_time = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue

            if close_time > deadline:
                continue

            ticker = m.get("ticker", "")
            if ticker in seen_tickers:
                continue

            candidates.append((m, close_time, ticker))

        # Rank by heuristic and take top N
        candidates.sort(key=lambda x: _market_score(x[0]), reverse=True)

        for m, close_time, ticker in candidates[:events_per_category]:
            seen_tickers.add(ticker)

            selected.append(
                Event(
                    event_ticker=m.get("event_ticker", ""),
                    market_ticker=ticker,
                    title=m.get("title", "") or m.get("question", ""),
                    subtitle=m.get("subtitle"),
                    description=m.get("description"),
                    category=category,
                    rules=m.get("rules_primary") or m.get("rules"),
                    close_time=close_time,
                )
            )

        if not candidates:
            logger.warning("No markets found for category: %s", category)

    logger.info("Selected %d events across %d categories", len(selected), len(cats))
    return selected
