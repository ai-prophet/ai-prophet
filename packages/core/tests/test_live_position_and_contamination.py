"""Tests for live position refresh and cross-instance contamination prevention.

Validates:
1. _live_ledger_state returns per-instance positions (no contamination)
2. RebalancingStrategy receives fresh live position, not stale snapshot
3. Delta computation is correct (no over-buying)
4. DRY_RUN cash uses starting_cash baseline
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine

# Ensure position_replay is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "services"))

from ai_prophet_core.betting.adapters.base import OrderStatus
from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder, BettingPrediction, BettingSignal
from ai_prophet_core.betting.engine import BettingEngine
from ai_prophet_core.betting.strategy import (
    DefaultBettingStrategy,
    PortfolioSnapshot,
    RebalancingStrategy,
)

# ── Fixtures ────────────────────────────────────────────────────────

_signal_counter = 0


@pytest.fixture
def db_engine():
    """In-memory SQLite database with tables created."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _seed_orders(db_engine, orders: list[dict]):
    """Insert BettingOrder rows (with required prediction+signal parents)."""
    global _signal_counter
    with get_session(db_engine) as session:
        for o in orders:
            _signal_counter += 1
            now = o.get("created_at", datetime.now(UTC))
            # Create parent prediction
            pred = BettingPrediction(
                instance_name=o["instance_name"],
                tick_ts=now,
                market_id=f"kalshi:{o['ticker']}",
                source="test",
                p_yes=0.5,
                yes_ask=o["price_cents"] / 100.0,
                no_ask=1.0 - o["price_cents"] / 100.0,
                created_at=now,
            )
            session.add(pred)
            session.flush()
            # Create parent signal
            sig = BettingSignal(
                instance_name=o["instance_name"],
                prediction_id=pred.id,
                strategy_name="test",
                side=o["side"],
                shares=float(o["count"]) / 100.0,
                price=o["price_cents"] / 100.0,
                cost=float(o["count"]) * o["price_cents"] / 10000.0,
                created_at=now,
            )
            session.add(sig)
            session.flush()
            # Create the order
            session.add(BettingOrder(
                instance_name=o["instance_name"],
                signal_id=sig.id,
                ticker=o["ticker"],
                side=o["side"],
                action=o.get("action", "BUY"),
                count=o["count"],
                price_cents=o["price_cents"],
                status=o.get("status", "DRY_RUN"),
                filled_shares=float(o["count"]),
                fill_price=o["price_cents"] / 100.0,
                order_id=o.get("order_id", f"ord-{_signal_counter}"),
                dry_run=True,
                created_at=now,
            ))


def _make_engine(db_engine, instance_name="Haifeng", starting_cash=10000.0, strategy=None):
    """Create a BettingEngine with a mocked adapter (DRY_RUN).

    The mock echoes back the requested shares/price so position_replay
    sees the correct fill quantities (matches real DRY_RUN behavior).
    """
    engine = BettingEngine(
        strategy=strategy or RebalancingStrategy(),
        db_engine=db_engine,
        paper=True,
        enabled=True,
        instance_name=instance_name,
        starting_cash=starting_cash,
    )
    mock_adapter = Mock()

    def _echo_submit(order_req):
        return Mock(
            status=OrderStatus.DRY_RUN,
            filled_shares=order_req.shares,
            fill_price=order_req.limit_price,
            exchange_order_id="dry-run-test",
            rejection_reason=None,
        )

    mock_adapter.submit_order.side_effect = _echo_submit
    engine._adapter = mock_adapter
    return engine


# ── Test 1: No cross-instance contamination ────────────────────────


