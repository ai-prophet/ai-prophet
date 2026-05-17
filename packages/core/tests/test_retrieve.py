"""Tests for the forecast event-selection logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

from ai_prophet_core.forecast.retrieve import select_events


def _market(ticker: str, event_ticker: str, *, volume_24h: float = 1000.0) -> dict[str, Any]:
    """Build a minimal Kalshi-shaped market dict."""
    close_time = (datetime.now(UTC) + timedelta(hours=48)).isoformat().replace("+00:00", "Z")
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": f"Market {ticker}",
        "subtitle": None,
        "description": None,
        "rules_primary": "Test rules",
        "rules": None,
        "close_time": close_time,
        "volume_24h_fp": str(volume_24h),
    }


def _make_client(markets: list[dict], events: list[dict], per_event: dict[str, dict] | None = None):
    """Construct a mock client that mimics KalshiForecastClient."""
    client = MagicMock()
    client.get_markets.return_value = markets
    client.get_events.return_value = events
    if per_event:
        client.get_event.side_effect = lambda et: per_event.get(et)
    else:
        client.get_event.return_value = None
    return client


def test_select_events_uses_bulk_event_map_when_present():
    """When /events?status=open contains the event, bulk mapping is used."""
    markets = [_market("MKT-A", "EVT-1"), _market("MKT-B", "EVT-1")]
    events = [{"event_ticker": "EVT-1", "category": "Politics"}]
    client = _make_client(markets, events)

    selected = select_events(
        client,
        datetime.now(UTC) + timedelta(hours=72),
        events_per_category=5,
        categories=["Politics"],
    )

    assert len(selected) == 2
    assert all(e.category == "Politics" for e in selected)
    # get_event should NOT have been called — bulk map covered both.
    client.get_event.assert_not_called()


def test_select_events_falls_back_to_per_event_lookup_when_bulk_misses():
    """When /events feed doesn't include the event, per-event lookup recovers it."""
    markets = [_market("MKT-A", "EVT-MISSING")]
    events: list[dict] = []  # bulk feed is empty
    per_event = {"EVT-MISSING": {"event_ticker": "EVT-MISSING", "category": "Politics"}}
    client = _make_client(markets, events, per_event=per_event)

    selected = select_events(
        client,
        datetime.now(UTC) + timedelta(hours=72),
        events_per_category=5,
        categories=["Politics"],
    )

    assert len(selected) == 1
    assert selected[0].category == "Politics"
    client.get_event.assert_called_once_with("EVT-MISSING")


def test_select_events_caches_per_event_results():
    """Multiple markets sharing the same missing event_ticker only fetch once."""
    markets = [_market("MKT-A", "EVT-MISSING"), _market("MKT-B", "EVT-MISSING")]
    events: list[dict] = []
    per_event = {"EVT-MISSING": {"event_ticker": "EVT-MISSING", "category": "Politics"}}
    client = _make_client(markets, events, per_event=per_event)

    selected = select_events(
        client,
        datetime.now(UTC) + timedelta(hours=72),
        events_per_category=5,
        categories=["Politics"],
    )

    assert len(selected) == 2
    client.get_event.assert_called_once_with("EVT-MISSING")


def test_select_events_skips_when_per_event_lookup_returns_none():
    """If the per-event lookup also fails, the market is dropped (same as before)."""
    markets = [_market("MKT-A", "EVT-MISSING")]
    events: list[dict] = []
    client = _make_client(markets, events, per_event={})  # lookup returns None

    selected = select_events(
        client,
        datetime.now(UTC) + timedelta(hours=72),
        events_per_category=5,
        categories=["Politics"],
    )

    assert selected == []
