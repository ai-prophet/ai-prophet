from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import count

from ai_prophet.main import cli
from ai_prophet_core.forecast.schemas import Event
from click.testing import CliRunner


def test_predict_skips_market_with_malformed_agent_response(monkeypatch, tmp_path):
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
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


def test_predict_accepts_probability_distribution_response(monkeypatch, tmp_path):
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    events_path.write_text(json.dumps([
        {
            "market_ticker": "TEST-MULTI",
            "close_time": close_time,
        },
    ]))

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "probabilities": [
                    {"market": "Pittsburgh", "probability": 68},
                    {"market": "Atlanta", "probability": 32},
                ]
            }

    def fake_post(*_args, **_kwargs):
        return FakeResponse()

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
    assert "TEST-MULTI: probabilities=[Pittsburgh=0.680, Atlanta=0.320]" in result.output

    submission = json.loads(output_path.read_text())
    prediction = submission["predictions"][0]
    assert prediction["market_ticker"] == "TEST-MULTI"
    assert prediction["probabilities"] == [
        {"market": "Pittsburgh", "probability": 0.68},
        {"market": "Atlanta", "probability": 0.32},
    ]
    assert "p_yes" not in prediction


def test_retrieve_defaults_to_dataset_source(monkeypatch, tmp_path):
    output_path = tmp_path / "events.json"
    captured = {}

    def fake_retrieve_dataset_events(**kwargs):
        captured.update(kwargs)
        return (
            [
                Event(
                    event_ticker="manual-001",
                    market_ticker="task-001",
                    title="Will the launch happen?",
                    category="Science and Technology",
                    rules="Resolution criteria",
                    close_time=datetime(2026, 5, 13, tzinfo=UTC),
                    outcomes=["Yes", "No"],
                )
            ],
            "sample-sports",
            "v1.0.0",
        )

    monkeypatch.setattr(
        "ai_prophet.forecast.main.retrieve_dataset_events",
        fake_retrieve_dataset_events,
    )

    result = CliRunner().invoke(
        cli,
        ["forecast", "retrieve", "--output", str(output_path)],
    )

    assert result.exit_code == 0
    assert captured["dataset"] is None
    assert captured["release_id"] is None
    assert "Retrieved 1 events from sample-sports/v1.0.0" in result.output
    payload = json.loads(output_path.read_text())
    assert payload[0]["market_ticker"] == "task-001"
    assert payload[0]["outcomes"] == ["Yes", "No"]


def test_retrieve_rejects_kalshi_source_flag():
    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "retrieve",
            "--source",
            "kalshi",
        ],
    )

    assert result.exit_code != 0
    assert "No such option: --source" in result.output


def test_forecast_submit_command_is_not_available():
    result = CliRunner().invoke(cli, ["forecast", "submit", "--help"])

    assert result.exit_code != 0
    assert "No such command 'submit'" in result.output


def test_predict_strategy_flag_routes_to_correct_module(monkeypatch, tmp_path):
    """--strategy ensemble must call ai_prophet.forecast.ensemble_agent.predict."""
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    events_path.write_text(
        json.dumps([{"market_ticker": "STRAT-TEST", "close_time": close_time}])
    )

    # Stub only the ensemble agent's predict; if the CLI accidentally calls
    # example_agent.predict instead, p_yes won't match the sentinel value.
    monkeypatch.setattr(
        "ai_prophet.forecast.ensemble_agent.predict",
        lambda _event: {"p_yes": 0.61, "rationale": "stubbed ensemble"},
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--strategy",
            "ensemble",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "STRAT-TEST: p_yes=0.610" in result.output


def test_predict_strategy_example_routes_to_example_agent(monkeypatch, tmp_path):
    events_path = tmp_path / "events.json"
    output_path = tmp_path / "submission.json"
    close_time = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    events_path.write_text(
        json.dumps([{"market_ticker": "EX-TEST", "close_time": close_time}])
    )

    # The example agent constructs an Anthropic client at call time; stub the
    # predict() function so we don't need a real API key.
    monkeypatch.setattr(
        "ai_prophet.forecast.example_agent.predict",
        lambda _event: {"p_yes": 0.42, "rationale": "stubbed example"},
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--strategy",
            "example",
            "--output",
            str(output_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "EX-TEST: p_yes=0.420" in result.output


def test_predict_rejects_strategy_with_local(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text("[]")

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--strategy",
            "ensemble",
            "--local",
            "ai_prophet.forecast.example_agent",
        ],
    )

    assert result.exit_code != 0
    assert "only one of" in result.output.lower()


def test_predict_requires_a_source(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text("[]")

    result = CliRunner().invoke(
        cli,
        ["forecast", "predict", "--events", str(events_path)],
    )

    assert result.exit_code != 0
    assert "provide one of" in result.output.lower()


def test_predict_strategy_rejects_unknown_choice(tmp_path):
    events_path = tmp_path / "events.json"
    events_path.write_text("[]")

    result = CliRunner().invoke(
        cli,
        [
            "forecast",
            "predict",
            "--events",
            str(events_path),
            "--strategy",
            "bogus",
        ],
    )

    assert result.exit_code != 0
    # click's Choice validator emits this exact phrase.
    assert "invalid value for '--strategy'" in result.output.lower()
