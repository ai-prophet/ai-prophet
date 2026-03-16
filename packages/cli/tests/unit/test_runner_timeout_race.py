from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace

from ai_prophet.trade.runner import ExperimentRunner


class _FakeMarket:
    def model_dump(self):
        return {
            "market_id": "kalshi:TEST-MARKET",
            "question": "Will this resolve YES?",
            "description": "test",
            "resolution_time": datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            "quote": {
                "best_bid": "0.48",
                "best_ask": "0.52",
                "volume_24h": 1000.0,
                "ts": datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            },
        }


def test_process_tick_timeout_ignores_late_finalize(monkeypatch):
    finalize_calls: list[tuple[int, str]] = []

    api = SimpleNamespace(
        get_candidates=lambda *_args, **_kwargs: SimpleNamespace(
            markets=[_FakeMarket()],
            data_asof_ts=datetime(2026, 3, 1, 11, 55, tzinfo=UTC),
            candidate_set_id="snap-1",
        ),
        finalize_participant=lambda _exp_id, idx, _tick_id, status, **_kwargs: finalize_calls.append(
            (idx, status)
        ),
    )

    runner = ExperimentRunner(
        api_url="http://example.com",
        api_key=None,
        experiment_slug="timeout-race",
        models=[],
        build_pipeline=None,
    )
    runner.api = api
    runner.experiment_id = "exp-1"
    runner.participants = {0: {"model": "openai:gpt-5", "rep": 0, "participant_idx": 0}}

    def late_participant_finalize(idx: int, tick_id: str, *_args):
        time.sleep(0.05)
        runner._finalize(idx, tick_id, "COMPLETED")

    monkeypatch.setattr("ai_prophet.trade.runner.PARTICIPANT_TICK_BUDGET_SEC", 0.01)
    monkeypatch.setattr(runner, "_process_participant", late_participant_finalize)

    runner._process_tick("2026-03-01T12:00:00+00:00", "snap-1")

    # TIMEOUT should be persisted; late COMPLETED is dropped by timeout guard.
    assert finalize_calls == [(0, "TIMEOUT")]

