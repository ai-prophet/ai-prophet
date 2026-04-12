from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from itertools import count

from click.testing import CliRunner

from ai_prophet.main import cli


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
