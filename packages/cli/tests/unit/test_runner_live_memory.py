import logging
from datetime import UTC, datetime
from types import SimpleNamespace

from ai_prophet.trade.core.tick_context import CandidateMarket
from ai_prophet.trade.runner import ExperimentRunner


def test_live_betting_reasoning_shape():
    runner = ExperimentRunner(
        api_url="http://example.com",
        experiment_slug="test_live_memory",
        models=[],
        publish_reasoning=False,
    )
    market = CandidateMarket(
        market_id="m1",
        question="Will event happen?",
        description="desc",
        resolution_time=datetime(2026, 3, 1, tzinfo=UTC),
        yes_bid=0.45,
        yes_ask=0.55,
        yes_mark=0.50,
        no_bid=0.45,
        no_ask=0.55,
        no_mark=0.50,
        volume_24h=1000.0,
        quote_ts=datetime(2026, 2, 20, 5, 30, tzinfo=UTC),
    )
    reasoning = runner._build_live_betting_reasoning(
        candidate_markets=(market,),
        forecasts={"m1": 0.62},
        intents=[
            {
                "market_id": "m1",
                "side": "YES",
                "shares": "10.00",
                "rationale": "Live betting: p_yes=0.620",
            }
        ],
    )

    assert "candidates" in reasoning
    assert "forecasts" in reasoning
    assert "decisions" in reasoning
    assert reasoning["forecasts"]["m1"]["p_yes"] == 0.62
    assert reasoning["decisions"]["m1"]["recommendation"] == "BUY_YES"
    assert reasoning["decisions"]["m1"]["size_usd"] > 0


def test_submit_intents_logs_rejection_reasons(caplog):
    runner = ExperimentRunner(
        api_url="http://example.com",
        experiment_slug="test_rejections",
        models=[],
        publish_reasoning=False,
    )
    runner.experiment_id = "exp-1"
    runner.api = SimpleNamespace(
        submit_trade_intents=lambda **_kwargs: SimpleNamespace(
            accepted=2,
            rejected=1,
            rejections=[
                SimpleNamespace(intent_id="intent-123", reason="insufficient liquidity")
            ],
        )
    )

    with caplog.at_level(logging.WARNING):
        runner._submit_intents(
            idx=0,
            tick_id="2026-03-09T00:30:00+00:00",
            snapshot_id="snapshot-1",
            raw_intents=[
                {"market_id": "m1", "action": "BUY", "side": "YES", "shares": "100"},
                {"market_id": "m2", "action": "BUY", "side": "NO", "shares": "100"},
                {"market_id": "m3", "action": "BUY", "side": "NO", "shares": "100"},
            ],
        )

    assert "Participant 0 trade intent rejected" in caplog.text
    assert "intent-123" in caplog.text
    assert "insufficient liquidity" in caplog.text