def test_live_ledger_state_filters_by_instance(db_engine):
    """Haifeng's _live_ledger_state must NOT see Jibang's orders."""
    _seed_orders(db_engine, [
        # Haifeng: BUY 64 YES @ 23c
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 64, "price_cents": 23, "order_id": "h-1"},
        # Jibang: BUY 6 NO @ 67c (SAME market, different instance)
        {"instance_name": "Jibang", "ticker": "KXTEST-26APR01", "side": "no",
         "count": 6, "price_cents": 67, "order_id": "j-1"},
    ])

    haifeng_engine = _make_engine(db_engine, instance_name="Haifeng")
    jibang_engine = _make_engine(db_engine, instance_name="Jibang")

    h_side, h_qty, h_cash = haifeng_engine._live_ledger_state("KXTEST-26APR01")
    j_side, j_qty, j_cash = jibang_engine._live_ledger_state("KXTEST-26APR01")

    # Haifeng should see 64 YES, not 64 YES - 6 NO = 58
    assert h_side == "yes"
    assert h_qty == 64
    # Jibang should see 6 NO, not 64 YES - 6 NO = 58 YES
    assert j_side == "no"
    assert j_qty == 6


def test_no_contamination_121_minus_5_scenario(db_engine):
    """Reproduces the 116 = 121 - 5 contamination bug from screenshots."""
    _seed_orders(db_engine, [
        # Haifeng accumulates 121 YES across 4 trades
        {"instance_name": "Haifeng", "ticker": "KXMARKET-26APR01", "side": "yes",
         "count": 64, "price_cents": 23, "order_id": "h-1"},
        {"instance_name": "Haifeng", "ticker": "KXMARKET-26APR01", "side": "yes",
         "count": 11, "price_cents": 36, "order_id": "h-3",
         "action": "BUY"},
        {"instance_name": "Haifeng", "ticker": "KXMARKET-26APR01", "side": "yes",
         "count": 58, "price_cents": 35, "order_id": "h-4"},
        # Haifeng also sold 12 YES (net = 64 - 12 + 11 + 58 = 121)
        {"instance_name": "Haifeng", "ticker": "KXMARKET-26APR01", "side": "yes",
         "count": 12, "price_cents": 43, "order_id": "h-2",
         "action": "SELL"},
        # Jibang has 5 NO (7 bought, 2 sold)
        {"instance_name": "Jibang", "ticker": "KXMARKET-26APR01", "side": "no",
         "count": 6, "price_cents": 67, "order_id": "j-1"},
        {"instance_name": "Jibang", "ticker": "KXMARKET-26APR01", "side": "no",
         "count": 1, "price_cents": 67, "order_id": "j-2"},
        {"instance_name": "Jibang", "ticker": "KXMARKET-26APR01", "side": "no",
         "count": 2, "price_cents": 78, "order_id": "j-3",
         "action": "SELL"},
    ])

    haifeng_engine = _make_engine(db_engine, instance_name="Haifeng")
    jibang_engine = _make_engine(db_engine, instance_name="Jibang")

    h_side, h_qty, _ = haifeng_engine._live_ledger_state("KXMARKET-26APR01")
    j_side, j_qty, _ = jibang_engine._live_ledger_state("KXMARKET-26APR01")

    # Haifeng: 64 - 12 + 11 + 58 = 121 YES
    assert h_side == "yes"
    assert h_qty == 121

    # Jibang: 6 + 1 - 2 = 5 NO (NOT 116 YES from cross-instance netting!)
    assert j_side == "no"
    assert j_qty == 5


# ── Test 2: Strategy gets fresh live position ──────────────────────


def test_rebalancing_uses_live_position_not_stale_portfolio(db_engine):
    """The engine refreshes the strategy's portfolio from live DB state,
    not from the stale snapshot passed by the caller."""
    # Seed: Haifeng already holds 63 YES
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 63, "price_cents": 30, "order_id": "h-1"},
    ])

    captured_portfolios = []

    class SpyRebalancing(RebalancingStrategy):
        """Captures the portfolio seen during evaluation."""
        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            captured_portfolios.append(self.portfolio)
            return super().evaluate(market_id, p_yes, yes_ask, no_ask)

    engine = _make_engine(db_engine, strategy=SpyRebalancing())

    # Pass a STALE portfolio claiming 6 shares (simulating the bug)
    stale_portfolio = PortfolioSnapshot(
        cash=Decimal("9000"),
        market_position_shares=Decimal("6"),
        market_position_side="yes",
    )

    engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXTEST-26APR01": 0.99},
        market_prices={"kalshi:KXTEST-26APR01": (0.35, 0.65)},
        source="test-model",
        portfolio=stale_portfolio,  # intentionally stale
    )

    # The engine should have refreshed the portfolio from live DB
    assert len(captured_portfolios) == 1
    live_portfolio = captured_portfolios[0]

    # Live DB shows 63, NOT the stale 6
    assert float(live_portfolio.market_position_shares) == 63
    assert live_portfolio.market_position_side == "yes"


