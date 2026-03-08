from datetime import UTC, datetime

from ai_prophet.core.tick_context import CandidateMarket
from ai_prophet.runner import ExperimentRunner


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

