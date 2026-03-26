from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder
from db_models import KalshiBalanceSnapshot, KalshiOrderSnapshot, KalshiPositionSnapshot, TradingPosition
from kalshi_state import build_pending_orders_by_ticker, build_position_views, record_kalshi_state
from order_management import _sync_pending_order_status


class _FakeAdapter:
    def get_balance_details(self):
        return {
            "balance": 45887,
            "portfolio_value": 49611,
            "updated_ts": 1764200000,
        }

    def get_positions(self):
        return [
            {
                "ticker": "TEST-APR",
                "position_fp": "-10.00",
                "market_exposure_dollars": "5.0000",
                "realized_pnl_dollars": "1.2500",
                "fees_paid_dollars": "0.1700",
                "total_cost_dollars": "5.0000",
                "total_cost_shares_fp": "10.00",
                "resting_orders_count": 1,
            }
        ]

    def get_orders(self, *, status=None, ticker=None):
        if status == "resting":
            return [
                {
                    "order_id": "ord-pending",
                    "client_order_id": "client-pending",
                    "ticker": "TEST-APR",
                    "side": "no",
                    "action": "buy",
                    "status": "resting",
                    "no_price_dollars": "0.5100",
                    "fill_count_fp": "2.00",
                    "remaining_count_fp": "3.00",
                    "initial_count_fp": "5.00",
                    "created_time": "2026-03-26T20:00:00Z",
                    "last_update_time": "2026-03-26T20:05:00Z",
                }
            ]
        if status == "executed":
            return [
                {
                    "order_id": "ord-filled",
                    "client_order_id": "client-filled",
                    "ticker": "TEST-APR",
                    "side": "no",
                    "action": "buy",
                    "status": "executed",
                    "no_price_dollars": "0.5000",
                    "fill_count_fp": "10.00",
                    "remaining_count_fp": "0.00",
                    "initial_count_fp": "10.00",
                    "taker_fill_cost_dollars": "5.0000",
                    "taker_fees_dollars": "0.1700",
                    "created_time": "2026-03-24T01:24:00Z",
                    "last_update_time": "2026-03-24T01:24:30Z",
                }
            ]
        return []

    def get_historical_orders(self, *, ticker=None):
        return []


class _LifecycleAdapter:
    def __init__(self):
        self._cycle = 0

    def get_balance_details(self):
        return {
            "balance": 40000,
            "portfolio_value": 43000,
            "updated_ts": 1764200000,
        }

    def get_positions(self):
        if self._cycle == 0:
            return []
        return [
            {
                "ticker": "FLIP-APR",
                "position_fp": "4.00",
                "market_exposure_dollars": "2.2000",
                "realized_pnl_dollars": "0.0000",
                "fees_paid_dollars": "0.0200",
                "total_cost_dollars": "2.2000",
                "total_cost_shares_fp": "4.00",
                "resting_orders_count": 0,
            }
        ]

    def get_orders(self, *, status=None, ticker=None):
        if self._cycle == 0:
            if status == "resting":
                return [
                    {
                        "order_id": "exchange-1",
                        "client_order_id": "local-1",
                        "ticker": "FLIP-APR",
                        "side": "yes",
                        "action": "buy",
                        "status": "resting",
                        "yes_price_dollars": "0.55",
                        "fill_count_fp": "0.00",
                        "remaining_count_fp": "4.00",
                        "initial_count_fp": "4.00",
                        "created_time": "2026-03-26T20:00:00Z",
                        "last_update_time": "2026-03-26T20:00:00Z",
                    }
                ]
            return []

        if status == "executed":
            return [
                {
                    "order_id": "exchange-1",
                    "client_order_id": "local-1",
                    "ticker": "FLIP-APR",
                    "side": "yes",
                    "action": "buy",
                    "status": "executed",
                    "yes_price_dollars": "0.55",
                    "fill_count_fp": "4.00",
                    "remaining_count_fp": "0.00",
                    "initial_count_fp": "4.00",
                    "taker_fill_cost_dollars": "2.2000",
                    "taker_fees_dollars": "0.0200",
                    "created_time": "2026-03-26T20:00:00Z",
                    "last_update_time": "2026-03-26T20:10:00Z",
                }
            ]
        return []

    def get_historical_orders(self, *, ticker=None):
        return []


def test_record_kalshi_state_persists_balance_positions_and_orders():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        counts = record_kalshi_state(
            session,
            _FakeAdapter(),
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )

        assert counts == {"balances": 1, "positions": 1, "orders": 2}
        assert session.query(KalshiBalanceSnapshot).count() == 1
        assert session.query(KalshiPositionSnapshot).count() == 1
        assert session.query(KalshiOrderSnapshot).count() == 2


def test_build_position_views_and_pending_orders_use_latest_kalshi_snapshots():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        record_kalshi_state(
            session,
            _FakeAdapter(),
            "Jibang",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )

        position_views = build_position_views(session, "Jibang")
        assert len(position_views) == 1
        view = position_views[0]
        assert view.market_id == "kalshi:TEST-APR"
        assert view.contract == "no"
        assert view.quantity == 10.0
        assert round(view.avg_price, 4) == 0.5
        assert round(view.realized_pnl, 4) == 1.25

        pending = build_pending_orders_by_ticker(session, "Jibang")
        assert list(pending) == ["TEST-APR"]
        assert pending["TEST-APR"][0]["order_id"] == "client-pending"
        assert pending["TEST-APR"][0]["count"] == 5.0
        assert pending["TEST-APR"][0]["filled_shares"] == 2.0
        assert pending["TEST-APR"][0]["price_cents"] == 51


def test_sync_pending_order_status_updates_local_orders_and_positions_from_snapshots():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add(
            BettingOrder(
                instance_name="Haifeng",
                signal_id=None,
                order_id="local-1",
                ticker="FLIP-APR",
                action="BUY",
                side="YES",
                count=4,
                price_cents=55,
                status="PENDING",
                filled_shares=0,
                fill_price=0,
                fee_paid=0,
                exchange_order_id="exchange-1",
                dry_run=False,
                created_at=datetime(2026, 3, 26, 20, 0, tzinfo=UTC),
            )
        )
        session.commit()

    adapter = _LifecycleAdapter()

    updated = _sync_pending_order_status(engine, adapter, "Haifeng")
    assert updated == 0
    with get_session(engine) as session:
        order = session.query(BettingOrder).filter(BettingOrder.order_id == "local-1").one()
        assert order.status == "PENDING"
        assert order.filled_shares == 0
        assert session.query(TradingPosition).count() == 0

    adapter._cycle = 1
    updated = _sync_pending_order_status(engine, adapter, "Haifeng")
    assert updated == 1
    with get_session(engine) as session:
        order = session.query(BettingOrder).filter(BettingOrder.order_id == "local-1").one()
        assert order.status == "FILLED"
        assert order.filled_shares == 4
        assert round(order.fill_price, 4) == 0.55
        pos = session.query(TradingPosition).filter(TradingPosition.market_id == "kalshi:FLIP-APR").one()
        assert pos.contract == "yes"
        assert pos.quantity == 4
        assert round(pos.avg_price, 4) == 0.55
