"""Tests for the capture-snapshots flow."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from ai_prophet_core.forecast.capture import (
    SNAPSHOT_FIELDS,
    append_snapshots_jsonl,
    capture_snapshots,
)


def _market(ticker: str, event_ticker: str, vol_24h: float = 1000.0) -> dict:
    return {
        "ticker": ticker,
        "event_ticker": event_ticker,
        "title": f"Market {ticker}",
        "subtitle": None,
        "description": None,
        "rules_primary": "Test rules",
        "close_time": "2026-12-31T23:59:59Z",
        "yes_bid_dollars": "0.40",
        "yes_ask_dollars": "0.42",
        "yes_bid_size_fp": "100",
        "yes_ask_size_fp": "120",
        "volume_24h_fp": str(vol_24h),
        "volume_fp": str(vol_24h * 5),
        "open_interest_fp": "500",
        "liquidity_dollars": "200",
        "last_price_dollars": "0.41",
        "previous_yes_bid_dollars": "0.39",
        "previous_yes_ask_dollars": "0.43",
        "previous_price_dollars": "0.40",
        "no_bid_dollars": "0.58",
        "no_ask_dollars": "0.60",
        "open_time": "2026-05-13T12:00:00Z",
        "expiration_time": "2026-12-31T23:59:59Z",
        "status": "active",
        "updated_time": "2026-05-14T15:00:00Z",
        "result": "",
    }


def _make_client(markets: list[dict], events: list[dict]):
    client = MagicMock()
    client.get_markets.return_value = markets
    client.get_events.return_value = events
    return client


def test_captures_top_n_per_category_by_volume():
    markets = [
        _market("M-A", "EVT-1", vol_24h=500),
        _market("M-B", "EVT-1", vol_24h=2000),
        _market("M-C", "EVT-1", vol_24h=1500),
        _market("M-D", "EVT-2", vol_24h=300),
    ]
    events = [
        {"event_ticker": "EVT-1", "category": "Politics"},
        {"event_ticker": "EVT-2", "category": "Sports"},
    ]
    client = _make_client(markets, events)

    snaps = capture_snapshots(client, top_per_category=2)

    pol = [s for s in snaps if s["event"]["category"] == "Politics"]
    sp = [s for s in snaps if s["event"]["category"] == "Sports"]
    assert len(pol) == 2
    assert len(sp) == 1
    # Top-by-volume order respected
    assert pol[0]["event"]["market_ticker"] == "M-B"
    assert pol[1]["event"]["market_ticker"] == "M-C"


def test_filters_mve_markets():
    markets = [
        _market("KXMVECROSSCATEGORY-12345", "EVT-1", vol_24h=10000),
        _market("M-REAL", "EVT-1", vol_24h=100),
    ]
    events = [{"event_ticker": "EVT-1", "category": "Politics"}]
    client = _make_client(markets, events)

    snaps = capture_snapshots(client)
    assert len(snaps) == 1
    assert snaps[0]["event"]["market_ticker"] == "M-REAL"


def test_filters_zero_volume_markets():
    markets = [
        _market("M-VOL", "EVT-1", vol_24h=100),
        _market("M-NOVOL", "EVT-1", vol_24h=0),
    ]
    events = [{"event_ticker": "EVT-1", "category": "Politics"}]
    client = _make_client(markets, events)

    snaps = capture_snapshots(client)
    assert len(snaps) == 1
    assert snaps[0]["event"]["market_ticker"] == "M-VOL"


def test_skips_uncategorized_markets():
    markets = [_market("M-A", "EVT-MISSING", vol_24h=100)]
    events: list[dict] = []  # bulk feed empty → can't categorize
    client = _make_client(markets, events)

    snaps = capture_snapshots(client)
    assert snaps == []


def test_categories_filter_restricts_to_allowed():
    markets = [
        _market("M-POL", "EVT-1", vol_24h=100),
        _market("M-SPO", "EVT-2", vol_24h=100),
    ]
    events = [
        {"event_ticker": "EVT-1", "category": "Politics"},
        {"event_ticker": "EVT-2", "category": "Sports"},
    ]
    client = _make_client(markets, events)

    snaps = capture_snapshots(client, categories=["Politics"])
    assert {s["event"]["category"] for s in snaps} == {"Politics"}


def test_snapshot_includes_all_book_fields():
    markets = [_market("M-A", "EVT-1", vol_24h=100)]
    events = [{"event_ticker": "EVT-1", "category": "Politics"}]
    client = _make_client(markets, events)

    snaps = capture_snapshots(client)
    assert len(snaps) == 1
    snap = snaps[0]["market_snapshot"]
    for field in SNAPSHOT_FIELDS:
        assert field in snap, f"missing field {field}"


def test_append_jsonl_creates_file_and_appends(tmp_path: Path):
    out = tmp_path / "snaps.jsonl"
    snaps_a = [{"a": 1}, {"a": 2}]
    snaps_b = [{"b": 3}]

    assert append_snapshots_jsonl(snaps_a, out) == 2
    assert append_snapshots_jsonl(snaps_b, out) == 1

    lines = out.read_text().strip().splitlines()
    assert len(lines) == 3
    assert json.loads(lines[0]) == {"a": 1}
    assert json.loads(lines[2]) == {"b": 3}


def test_append_jsonl_creates_parent_dirs(tmp_path: Path):
    out = tmp_path / "nested" / "deep" / "snaps.jsonl"
    append_snapshots_jsonl([{"x": 1}], out)
    assert out.exists()
