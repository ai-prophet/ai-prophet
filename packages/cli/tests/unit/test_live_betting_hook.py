from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from ai_prophet.live_betting import hook as hook_module
from ai_prophet.live_betting.hook import LiveBettingHook


def test_hook_dedupes_duplicate_model_forecasts(monkeypatch):
    monkeypatch.setattr(hook_module, "LIVE_BETTING_ENABLED", True)

    engine = create_engine("sqlite:///:memory:")
    hook = LiveBettingHook(
        betting_model_names=["model-a", "model-b"],
        db_engine=engine,
        dry_run=True,
    )

    saved_decisions: list[dict] = []
    placed_orders: list[tuple[str, str, int, float]] = []

    monkeypatch.setattr(
        hook,
        "_save_bet_decision",
        lambda **kwargs: saved_decisions.append(kwargs),
    )
    monkeypatch.setattr(
        hook,
        "_place_kalshi_order",
        lambda ticker, side, count, price: placed_orders.append((ticker, side, count, price))
        or {
            "order_id": "order-1",
            "status": "DRY_RUN",
            "filled_shares": 0.0,
            "fill_price": 0.0,
            "exchange_order_id": "dry-run-order-1",
        },
    )
    monkeypatch.setattr(hook, "_save_kalshi_order", lambda **_kwargs: None)

    tick_ts = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    kwargs = {
        "tick_ts": tick_ts,
        "market_id": "kalshi:TEST-MARKET",
        "p_yes": 0.72,
        "yes_ask": 0.55,
        "no_ask": 0.45,
        "question": "Test market?",
    }

    # First model reports, then sends a duplicate update.
    assert hook.on_forecast(model_name="model-a", **kwargs) is None
    assert hook.on_forecast(model_name="model-a", **kwargs) is None

    # Second distinct model completes the set and triggers one aggregate.
    result = hook.on_forecast(model_name="model-b", **kwargs)
    assert result is not None

    # Late duplicate after completion should be ignored entirely.
    assert hook.on_forecast(model_name="model-a", **kwargs) is None

    assert len(saved_decisions) == 2
    assert len(placed_orders) == 1

