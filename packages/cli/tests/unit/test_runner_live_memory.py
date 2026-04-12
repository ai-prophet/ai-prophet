import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from ai_prophet.trade.agent.pipeline import PipelineError
from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.runner import ExperimentRunner
from ai_prophet_core.arena import TickLease


def test_submit_intents_logs_rejection_reasons(caplog):
    runner = ExperimentRunner(
        api_url="http://example.com",
        api_key=None,
        experiment_slug="test_rejections",
        models=[],
        publish_reasoning=False,
    )
    runner.session.experiment_id = "exp-1"
    runner.session.submit_intents = lambda *_args, **_kwargs: SimpleNamespace(
        accepted=2,
        rejected=1,
        fills=[],
        rejections=[
            SimpleNamespace(intent_id="intent-123", reason="insufficient liquidity")
        ],
    )

    lease = TickLease(
        available=True,
        tick_id="2026-03-09T00:30:00+00:00",
        candidate_set_id="snapshot-1",
    )
    with caplog.at_level(logging.WARNING):
        runner._submit_intents(
            idx=0,
            lease=lease,
            raw_intents=[
                {"market_id": "m1", "action": "BUY", "side": "YES", "shares": "100"},
                {"market_id": "m2", "action": "BUY", "side": "NO", "shares": "100"},
                {"market_id": "m3", "action": "BUY", "side": "NO", "shares": "100"},
            ],
        )

    assert "Participant 0 trade intent rejected" in caplog.text
    assert "intent-123" in caplog.text
    assert "insufficient liquidity" in caplog.text


def test_generate_plan_does_not_process_forecasts_on_action_failure():
    candidate_market = CandidateMarket.from_server_response(
        {
            "market_id": "kalshi:TEST-MARKET",
            "question": "Will this resolve YES?",
            "description": "test",
            "resolution_time": datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
            "quote": {
                "best_bid": "0.55",
                "best_ask": "0.60",
                "volume_24h": 1000.0,
                "ts": datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            },
        }
    )

    class FailingPipeline:
        def execute(self, *_args, **_kwargs):
            raise PipelineError(
                "Stage 'action' failed: boom",
                stage_name="action",
                forecasts={
                    "kalshi:TEST-MARKET": {
                        "p_yes": 0.72,
                        "rationale": "edge",
                    }
                },
            )

        def close(self):
            return None

    betting_engine = Mock()
    runner = ExperimentRunner(
        api_url="http://example.com",
        api_key=None,
        experiment_slug="test_forecast_side_effects",
        models=[],
        build_pipeline=lambda _cfg: FailingPipeline(),
        betting_engine=betting_engine,
    )
    runner.session.experiment_id = "exp-1"
    runner.participants = {
        0: {"model": "openai:gpt-5", "rep": 0, "participant_idx": 0},
    }
    runner.session.get_portfolio = lambda _idx: None

    lease = TickLease(
        available=True,
        tick_id="2026-03-09T12:00:00+00:00",
        candidate_set_id="snap-1",
    )
    tick_shared = {
        "tick_ts": datetime(2026, 3, 9, 12, 0, tzinfo=UTC),
        "candidate_markets": (candidate_market,),
        "data_asof": datetime(2026, 3, 9, 11, 55, tzinfo=UTC),
        "candidate_set_id": "snap-1",
    }

    with pytest.raises(PipelineError, match="Stage 'action' failed"):
        runner._generate_plan(0, lease, tick_shared)

    betting_engine.process_forecasts.assert_not_called()