# ── Test 3: Correct delta (no over-buying) ─────────────────────────


def test_rebalancing_delta_is_1_not_58(db_engine):
    """Holding 63 YES, target 64 → should signal delta of 1, not 58+."""
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 63, "price_cents": 30, "order_id": "h-1"},
    ])

    captured_signals = []

    class SpyRebalancing(RebalancingStrategy):
        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            signal = super().evaluate(market_id, p_yes, yes_ask, no_ask)
            captured_signals.append(signal)
            return signal

    engine = _make_engine(db_engine, strategy=SpyRebalancing())

    # p_yes=0.99, yes_ask=0.35 → target = 0.64 fractional = 64 contracts
    # Holding 63 → delta should be ~0.01 = 1 contract
    engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXTEST-26APR01": 0.99},
        market_prices={"kalshi:KXTEST-26APR01": (0.35, 0.65)},
        source="test-model",
    )

    assert len(captured_signals) == 1
    signal = captured_signals[0]
    if signal is not None:
        # The delta should be ~0.01 (1 contract), NOT 0.58 or 0.64
        assert signal.shares < 0.05, (
            f"Expected small delta ~0.01 but got {signal.shares} "
            f"(would buy {round(signal.shares * 100)} contracts instead of 1)"
        )
    # If signal is None, that's also acceptable (delta < min_trade)


def test_rebalancing_sell_when_overexposed(db_engine):
    """Holding 64 YES, target 52 → should signal SELL (buy NO via engine)."""
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 64, "price_cents": 23, "order_id": "h-1"},
    ])

    captured_signals = []

    class SpyRebalancing(RebalancingStrategy):
        def evaluate(self, market_id, p_yes, yes_ask, no_ask):
            signal = super().evaluate(market_id, p_yes, yes_ask, no_ask)
            captured_signals.append(signal)
            return signal

    engine = _make_engine(db_engine, strategy=SpyRebalancing())

    # p_yes=0.95, yes_ask=0.43 → target = 0.52 = 52 contracts
    # Holding 64 → delta = -0.12 → should sell (buy NO)
    engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXTEST-26APR01": 0.95},
        market_prices={"kalshi:KXTEST-26APR01": (0.43, 0.57)},
        source="test-model",
    )

    assert len(captured_signals) == 1
    signal = captured_signals[0]
    assert signal is not None
    assert signal.side == "no", "Should want to reduce YES exposure by buying NO"
    # Delta should be ~0.12 (12 contracts), not a huge amount
    assert signal.shares < 0.2, f"Expected ~0.12 but got {signal.shares}"


# ── Test 4: DefaultBettingStrategy delta awareness ─────────────────


def test_default_strategy_delta_when_holding_same_side(db_engine):
    """DefaultBettingStrategy with 63 YES held, edge +64pp → buy only 1."""
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 63, "price_cents": 30, "order_id": "h-1"},
    ])

    engine = _make_engine(db_engine, strategy=DefaultBettingStrategy())

    # p_yes=0.99, yes_ask=0.35 → edge = 0.64, desired = 64 contracts
    # Holding 63 → delta = 1 → shares ≈ 0.01
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXTEST-26APR01": 0.99},
        market_prices={"kalshi:KXTEST-26APR01": (0.35, 0.65)},
        source="test-model",
    )

    assert len(results) == 1
    result = results[0]
    if result.signal is not None and result.order_placed:
        # Should place at most a few contracts, not 64
        submitted = engine._adapter.submit_order.call_args[0][0]
        assert int(submitted.shares) <= 5, (
            f"Expected ≤5 shares but engine tried to buy {submitted.shares}"
        )


