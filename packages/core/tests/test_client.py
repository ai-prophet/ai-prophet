from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from ai_prophet_core.client import APIConnectionError, APIValidationError, ServerAPIClient


def test_constructor_defaults_match_legacy_config():
    client = ServerAPIClient("https://example.test")
    assert client.api_key is None
    assert client.timeout == 30
    assert client.max_retries == 3
    assert client.retry_backoff == 1.0


def test_constructor_overrides_are_respected():
    client = ServerAPIClient(
        "https://example.test",
        api_key="test-key",
        timeout=12,
        max_retries=7,
        retry_backoff=0.25,
    )
    assert client.api_key == "test-key"
    assert client.timeout == 12
    assert client.max_retries == 7
    assert client.retry_backoff == 0.25


def test_http_client_includes_api_key_header():
    client = ServerAPIClient("https://example.test", api_key="test-key")
    assert client.client.headers["X-API-Key"] == "test-key"


def test_request_retries_on_remote_protocol_error(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=3, retry_backoff=0.0)
    calls = {"n": 0, "resets": 0}

    def fake_request(method, path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError("server dropped connection")
        return httpx.Response(200, json={"ok": True})

    def fake_reset():
        calls["resets"] += 1

    monkeypatch.setattr(client.client, "request", fake_request)
    monkeypatch.setattr(client, "_reset_client", fake_reset)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda *_args, **_kwargs: None)

    response = client._request("GET", "/health")

    assert response.status_code == 200
    assert calls["n"] == 2
    assert calls["resets"] == 1


def test_request_retries_on_server_error(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=3, retry_backoff=0.0)
    calls = {"n": 0}

    def fake_request(method, path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="database busy")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client.client, "request", fake_request)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda *_args, **_kwargs: None)

    response = client._request("GET", "/health")
    assert response.status_code == 200
    assert calls["n"] == 2


def test_request_retries_on_rate_limit_with_retry_after(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=3, retry_backoff=0.0)
    calls = {"n": 0}
    sleeps: list[float] = []

    def fake_request(method, path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down", headers={"Retry-After": "2"})
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client.client, "request", fake_request)
    monkeypatch.setattr("ai_prophet_core.client.random.uniform", lambda *_args, **_kwargs: 0.0)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda delay: sleeps.append(delay))

    response = client._request("GET", "/health")

    assert response.status_code == 200
    assert calls["n"] == 2
    assert sleeps == [2.0]


def test_request_retries_on_timeout_exception(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=3, retry_backoff=0.0)
    calls = {"n": 0}

    def fake_request(method, path, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.TimeoutException("read timed out")
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(client.client, "request", fake_request)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda *_args, **_kwargs: None)

    response = client._request("GET", "/health")
    assert response.status_code == 200
    assert calls["n"] == 2


def test_request_raises_connection_error_after_transport_exhaustion(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=2, retry_backoff=0.0)

    def always_fails(_method, _path, **_kwargs):
        raise httpx.RemoteProtocolError("server dropped connection")

    monkeypatch.setattr(client.client, "request", always_fails)
    monkeypatch.setattr(client, "_reset_client", lambda: None)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda *_args, **_kwargs: None)

    with pytest.raises(APIConnectionError):
        client._request("GET", "/health")


def test_health_check_raises_api_validation_error_on_invalid_json(monkeypatch):
    client = ServerAPIClient("https://example.test")
    response = httpx.Response(200, text="not json")

    monkeypatch.setattr(client, "_get", lambda _path: response)

    with pytest.raises(APIValidationError, match="Invalid JSON response"):
        client.health_check()


def test_health_check_raises_api_validation_error_on_schema_mismatch(monkeypatch):
    client = ServerAPIClient("https://example.test")
    response = httpx.Response(200, json={"status": "ok"})

    monkeypatch.setattr(client, "_get", lambda _path: response)

    with pytest.raises(APIValidationError, match="Invalid HealthResponse response payload"):
        client.health_check()


def test_get_market_snapshot_passes_as_of_query(monkeypatch):
    client = ServerAPIClient("https://example.test")
    requested_asof = datetime(2026, 1, 20, 5, 30, tzinfo=UTC)
    captured: dict[str, object] = {}
    response = httpx.Response(
        200,
        json={
            "candidate_set_id": "snap_123",
            "requested_asof_ts": requested_asof.isoformat(),
            "data_asof_ts": "2026-01-20T05:31:39+00:00",
            "market_count": 1,
            "markets": [{
                "market_id": "market_123",
                "question": "Will X happen?",
                "description": "Details...",
                "resolution_time": "2026-02-01T00:00:00+00:00",
                "quote": {
                    "best_bid": "0.45",
                    "best_ask": "0.55",
                    "volume_24h": 1000.0,
                    "ts": "2026-01-20T05:30:00+00:00",
                },
            }],
        },
    )

    def fake_get(path, params=None):
        captured["path"] = path
        captured["params"] = params
        return response

    monkeypatch.setattr(client, "_get", fake_get)

    snapshot = client.get_market_snapshot(requested_asof)

    assert captured["path"] == "/candidates/asof"
    assert captured["params"] == {"as_of_ts": requested_asof.isoformat()}
    assert snapshot.candidate_set_id == "snap_123"
