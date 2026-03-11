"""Unit tests for BellwetherStage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest

from ai_prophet_core.bellwether_client import BellwetherAPIError
from ai_prophet_core.bellwether_models import (
    BellwetherEventMetrics,
    BellwetherPlatformPrices,
    BellwetherRobustness,
    BellwetherSearchResponse,
    BellwetherSearchResult,
    BellwetherVWAPDetails,
)

from ai_prophet.trade.agent.stages import StageResult
from ai_prophet.trade.agent.stages.bellwether import BellwetherStage
from ai_prophet.trade.core.tick_context import CandidateMarket


@pytest.fixture
def mock_bellwether_client():
    return MagicMock()


@pytest.fixture
def tick_ctx():
    ctx = MagicMock()
    ctx.tick_ts = datetime(2026, 1, 20, 6, 0, 0, tzinfo=UTC)
    ctx.candidates = [
        CandidateMarket(
            market_id="market_1",
            question="Will Republicans win the Senate in 2026?",
            description="Senate control",
            resolution_time=datetime(2026, 11, 4, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.45,
            yes_ask=0.55,
            yes_mark=0.50,
            no_bid=0.45,
            no_ask=0.55,
            no_mark=0.50,
            volume_24h=1000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC),
        ),
        CandidateMarket(
            market_id="market_2",
            question="Will Bitcoin reach $100k by end of 2026?",
            description="BTC price target",
            resolution_time=datetime(2026, 12, 31, 0, 0, 0, tzinfo=UTC),
            yes_bid=0.30,
            yes_ask=0.35,
            yes_mark=0.325,
            no_bid=0.65,
            no_ask=0.70,
            no_mark=0.675,
            volume_24h=5000.0,
            quote_ts=datetime(2026, 1, 20, 5, 30, 0, tzinfo=UTC),
        ),
    ]
    return ctx


def _make_review_result(market_ids: list[str]) -> dict[str, StageResult]:
    review_items = [
        {"market_id": mid, "priority": 80, "queries": ["q"], "rationale": "r"}
        for mid in market_ids
    ]
    return {
        "review": StageResult(
            stage_name="review",
            success=True,
            data={"review": review_items},
        )
    }


def test_bellwether_stage_name(mock_bellwether_client):
    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    assert stage.name == "bellwether"


def test_bellwether_stage_no_review_results(mock_bellwether_client, tick_ctx):
    """Stage returns empty enrichments when review results are missing."""
    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    result = stage.execute(tick_ctx, {})
    assert result.success is True
    assert result.data["enrichments"] == {}


def test_bellwether_stage_no_reviewed_markets(mock_bellwether_client, tick_ctx):
    """Stage returns empty enrichments when review has no items."""
    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = {
        "review": StageResult(
            stage_name="review", success=True, data={"review": []}
        )
    }
    result = stage.execute(tick_ctx, prev)
    assert result.success is True
    assert result.data["enrichments"] == {}


def test_bellwether_stage_successful_enrichment(mock_bellwether_client, tick_ctx):
    """Stage enriches markets when search and metrics succeed."""
    # Configure mock search response
    mock_bellwether_client.search_markets.return_value = BellwetherSearchResponse(
        results=[
            BellwetherSearchResult(
                ticker="SENATE_2026",
                title="Will Republicans win the Senate in 2026?",
                category="politics",
                volume_usd=500000.0,
                is_matched=True,
                platforms=["polymarket", "kalshi"],
            )
        ],
        total=1,
        query="Will Republicans win the Senate in 2026?",
    )

    # Configure mock metrics response
    mock_bellwether_client.get_event_metrics.return_value = BellwetherEventMetrics(
        ticker="SENATE_2026",
        title="Will Republicans win the Senate in 2026?",
        bellwether_price=0.47,
        price_tier="mid",
        price_label="47%",
        platform_prices=BellwetherPlatformPrices(polymarket=0.50, kalshi=0.42),
        robustness=BellwetherRobustness(cost_to_move_5c=180000.0, reportability="reportable"),
        vwap_details=BellwetherVWAPDetails(trade_count=1200, total_volume=50000.0),
    )

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert "market_1" in result.data["enrichments"]
    enrichment = result.data["enrichments"]["market_1"]
    assert enrichment["bellwether_price"] == 0.47
    assert enrichment["polymarket_price"] == 0.50
    assert enrichment["kalshi_price"] == 0.42
    assert enrichment["cost_to_move_5c"] == 180000.0
    assert enrichment["reportability"] == "reportable"


def test_bellwether_stage_no_search_results(mock_bellwether_client, tick_ctx):
    """Stage gracefully handles no search results."""
    mock_bellwether_client.search_markets.return_value = BellwetherSearchResponse(
        results=[], total=0, query="test"
    )

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert result.data["enrichments"] == {}


def test_bellwether_stage_low_similarity_match(mock_bellwether_client, tick_ctx):
    """Stage skips markets where title similarity is below threshold."""
    mock_bellwether_client.search_markets.return_value = BellwetherSearchResponse(
        results=[
            BellwetherSearchResult(
                ticker="UNRELATED",
                title="Completely unrelated market title about weather",
                category="other",
            )
        ],
        total=1,
        query="test",
    )

    stage = BellwetherStage(
        bellwether_client=mock_bellwether_client,
        min_title_similarity=0.4,
    )
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert result.data["enrichments"] == {}
    mock_bellwether_client.get_event_metrics.assert_not_called()


def test_bellwether_stage_search_api_error(mock_bellwether_client, tick_ctx):
    """Stage handles search API errors gracefully."""
    mock_bellwether_client.search_markets.side_effect = BellwetherAPIError("timeout")

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert result.data["enrichments"] == {}


def test_bellwether_stage_metrics_api_error(mock_bellwether_client, tick_ctx):
    """Stage handles metrics API errors gracefully."""
    mock_bellwether_client.search_markets.return_value = BellwetherSearchResponse(
        results=[
            BellwetherSearchResult(
                ticker="SENATE_2026",
                title="Will Republicans win the Senate in 2026?",
            )
        ],
        total=1,
        query="test",
    )
    mock_bellwether_client.get_event_metrics.side_effect = BellwetherAPIError("500")

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert result.data["enrichments"] == {}


def test_bellwether_stage_multiple_markets(mock_bellwether_client, tick_ctx):
    """Stage processes multiple markets, succeeding on some and failing on others."""
    call_count = {"n": 0}

    def mock_search(query, limit=5):
        call_count["n"] += 1
        if "Senate" in query:
            return BellwetherSearchResponse(
                results=[
                    BellwetherSearchResult(
                        ticker="SENATE_2026",
                        title="Will Republicans win the Senate in 2026?",
                    )
                ],
                total=1,
                query=query,
            )
        # No match for Bitcoin market
        return BellwetherSearchResponse(results=[], total=0, query=query)

    mock_bellwether_client.search_markets.side_effect = mock_search
    mock_bellwether_client.get_event_metrics.return_value = BellwetherEventMetrics(
        ticker="SENATE_2026",
        title="Will Republicans win the Senate in 2026?",
        bellwether_price=0.47,
        platform_prices=BellwetherPlatformPrices(polymarket=0.50, kalshi=0.42),
        robustness=BellwetherRobustness(cost_to_move_5c=180000.0, reportability="reportable"),
        vwap_details=BellwetherVWAPDetails(trade_count=1200, total_volume=50000.0),
    )

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1", "market_2"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    # Only market_1 should be enriched
    assert "market_1" in result.data["enrichments"]
    assert "market_2" not in result.data["enrichments"]


def test_bellwether_stage_always_succeeds(mock_bellwether_client, tick_ctx):
    """Stage always returns success=True even on unexpected errors."""
    mock_bellwether_client.search_markets.side_effect = RuntimeError("unexpected")

    stage = BellwetherStage(bellwether_client=mock_bellwether_client)
    prev = _make_review_result(["market_1"])
    result = stage.execute(tick_ctx, prev)

    assert result.success is True
    assert result.data["enrichments"] == {}