def test_default_strategy_skips_when_at_target(db_engine):
    """DefaultBettingStrategy with 64 YES held, edge +64pp → no trade needed."""
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 64, "price_cents": 30, "order_id": "h-1"},
    ])

    engine = _make_engine(db_engine, strategy=DefaultBettingStrategy())

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXTEST-26APR01": 0.99},
        market_prices={"kalshi:KXTEST-26APR01": (0.35, 0.65)},
        source="test-model",
    )

    assert len(results) == 1
    # Should skip — already at target
    assert results[0].signal is None or results[0].order_placed is False


# ── Test 5: DRY_RUN cash uses starting_cash ────────────────────────


def test_paper_cash_uses_starting_cash(db_engine):
    """DRY_RUN mode should compute cash from starting_cash, not Kalshi API."""
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXTEST-26APR01", "side": "yes",
         "count": 10, "price_cents": 50, "order_id": "h-1"},
    ])

    engine = _make_engine(db_engine, starting_cash=500.0)

    _, _, cash = engine._live_ledger_state("KXTEST-26APR01")

    # starting_cash=500, capital_deployed = 10 * 0.50 = $5.00
    # cash = 500 - 5 = $495 (approximately)
    assert float(cash) > 400, f"Expected ~$495 but got ${float(cash)}"
    assert float(cash) < 500, "Cash should be < starting_cash due to deployed capital"
    # Critically: should NOT be 0 or negative (which would happen if get_balance() was used
    # and returned $0 for a DRY_RUN account)


def test_paper_cash_blocks_buy_when_exhausted(db_engine):
    """When DRY_RUN cash is exhausted, engine should reject BUY orders."""
    # Spend almost all of the $100 starting cash
    _seed_orders(db_engine, [
        {"instance_name": "Haifeng", "ticker": "KXOTHER", "side": "yes",
         "count": 100, "price_cents": 99, "order_id": "h-1"},
    ])

    engine = _make_engine(db_engine, starting_cash=100.0)

    # Cash = 100 - (100 * 0.99) = $1.00. Trying to buy at 0.35 per share
    # should allow max 2 shares (2 * 0.35 = $0.70 < $1.00)
    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={"kalshi:KXNEW-26APR01": 0.99},
        market_prices={"kalshi:KXNEW-26APR01": (0.35, 0.65)},
        source="test-model",
    )

    assert len(results) == 1
    if results[0].order_placed:
        # If order was placed, it should be capped to affordable quantity
        submitted = engine._adapter.submit_order.call_args[0][0]
        assert int(submitted.shares) <= 3, (
            f"Expected ≤3 shares (cash limited) but got {submitted.shares}"
        )


# ── Test 6: Multi-market same cycle doesn't double-spend ──────────


def test_multi_market_cash_decreases_between_orders(db_engine):
    """When processing multiple markets, cash should decrease as orders are placed."""
    engine = _make_engine(db_engine, starting_cash=20.0)

    # Two markets, both want to buy — but only $20 total budget
    # Market A: p_yes=0.90, yes_ask=0.35 → edge 0.55 → 55 contracts @ $0.35 = $19.25
    # Market B: p_yes=0.80, yes_ask=0.35 → edge 0.45 → 45 contracts @ $0.35 = $15.75
    # Total would be $35, but only $20 available

    results = engine.process_forecasts(
        tick_ts=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        forecasts={
            "kalshi:MKT-A": 0.90,
            "kalshi:MKT-B": 0.80,
        },
        market_prices={
            "kalshi:MKT-A": (0.35, 0.65),
            "kalshi:MKT-B": (0.35, 0.65),
        },
        source="test-model",
    )

    # At least one should be placed, and the total spent should not exceed $20
    placed = [r for r in results if r.order_placed]
    assert len(placed) >= 1

    # If both placed, second order should be cash-capped
    if len(placed) == 2:
        calls = engine._adapter.submit_order.call_args_list
        total_cost = sum(
            float(call[0][0].shares) * float(call[0][0].limit_price)
            for call in calls
        )
        assert total_cost <= 21.0, (
            f"Total cost ${total_cost:.2f} exceeds starting cash $20"
        )
