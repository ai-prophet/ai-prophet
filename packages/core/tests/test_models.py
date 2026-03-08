"""Tests for Pydantic models."""

import pytest
from datetime import datetime, timezone
from ai_prophet_core.models import (
    Market,
    Quote,
    TradeIntent,
    TradeAction,
    TradeSide,
    SizeType,
)


def test_market_model():
    """Test Market model."""
    market = Market(
        market_id="market_123",
        question="Will X happen?",
        description="Details...",
        resolution_time=datetime(2024, 2, 1, tzinfo=timezone.utc),
        created_at=datetime(2024, 1, 15, tzinfo=timezone.utc),
        source="polymarket",
        source_market_id="pm_456",
        metadata={"category": "sports"}
    )
    assert market.market_id == "market_123"
    assert market.metadata["category"] == "sports"


def test_quote_model():
    """Test Quote model."""
    quote = Quote(
        quote_id="quote_1",
        market_id="market_123",
        ts=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
        ingested_at=datetime(2024, 1, 15, 14, 1, 0, tzinfo=timezone.utc),
        best_bid=0.45,
        best_ask=0.47,
        bid_size=100.0,
        ask_size=150.0,
        volume_24h=5000.0
    )
    assert quote.best_bid < quote.best_ask
    
    # Test validation (ask must be >= bid)
    with pytest.raises(ValueError):
        Quote(
            quote_id="quote_2",
            market_id="market_123",
            ts=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
            ingested_at=datetime(2024, 1, 15, 14, 1, 0, tzinfo=timezone.utc),
            best_bid=0.50,
            best_ask=0.45,  # Invalid: less than bid
            bid_size=100.0,
            ask_size=150.0,
            volume_24h=5000.0
        )


def test_trade_intent_model():
    """Test TradeIntent model."""
    intent = TradeIntent(
        intent_id="intent_1",
        experiment_id="exp_1",
        participant_idx=0,
        tick_ts=datetime(2024, 1, 15, 14, 0, 0, tzinfo=timezone.utc),
        market_id="market_123",
        action=TradeAction.BUY,
        side=TradeSide.YES,
        size_type=SizeType.NOTIONAL,
        size=100.0,
        submitted_at=datetime(2024, 1, 15, 14, 5, 0, tzinfo=timezone.utc)
    )
    assert intent.action == TradeAction.BUY
    assert intent.size > 0
    
    # Test validation (tick must be on valid tick boundary)
    with pytest.raises(ValueError):
        TradeIntent(
            intent_id="intent_2",
            experiment_id="exp_1",
            participant_idx=0,
            tick_ts=datetime(2024, 1, 15, 14, 7, 0, tzinfo=timezone.utc),
            market_id="market_123",
            action=TradeAction.BUY,
            side=TradeSide.YES,
            size_type=SizeType.NOTIONAL,
            size=100.0,
            submitted_at=datetime(2024, 1, 15, 14, 5, 0, tzinfo=timezone.utc)
        )

