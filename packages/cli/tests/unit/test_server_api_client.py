from __future__ import annotations

import httpx
import pytest
from ai_prophet_core.client import APIConnectionError, ServerAPIClient


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


def test_request_raises_connection_error_after_transport_exhaustion(monkeypatch):
    client = ServerAPIClient("https://example.test", max_retries=2, retry_backoff=0.0)

    def always_fails(_method, _path, **_kwargs):
        raise httpx.RemoteProtocolError("server dropped connection")

    monkeypatch.setattr(client.client, "request", always_fails)
    monkeypatch.setattr(client, "_reset_client", lambda: None)
    monkeypatch.setattr("ai_prophet_core.client.time.sleep", lambda *_args, **_kwargs: None)

    with pytest.raises(APIConnectionError):
        client._request("GET", "/health")
