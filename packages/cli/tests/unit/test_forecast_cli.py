from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from itertools import count

from click.testing import CliRunner

from ai_prophet.forecast.main import _extract_trade_prices
from ai_prophet.main import cli


def test_extract_trade_prices_derives_missing_side_from_complement():
    yes_ask, no_ask = _extract_trade_prices({"yes_ask": "0.61"})
    assert yes_ask == 0.61
    assert no_ask == 0.39


def test_predict_trade_uses_explicit_no_ask(monkeypatch, tmp_path):
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    events_path.write_text(json.dumps([{
        "market_ticker": "TEST-1",
        "close_time": close_time,
        "yes_ask": 0.61,
        "no_ask": 0.47,
    }]))

    captured: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"p_yes": 0.72, "rationale": "test"}

    class FakeEngine:
        def trade_from_forecast(self, **kwargs):
            captured.update(kwargs)
            return None

        def close(self):
            captured["closed"] = True

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.post",
        lambda *_args, **_kwargs: FakeResponse(),
    )
    monkeypatch.setattr(
        "ai_prophet.forecast.main._create_betting_engine",
        lambda paper: FakeEngine(),
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--agent-url",
            "http://agent.test/predict",
            "--trade",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert captured["market_id"] == "kalshi:TEST-1"
    assert captured["p_yes"] == 0.72
    assert captured["yes_ask"] == 0.61
    assert captured["no_ask"] == 0.47
    assert captured["closed"] is True
    assert output_path.exists()


def test_predict_skips_market_with_malformed_agent_response(monkeypatch, tmp_path):
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    events_path.write_text(json.dumps([
        {
            "market_ticker": "TEST-BAD",
            "close_time": close_time,
        },
        {
            "market_ticker": "TEST-GOOD",
            "close_time": close_time,
        },
    ]))

    responses = [
        {"rationale": "missing probability"},
        {"p_yes": 0.72, "rationale": "valid"},
    ]
    call_idx = count()

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    def fake_post(*_args, **_kwargs):
        return FakeResponse(responses[next(call_idx)])

    monkeypatch.setattr("ai_prophet.forecast.main.requests.post", fake_post)

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--agent-url",
            "http://agent.test/predict",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0
    assert "TEST-BAD: SKIPPED" in result.output
    assert "TEST-GOOD: p_yes=0.720" in result.output

    submission = json.loads(output_path.read_text())
    assert [p["market_ticker"] for p in submission["predictions"]] == ["TEST-GOOD"]
