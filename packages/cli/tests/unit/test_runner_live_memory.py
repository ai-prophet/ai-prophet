import logging
from types import SimpleNamespace

from ai_prophet.trade.runner import ExperimentRunner


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

