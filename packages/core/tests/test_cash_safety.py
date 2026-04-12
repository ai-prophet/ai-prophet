"""Cash safety tests -- verify correct behaviour at zero / near-zero balances.

Critical before going live with real money:
1. Zero cash blocks BUY orders
2. Partial cash sizes BUY down to what's affordable
3. SELL works even with zero cash (returns cash, costs nothing)
4. NET flip: sell proceeds are available for the subsequent BUY
5. Strategy and engine agree -- neither double-spends
6. Live mode uses adapter.get_balance(), not starting_cash
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from ai_prophet_core.betting.adapters.base import OrderStatus
from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder, BettingPrediction, BettingSignal
from ai_prophet_core.betting.engine import BettingEngine
from ai_prophet_core.betting.strategy import RebalancingStrategy
from sqlalchemy import create_engine

TICK = datetime(2026, 3, 22, 12, 0, tzinfo=UTC)
MARKET = "kalshi:KXTEST-CASH"

_id = itertools.count(1)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_order(db, ticker="KXTEST-CASH", side="yes", action="BUY",
                count=10, price_cents=50, instance="Haifeng"):
    """Insert a completed order so _live_ledger_state sees it."""
    n = next(_id)
    now = TICK
    with get_session(db) as session:
        pred = BettingPrediction(
            instance_name=instance, tick_ts=now,
            market_id=f"kalshi:{ticker}", source=f"seed-{n}",
            p_yes=0.7, yes_ask=price_cents / 100,
            no_ask=1 - price_cents / 100, created_at=now,
        )
        session.add(pred)
        session.flush()
        sig = BettingSignal(
            instance_name=instance, prediction_id=pred.id,
            strategy_name="test", side=side,
            shares=count / 100, price=price_cents / 100,
            cost=count * price_cents / 10000, created_at=now,
        )
        session.add(sig)
        session.flush()
        session.add(BettingOrder(
            instance_name=instance, signal_id=sig.id,
            order_id=f"seed-ord-{n}", ticker=ticker,
            side=side, action=action, count=count,
            price_cents=price_cents, status="DRY_RUN",
            filled_shares=float(count),
            fill_price=price_cents / 100,
            dry_run=True, created_at=now,
        ))


def _make_engine(db, starting_cash=10000.0, strategy=None, paper=True):
    engine = BettingEngine(
        strategy=strategy or RebalancingStrategy(),
        db_engine=db, paper=paper, enabled=True,
        instance_name="Haifeng", starting_cash=starting_cash,
    )
    mock_adapter = Mock()
    def _echo(req):
        return Mock(
            status=OrderStatus.DRY_RUN,
            filled_shares=req.shares,
            fill_price=req.limit_price,
            exchange_order_id="dry-run",
            rejection_reason=None,
        )
    mock_adapter.submit_order.side_effect = _echo
    engine._adapter = mock_adapter
    return engine


# ── 1. Zero cash blocks BUY ───────────────────────────────────────────────────

def test_zero_cash_blocks_buy(db):
    """With $0 cash and no existing position, BUY should be blocked."""
    # Exhaust all cash on a different market
    _seed_order(db, ticker="KXOTHER", count=100, price_cents=99)

    engine = _make_engine(db, starting_cash=99.0)

    # Verify cash really is $0
    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert float(cash) <= 0.01, f"Expected ~$0 cash but got ${float(cash):.2f}"

    # No position on KXTEST-CASH, $0 cash → pure BUY request should be blocked
    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={MARKET: 0.99},
        market_prices={MARKET: (0.35, 0.65)},
        source="engine-test-1",
    )

    assert len(results) == 1
    result = results[0]
    assert not result.order_placed, (
        f"Should not place BUY with $0 cash, got: {result}"
    )
    # Block may happen at strategy level (signal=None, error=None) or engine
    # level (error message).  Either way no order should have been submitted.
    engine._adapter.submit_order.assert_not_called()


# ── 2. Partial cash sizes down BUY ───────────────────────────────────────────

def test_partial_cash_sizes_down_buy(db):
    """With $5 cash, a large BUY should be reduced to what's affordable."""
    # Spend $95 of $100 on a different market
    _seed_order(db, ticker="KXOTHER", count=95, price_cents=100)

    engine = _make_engine(db, starting_cash=100.0)

    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert 3.0 < float(cash) < 7.0, f"Expected ~$5 but got ${float(cash):.2f}"

    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={MARKET: 0.99},
        market_prices={MARKET: (0.35, 0.65)},
        source="engine-test-2",
    )

    assert len(results) == 1
    if results[0].order_placed:
        call = engine._adapter.submit_order.call_args[0][0]
        order_cost = float(call.shares) * float(call.limit_price)
        assert order_cost <= 6.0, (
            f"Order cost ${order_cost:.2f} exceeds available ~$5 cash"
        )
    else:
        # Acceptable if signal was too small after cash cap
        assert results[0].error is None or "cash" in results[0].error.lower()


# ── 3. SELL works with zero cash ─────────────────────────────────────────────

