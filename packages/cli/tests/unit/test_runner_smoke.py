from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from ai_prophet.trade.agent.pipeline import PipelineResult
from ai_prophet.trade.runner import ExperimentRunner
from ai_prophet_core.arena import TickLease


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


def test_runner_smoke_executes_single_tick_end_to_end(tmp_path):
    betting_engine = Mock()
    pipeline = Mock()
    pipeline.execute.return_value = PipelineResult(
        intents=[
            {
                "market_id": "kalshi:TEST-MARKET",
                "action": "BUY",
                "side": "YES",
                "shares": "10",
            }
        ],
        forecasts={
            "kalshi:TEST-MARKET": {
                "p_yes": 0.72,
                "rationale": "forecast edge",
            }
        },
        reasoning={
            "review": [{"market_id": "kalshi:TEST-MARKET"}],
            "search": {"kalshi:TEST-MARKET": {"summary": "test"}},
            "forecasts": {"kalshi:TEST-MARKET": {"p_yes": 0.72}},
            "decisions": {"kalshi:TEST-MARKET": {"recommendation": "BUY_YES"}},
        },
    )

    runner = ExperimentRunner(
        api_url="http://example.com",
        api_key="server-key",
        experiment_slug="runner-smoke",
        models=[{"model": "openai:gpt-5", "rep": 0}],
        build_pipeline=lambda _cfg: pipeline,
        publish_reasoning=True,
        betting_engine=betting_engine,
        memory_dir=tmp_path,
    )

    leases = iter([
        TickLease(
            available=True,
            tick_id="2026-03-09T12:00:00+00:00",
            candidate_set_id="claim-snapshot",
        ),
        TickLease(available=False, reason="experiment_completed"),
    ])
    captured: dict[str, object] = {
        "submitted_intents": [],
        "finalize_calls": [],
        "completed_ticks": [],
    }

    def fake_create_experiment(*, slug, config_hash, config_json, n_ticks):
        runner.session.experiment_id = "exp-1"
        captured["experiment"] = {
            "slug": slug,
            "config_hash": config_hash,
            "config_json": config_json,
            "n_ticks": n_ticks,
        }
        return SimpleNamespace(created=True)

    def fake_upsert_participant(*, model, rep, starting_cash):
        captured["participant"] = {
            "model": model,
            "rep": rep,
            "starting_cash": starting_cash,
        }
        return SimpleNamespace(participant_idx=0)

    def fake_claim_tick(*, lease_sec=600):
        claim_calls = int(captured.get("claim_calls", 0)) + 1
        captured["claim_calls"] = claim_calls
        captured[f"claim_{claim_calls}"] = lease_sec
        return next(leases)

    def fake_load_candidates(lease):
        return SimpleNamespace(
            lease=TickLease(
                available=lease.available,
                tick_id=lease.tick_id,
                candidate_set_id="authoritative-snapshot",
                reason=lease.reason,
                retry_after_sec=lease.retry_after_sec,
            ),
            candidates=SimpleNamespace(
                markets=[_FakeMarket()],
                data_asof_ts=datetime(2026, 3, 9, 11, 55, tzinfo=UTC),
                candidate_set_id="authoritative-snapshot",
            ),
        )

    def fake_put_plan(lease, idx, plan_json):
        captured["put_plan"] = {
            "experiment_id": runner.session.experiment_id,
            "participant_idx": idx,
            "tick_id": lease.tick_id,
            "candidate_set_id": lease.candidate_set_id,
            "plan_json": plan_json,
        }
        return SimpleNamespace(plan_json=plan_json, already_persisted=False)

    def fake_submit_intents(lease, idx, intents):
        captured["submitted_intents"] = [
            {
                "tick_id": lease.tick_id,
                "participant_idx": idx,
                "market_id": intent.market_id,
                "action": intent.action,
                "side": intent.side,
                "shares": intent.shares,
            }
            for intent in intents
        ]
        return SimpleNamespace(accepted=1, rejected=0, rejections=[], fills=[])

    def fake_finalize(lease, idx, status, **kwargs):
        captured["finalize_calls"].append(
            {
                "tick_id": lease.tick_id,
                "participant_idx": idx,
                "status": status,
                "kwargs": kwargs,
            }
        )

    def fake_complete_tick(lease):
        captured["completed_ticks"].append(lease.tick_id)

    def fake_close():
        captured["session_closed"] = True

    runner.session.create_experiment = fake_create_experiment
    runner.session.upsert_participant = fake_upsert_participant
    runner.session.claim_tick = fake_claim_tick
    runner.session.load_candidates = fake_load_candidates
    runner.session.get_portfolio = lambda _idx: None
    runner.session.put_plan = fake_put_plan
    runner.session.submit_intents = fake_submit_intents
    runner.session.finalize = fake_finalize
    runner.session.complete_tick = fake_complete_tick
    runner.session.close = fake_close

    runner.run()

    assert captured["experiment"] == {
        "slug": "runner-smoke",
        "config_hash": runner.config_hash,
        "config_json": {},
        "n_ticks": 96,
    }
    assert captured["participant"] == {
        "model": "openai:gpt-5",
        "rep": 0,
        "starting_cash": 10000.0,
    }
    assert captured["claim_calls"] == 2
    assert captured["put_plan"] == {
        "experiment_id": "exp-1",
        "participant_idx": 0,
        "tick_id": "2026-03-09T12:00:00+00:00",
        "candidate_set_id": "authoritative-snapshot",
        "plan_json": {
            "intents": [
                {
                    "market_id": "kalshi:TEST-MARKET",
                    "action": "BUY",
                    "side": "YES",
                    "shares": "10",
                }
            ],
            "tick_id": "2026-03-09T12:00:00+00:00",
            "candidate_set_id": "authoritative-snapshot",
            "reasoning": pipeline.execute.return_value.reasoning,
        },
    }
    assert captured["submitted_intents"] == [
        {
            "tick_id": "2026-03-09T12:00:00+00:00",
            "participant_idx": 0,
            "market_id": "kalshi:TEST-MARKET",
            "action": "BUY",
            "side": "YES",
            "shares": "10",
        }
    ]
    assert captured["finalize_calls"] == [
        {
            "tick_id": "2026-03-09T12:00:00+00:00",
            "participant_idx": 0,
            "status": "COMPLETED",
            "kwargs": {},
        }
    ]
    assert captured["completed_ticks"] == ["2026-03-09T12:00:00+00:00"]
    assert captured["session_closed"] is True

    pipeline.execute.assert_called_once()
    pipeline.close.assert_called_once()
    betting_engine.process_forecasts.assert_called_once_with(
        tick_ts=datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:TEST-MARKET": 0.72},
        market_prices={"kalshi:TEST-MARKET": (0.52, 0.52)},
        source="openai:gpt-5",
    )
