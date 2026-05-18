"""Focused tests for prompt-fragment helpers in ``agent/utils.py``."""

from datetime import UTC, datetime
from decimal import Decimal

from ai_prophet.trade.agent.utils import render_portfolio
from ai_prophet.trade.core.tick_context import CandidateMarket, Position, TickContext

_TICK_TS = datetime(2026, 2, 20, 6, 0, tzinfo=UTC)
_ASOF = datetime(2026, 2, 20, 5, 30, tzinfo=UTC)


def _market(market_id: str = "m1") -> CandidateMarket:
    return CandidateMarket(
        market_id=market_id,
        question="Will X happen?",
        description="desc",
        resolution_time=datetime(2026, 6, 30, tzinfo=UTC),
        yes_bid=0.45, yes_ask=0.55, yes_mark=0.50,
        no_bid=0.45, no_ask=0.55, no_mark=0.50,
        volume_24h=1000.0,
        quote_ts=_ASOF,
    )


def _ctx(*positions: Position) -> TickContext:
    return TickContext(
        run_id="test:0",
        tick_ts=_TICK_TS,
        data_asof_ts=_ASOF,
        candidate_set_id="snap_test",
        submission_deadline=_TICK_TS.replace(minute=55),
        server_now=_ASOF,
        candidates=(_market(),),
        cash=Decimal("10000"),
        equity=Decimal("10000"),
        total_pnl=Decimal("0"),
        positions=positions,
        total_fills=0,
    )


def _position(**overrides) -> Position:
    base = {
        "market_id": "m1",
        "side": "YES",
        "shares": Decimal("100"),
        "avg_entry_price": Decimal("0.40"),
        "current_price": Decimal("0.50"),
        "unrealized_pnl": Decimal("10"),
        "realized_pnl": Decimal("0"),
        "updated_at": _TICK_TS,
        "question": "Will X happen?",
    }
    base.update(overrides)
    return Position(**base)


def test_focused_position_renders_thesis_when_rationales_present():
    ctx = _ctx(_position(
        entry_forecast_rationale="Base rate ~15% but market priced 40%; estimated true ~18%.",
        entry_trade_rationale="Bought NO for ~22pp edge; sized at 3% of equity.",
    ))

    text = render_portfolio(ctx, focus_market_id="m1")

    assert "ORIGINAL THESIS (from the trade that opened this position):" in text
    assert "Forecaster: Base rate ~15%" in text
    assert "Trader: Bought NO for ~22pp edge" in text


def test_focused_position_renders_fallback_when_rationales_missing():
    ctx = _ctx(_position())  # both rationales default to ""

    text = render_portfolio(ctx, focus_market_id="m1")

    assert "ORIGINAL THESIS: not captured for this position" in text
    assert "Forecaster:" not in text
    assert "Trader:" not in text


def test_focused_position_omits_block_when_no_position_held():
    ctx = _ctx()  # no positions

    text = render_portfolio(ctx, focus_market_id="m1")

    assert "ORIGINAL THESIS" not in text
    assert "YOU HOLD THIS MARKET" not in text