def test_sell_works_with_zero_cash(db):
    """Reducing an over-exposed position must work even with $0 cash."""
    # Hold 50 YES @ 50c = $25, and exhaust remaining cash
    _seed_order(db, ticker="KXTEST-CASH", side="yes", count=50, price_cents=50)
    _seed_order(db, ticker="KXOTHER", count=75, price_cents=100)  # exhaust rest

    engine = _make_engine(db, starting_cash=100.0)

    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert float(cash) <= 0.01, f"Expected ~$0 but got ${float(cash):.2f}"

    # p_yes=0.30, yes_ask=0.50 → target = -0.20, holding 0.50 YES → SELL YES
    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={MARKET: 0.30},
        market_prices={MARKET: (0.50, 0.50)},
        source="engine-test-3",
    )

    assert len(results) == 1
    assert results[0].order_placed, (
        f"SELL should succeed with $0 cash, got: {results[0]}"
    )
    # The engine may SELL-only or SELL then BUY (NET flip); either way there
    # must be at least one SELL submitted.
    calls = engine._adapter.submit_order.call_args_list
    actions = [c[0][0].action for c in calls]
    assert "SELL" in actions, f"Expected at least one SELL order, got: {actions}"


# ── 4. NET flip: sell proceeds fund the subsequent BUY ───────────────────────

def test_net_flip_sell_proceeds_fund_buy(db):
    """After selling the opposite side, the proceeds should be used for the new BUY."""
    # Hold 20 YES @ 50c = $10 deployed. Zero remaining cash.
    _seed_order(db, ticker="KXTEST-CASH", side="yes", count=20, price_cents=50)

    engine = _make_engine(db, starting_cash=10.0)  # exactly fully deployed

    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert float(cash) <= 0.01, f"Expected $0 cash but got ${float(cash):.2f}"

    # p_yes=0.01, yes_ask=0.50 → wants NO heavily
    # Engine should: SELL 20 YES (get $10 back) → BUY NO with those proceeds
    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={MARKET: 0.01},
        market_prices={MARKET: (0.50, 0.50)},
        source="engine-test-4",
    )

    assert len(results) == 1
    assert results[0].order_placed, (
        f"NET flip should work: sell YES proceeds should fund NO buy. Got: {results[0]}"
    )
    calls = engine._adapter.submit_order.call_args_list
    actions = [c[0][0].action for c in calls]
    assert "SELL" in actions, "Expected a SELL as part of NET flip"
    assert "BUY" in actions, (
        "Expected a BUY after NET sell (sell proceeds should fund it)"
    )

    # Total BUY cost should not exceed sell proceeds
    total_buys = sum(
        float(c[0][0].shares) * float(c[0][0].limit_price)
        for c in calls if c[0][0].action == "BUY"
    )
    total_sells = sum(
        float(c[0][0].shares) * float(c[0][0].limit_price)
        for c in calls if c[0][0].action == "SELL"
    )
    assert total_buys <= total_sells + 0.10, (
        f"BUY cost ${total_buys:.2f} exceeds sell proceeds ${total_sells:.2f}"
    )


# ── 5. Live mode uses adapter.get_balance() ──────────────────────────────────

def test_live_mode_uses_adapter_balance(db):
    """In LIVE mode, cash comes from get_balance(), not starting_cash."""
    engine = BettingEngine(
        strategy=RebalancingStrategy(),
        db_engine=db, paper=False, enabled=True,
        instance_name="Haifeng", starting_cash=99999.0,  # should be ignored
    )
    mock_adapter = Mock()
    mock_adapter.get_balance.return_value = Decimal("25.00")
    engine._adapter = mock_adapter

    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert float(cash) == 25.0, (
        f"Live mode should use adapter balance $25, got ${float(cash):.2f}"
    )


# ── 6. Exact minimum: enough for 1 contract ──────────────────────────────────

def test_exact_minimum_cash_buys_one_contract(db):
    """With exactly $0.35, engine should buy at most 1 contract at 35c."""
    # Spend $99.65 of $100 on other market
    _seed_order(db, ticker="KXOTHER", count=100, price_cents=100)

    engine = _make_engine(db, starting_cash=100.35)

    _, _, cash = engine._live_ledger_state("KXTEST-CASH")
    assert 0.30 <= float(cash) <= 0.40, f"Expected ~$0.35 but got ${float(cash):.2f}"

    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={MARKET: 0.99},
        market_prices={MARKET: (0.35, 0.65)},
        source="engine-test-6",
    )

    assert len(results) == 1
    if results[0].order_placed:
        call = engine._adapter.submit_order.call_args[0][0]
        assert int(call.shares) <= 1, (
            f"Expected at most 1 contract but got {int(call.shares)}"
        )


# ── 7. Multi-market: cash decreases correctly across markets ─────────────────

def test_multi_market_cash_does_not_double_spend(db):
    """Processing two markets should not spend more than available cash."""
    engine = _make_engine(db, starting_cash=10.0)

    results = engine.process_forecasts(
        tick_ts=TICK,
        forecasts={
            "kalshi:MKTA": 0.90,
            "kalshi:MKTB": 0.85,
        },
        market_prices={
            "kalshi:MKTA": (0.35, 0.65),
            "kalshi:MKTB": (0.35, 0.65),
        },
        source="engine-test-7",
    )

    assert len(results) == 2
    calls = engine._adapter.submit_order.call_args_list
    total_cost = sum(
        float(c[0][0].shares) * float(c[0][0].limit_price)
        for c in calls if c[0][0].action == "BUY"
    )
    assert total_cost <= 10.50, (
        f"Total BUY cost ${total_cost:.2f} exceeds $10 starting cash"
    )
