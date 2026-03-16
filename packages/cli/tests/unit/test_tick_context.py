"""Unit tests for tick context helpers."""

from datetime import UTC, datetime

from ai_prophet.trade.core.tick_context import CandidateMarket


def test_candidate_market_from_server_response_preserves_canonical_fields():
    market = CandidateMarket.from_server_response(
        {
            "market_id": "kalshi:ABC-123",
            "question": "Will X happen?",
            "short_label": "X",
            "description": "Resolution details",
            "resolution_time": datetime(2026, 2, 1, 0, 0, 0, tzinfo=UTC),
            "source": "kalshi",
            "source_url": "https://kalshi.com/markets/ABC",
            "topic": "politics",
            "family": "ABC",
            "quote": {
                "best_bid": "0.45",
                "best_ask": "0.55",
                "volume_24h": 1000.0,
                "ts": datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC),
            },
        }
    )

    assert market.market_id == "kalshi:ABC-123"
    assert market.short_label == "X"
    assert market.source == "kalshi"
    assert market.source_url == "https://kalshi.com/markets/ABC"
    assert market.topic == "politics"
    assert market.family == "ABC"
