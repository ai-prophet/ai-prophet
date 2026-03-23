"""Unit tests for BellwetherClient."""

from __future__ import annotations

import httpx
import pytest

from ai_prophet_core.bellwether_client import (
    BellwetherAPIError,
    BellwetherClient,
    DEFAULT_BELLWETHER_URL,
)
from ai_prophet_core.bellwether_models import (
    BellwetherEventMetrics,
    BellwetherSearchResponse,
)


def test_constructor_defaults():
    client = BellwetherClient()
    assert client.base_url == DEFAULT_BELLWETHER_URL
    assert client.timeout == 10
    assert client.max_retries == 2
    assert client.retry_backoff == 0.5
    assert client.api_key is None


def test_constructor_overrides():
    client = BellwetherClient(
        base_url="https://custom.test",
        timeout=5,
        max_retries=3,
        retry_backoff=1.0,
        api_key="test-key",
    )
    assert client.base_url == "https://custom.test"
    assert client.timeout == 5
    assert client.max_retries == 3
    assert client.api_key == "test-key"


def test_bearer_auth_header_set_when_api_key_provided():
    client = BellwetherClient(api_key="my-secret")
    assert client.client.headers.get("authorization") == "Bearer my-secret"


def test_no_auth_header_when_no_api_key():
    client = BellwetherClient()
    assert "authorization" not in client.client.headers


def test_search_markets_parses_response(monkeypatch):
    client = BellwetherClient(max_retries=1, retry_backoff=0.0)

    payload = {
        "results": [
            {
                "ticker": "SENATE_2026",
                "title": "Will Republicans win the Senate in 2026?",
                "category": "politics",
                "volume_usd": 500000.0,
                "is_matched": True,
                "platforms": ["polymarket", "kalshi"],
            }
        ],
        "total": 1,
        "query": "senate 2026",
        "category": None,
    }

    def fake_get(path, params=None):
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(client.client, "get", fake_get)

    result = client.search_markets("senate 2026")
    assert isinstance(result, BellwetherSearchResponse)
    assert len(result.results) == 1
    assert result.results[0].ticker == "SENATE_2026"
    assert result.total == 1


def test_get_event_metrics_converts_ticker_to_slug(monkeypatch):
    client = BellwetherClient(max_retries=1, retry_backoff=0.0)
    captured_paths: list[str] = []

    payload = {
        "ticker": "SENATE_2026",
        "title": "Senate 2026",
        "bellwether_price": 0.47,
        "price_tier": "mid",
        "price_label": "47%",
        "platform_prices": {"polymarket": 0.50, "kalshi": 0.42},
        "robustness": {"cost_to_move_5c": 180000.0, "reportability": "reportable"},
        "vwap_details": {"trade_count": 1200, "total_volume": 50000.0},
        "fetched_at": "2026-03-10T12:00:00Z",
    }

    def fake_get(path, params=None):
        captured_paths.append(path)
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(client.client, "get", fake_get)

    result = client.get_event_metrics("SENATE_2026")
    assert isinstance(result, BellwetherEventMetrics)
    assert result.bellwether_price == 0.47
    assert result.platform_prices.polymarket == 0.50
    assert result.platform_prices.kalshi == 0.42
    assert result.robustness.cost_to_move_5c == 180000.0
    # Verify slug conversion
    assert captured_paths[0] == "/api/events/senate-2026/metrics"


def test_retries_on_server_error(monkeypatch):
    client = BellwetherClient(max_retries=3, retry_backoff=0.0)
    calls = {"n": 0}

    def fake_get(path, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="service unavailable")
        return httpx.Response(200, json={"results": [], "total": 0, "query": "test"})

    monkeypatch.setattr(client.client, "get", fake_get)
    monkeypatch.setattr("ai_prophet_core.bellwether_client.time.sleep", lambda *_: None)

    result = client.search_markets("test")
    assert calls["n"] == 2
    assert result.total == 0


def test_raises_on_client_error(monkeypatch):
    client = BellwetherClient(max_retries=1, retry_backoff=0.0)

    def fake_get(path, params=None):
        return httpx.Response(404, text="not found")

    monkeypatch.setattr(client.client, "get", fake_get)

    with pytest.raises(BellwetherAPIError) as exc_info:
        client.search_markets("nonexistent")
    assert exc_info.value.status_code == 404


def test_raises_on_timeout(monkeypatch):
    client = BellwetherClient(max_retries=1, retry_backoff=0.0)

    def fake_get(path, params=None):
        raise httpx.TimeoutException("connection timed out")

    monkeypatch.setattr(client.client, "get", fake_get)
    monkeypatch.setattr("ai_prophet_core.bellwether_client.time.sleep", lambda *_: None)

    with pytest.raises(BellwetherAPIError, match="Timeout"):
        client.search_markets("test")


def test_context_manager():
    client = BellwetherClient()
    with client as c:
        assert c is client
    # After exit, client should be closed — verify no error on double close
    client.close()


def test_search_markets_passes_params(monkeypatch):
    client = BellwetherClient(max_retries=1, retry_backoff=0.0)
    captured_params: list = []

    def fake_get(path, params=None):
        captured_params.append(params)
        return httpx.Response(200, json={"results": [], "total": 0, "query": "q"})

    monkeypatch.setattr(client.client, "get", fake_get)

    client.search_markets("test query", category="politics", limit=3)
    assert captured_params[0]["q"] == "test query"
    assert captured_params[0]["category"] == "politics"
    assert captured_params[0]["limit"] == 3
