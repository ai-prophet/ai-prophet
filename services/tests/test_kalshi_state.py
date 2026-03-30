from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import create_engine

from ai_prophet_core.betting.db import get_session
from ai_prophet_core.betting.db_schema import Base, BettingOrder
from db_models import KalshiBalanceSnapshot, KalshiOrderSnapshot, KalshiPositionSnapshot, TradingMarket, TradingPosition
from kalshi_state import (
    build_pending_orders_by_ticker,
    build_portfolio_summary,
    build_position_views,
    get_latest_position_snapshots,
    get_resolved_markets,
    record_kalshi_state,
)
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


class _DisappearingPositionAdapter:
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
            return [
                {
                    "ticker": "VANISH-APR",
                    "position_fp": "5.00",
                    "market_exposure_dollars": "2.5000",
                    "realized_pnl_dollars": "0.1500",
                    "fees_paid_dollars": "0.0300",
                    "total_cost_dollars": "2.5000",
                    "total_cost_shares_fp": "5.00",
                    "resting_orders_count": 0,
                }
            ]
        return []

    def get_orders(self, *, status=None, ticker=None):
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


def test_record_kalshi_state_skips_existing_order_versions_on_later_syncs():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        first_counts = record_kalshi_state(
            session,
            _FakeAdapter(),
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )
        second_counts = record_kalshi_state(
            session,
            _FakeAdapter(),
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 26, 20, 20, tzinfo=UTC),
        )

        assert first_counts == {"balances": 1, "positions": 1, "orders": 2}
        assert second_counts == {"balances": 1, "positions": 1, "orders": 0}
        assert session.query(KalshiOrderSnapshot).count() == 2
        assert session.query(KalshiBalanceSnapshot).count() == 2
        assert session.query(KalshiPositionSnapshot).count() == 2


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


def test_record_kalshi_state_reconciles_missing_positions_to_zero_snapshots():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    adapter = _DisappearingPositionAdapter()

    with get_session(engine) as session:
        first_counts = record_kalshi_state(
            session,
            adapter,
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 28, 15, 30, tzinfo=UTC),
        )
        adapter._cycle = 1
        second_counts = record_kalshi_state(
            session,
            adapter,
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 28, 15, 45, tzinfo=UTC),
        )

        position_views = build_position_views(session, "Haifeng")
        snapshots = (
            session.query(KalshiPositionSnapshot)
            .filter(KalshiPositionSnapshot.ticker == "VANISH-APR")
            .order_by(KalshiPositionSnapshot.snapshot_ts.asc())
            .all()
        )

    assert first_counts == {"balances": 1, "positions": 1, "orders": 0}
    assert second_counts == {"balances": 1, "positions": 1, "orders": 0}
    assert position_views == []
    assert len(snapshots) == 2
    assert snapshots[0].signed_quantity == 5.0
    assert snapshots[1].signed_quantity == 0.0
    assert snapshots[1].market_exposure == 0.0


def test_get_latest_position_snapshots_reads_latest_full_snapshot_batch():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add_all([
            KalshiPositionSnapshot(
                instance_name="Haifeng",
                ticker="OLD-YES",
                market_id="kalshi:OLD-YES",
                side="yes",
                signed_quantity=7.0,
                quantity=7.0,
                market_exposure=4.2,
                realized_pnl=0.0,
                fees_paid=0.0,
                total_cost=4.2,
                total_cost_shares=7.0,
                total_traded=4.2,
                resting_orders_count=0,
                snapshot_ts=datetime(2026, 3, 28, 15, 30, tzinfo=UTC),
                raw_json="{}",
            ),
            KalshiPositionSnapshot(
                instance_name="Haifeng",
                ticker="OLD-NO",
                market_id="kalshi:OLD-NO",
                side="no",
                signed_quantity=-3.0,
                quantity=3.0,
                market_exposure=1.8,
                realized_pnl=0.0,
                fees_paid=0.0,
                total_cost=1.8,
                total_cost_shares=3.0,
                total_traded=1.8,
                resting_orders_count=0,
                snapshot_ts=datetime(2026, 3, 28, 15, 30, tzinfo=UTC),
                raw_json="{}",
            ),
            KalshiPositionSnapshot(
                instance_name="Haifeng",
                ticker="OLD-YES",
                market_id="kalshi:OLD-YES",
                side="yes",
                signed_quantity=0.0,
                quantity=0.0,
                market_exposure=0.0,
                realized_pnl=0.5,
                fees_paid=0.0,
                total_cost=0.0,
                total_cost_shares=0.0,
                total_traded=4.2,
                resting_orders_count=0,
                snapshot_ts=datetime(2026, 3, 28, 15, 45, tzinfo=UTC),
                raw_json='{"reconciled_missing": true}',
            ),
            KalshiPositionSnapshot(
                instance_name="Haifeng",
                ticker="LIVE-YES",
                market_id="kalshi:LIVE-YES",
                side="yes",
                signed_quantity=2.0,
                quantity=2.0,
                market_exposure=1.1,
                realized_pnl=0.0,
                fees_paid=0.0,
                total_cost=1.1,
                total_cost_shares=2.0,
                total_traded=1.1,
                resting_orders_count=0,
                snapshot_ts=datetime(2026, 3, 28, 15, 45, tzinfo=UTC),
                raw_json="{}",
            ),
        ])
        session.commit()

        latest = get_latest_position_snapshots(session, "Haifeng")

    assert set(latest) == {"OLD-YES", "LIVE-YES"}
    assert latest["OLD-YES"].signed_quantity == 0.0
    assert latest["LIVE-YES"].signed_quantity == 2.0


def test_build_portfolio_summary_uses_last_traded_price_times_quantity():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Jibang",
                market_id="kalshi:TEST-APR",
                ticker="TEST-APR",
                event_ticker="TEST",
                title="Test market",
                category="POLITICS",
                expiration=datetime(2026, 4, 1, tzinfo=UTC),
                last_price=0.41,
                yes_bid=0.4,
                yes_ask=0.42,
                no_bid=0.58,
                no_ask=0.6,
                volume_24h=100,
                updated_at=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
            )
        )
        session.commit()

        record_kalshi_state(
            session,
            _FakeAdapter(),
            "Jibang",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )

        summary = build_portfolio_summary(session, "Jibang")
        assert round(summary.open_value, 4) == 5.9
        assert round(summary.cash_spent, 4) == 5.0
        assert round(summary.net_pnl, 4) == 2.15


def test_build_portfolio_summary_can_prefer_synced_portfolio_value():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Jibang",
                market_id="kalshi:TEST-APR",
                ticker="TEST-APR",
                event_ticker="TEST",
                title="Test market",
                category="POLITICS",
                expiration=datetime(2026, 4, 1, tzinfo=UTC),
                last_price=0.41,
                yes_bid=0.4,
                yes_ask=0.42,
                no_bid=0.58,
                no_ask=0.6,
                volume_24h=100,
                updated_at=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
            )
        )
        session.commit()

        record_kalshi_state(
            session,
            _FakeAdapter(),
            "Jibang",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )

        summary = build_portfolio_summary(
            session,
            "Jibang",
            prefer_synced_portfolio_value=True,
        )
        assert round(summary.open_value, 4) == 496.11
        assert round(summary.cash_spent, 4) == 5.0
        assert round(summary.net_pnl, 4) == 492.36


def test_build_portfolio_summary_with_starting_total_reconciles_to_equity():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        session.add(
            TradingMarket(
                instance_name="Haifeng",
                market_id="kalshi:TEST-APR",
                ticker="TEST-APR",
                event_ticker="TEST",
                title="Test market",
                category="POLITICS",
                expiration=datetime(2026, 4, 1, tzinfo=UTC),
                last_price=0.41,
                yes_bid=0.4,
                yes_ask=0.42,
                no_bid=0.58,
                no_ask=0.6,
                volume_24h=100,
                updated_at=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
            )
        )
        session.commit()

        record_kalshi_state(
            session,
            _FakeAdapter(),
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
        )

        summary = build_portfolio_summary(
            session,
            "Haifeng",
            starting_total=441.65,
            prefer_synced_portfolio_value=True,
        )
        expected_net_pnl = (458.87 + 496.11) - 441.65
        assert round(summary.cash_balance, 4) == 458.87
        assert round(summary.open_value, 4) == 496.11
        assert round(summary.net_pnl, 4) == round(expected_net_pnl, 4)
        assert round(summary.return_pct, 6) == round(expected_net_pnl / 441.65, 6)


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


class _FakeAdapterWithSettlements:
    """Test adapter with settlements and fills data."""

    def get_balance_details(self):
        return {
            "balance": 55000,
            "portfolio_value": 60000,
            "updated_ts": 1764200000,
        }

    def get_positions(self):
        return []

    def get_orders(self, *, status=None, ticker=None):
        return []

    def get_historical_orders(self, *, ticker=None):
        return []

    def get_settlements(self, *, limit=200):
        """Return fake settlement data for testing."""
        return [
            {
                "ticker": "BIDEN-WIN",
                "market_result": "no",
                "yes_count": 10.0,
                "no_count": 0.0,
                "revenue": 0.0,
                "fee_cost": 0.5,
                "settled_time": "2024-11-06T12:00:00Z",
            },
            {
                "ticker": "TRUMP-WIN",
                "market_result": "yes",
                "yes_count": 25.0,
                "no_count": 0.0,
                "revenue": 25.0,
                "fee_cost": 1.25,
                "settled_time": "2024-11-06T12:00:00Z",
            },
            {
                "ticker": "SENATE-GOP",
                "market_result": "yes",
                "yes_count": 15.0,
                "no_count": 5.0,
                "revenue": 15.0,
                "fee_cost": 0.75,
                "settled_time": "2024-11-06T14:00:00Z",
            },
        ]

    def get_fills(self, *, ticker=None, limit=200):
        """Return fake fills data for testing."""
        return [
            {
                "ticker": "EARLY-EXIT",
                "action": "buy",
                "side": "yes",
                "count_fp": "20.00",
                "yes_price_dollars": "0.45",
                "fee_cost": "0.20",
                "created_time": "2024-10-15T10:00:00Z",
            },
            {
                "ticker": "EARLY-EXIT",
                "action": "sell",
                "side": "yes",
                "count_fp": "20.00",
                "yes_price_dollars": "0.65",
                "fee_cost": "0.20",
                "created_time": "2024-10-20T10:00:00Z",
            },
        ]


def test_get_resolved_markets():
    """Test fetching resolved markets including settlements and sold positions."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        adapter = _FakeAdapterWithSettlements()
        resolved_markets = get_resolved_markets(
            session,
            adapter,
            "Haifeng",
            limit=100,
            include_sold_positions=True,
        )

        assert len(resolved_markets) == 4

        # Check settlements
        biden_market = [m for m in resolved_markets if m["ticker"] == "BIDEN-WIN"][0]
        assert biden_market["market_result"] == "NO"
        assert biden_market["outcome"] == "LOST"
        assert biden_market["yes_contracts"] == 10.0
        assert biden_market["winning_contracts"] == 0.0
        assert biden_market["losing_contracts"] == 10.0
        assert biden_market["realized_pnl"] == -0.5

        trump_market = [m for m in resolved_markets if m["ticker"] == "TRUMP-WIN"][0]
        assert trump_market["market_result"] == "YES"
        assert trump_market["outcome"] == "WON"
        assert trump_market["yes_contracts"] == 25.0
        assert trump_market["winning_contracts"] == 25.0
        assert trump_market["realized_pnl"] == 23.75

        senate_market = [m for m in resolved_markets if m["ticker"] == "SENATE-GOP"][0]
        assert senate_market["market_result"] == "YES"
        assert senate_market["yes_contracts"] == 15.0
        assert senate_market["no_contracts"] == 5.0
        assert senate_market["winning_contracts"] == 15.0
        assert senate_market["losing_contracts"] == 5.0

        # Check sold position
        early_exit = [m for m in resolved_markets if m["ticker"] == "EARLY-EXIT"][0]
        assert early_exit["market_result"] == "SOLD"
        assert early_exit["outcome"] == "SOLD"
        assert early_exit["sold_before_resolution"] is True
        assert early_exit["buy_volume"] == 20.0
        assert early_exit["sell_volume"] == 20.0
        assert early_exit["cost"] == 9.0  # 20 * 0.45
        assert early_exit["revenue"] == 13.0  # 20 * 0.65
        assert early_exit["realized_pnl"] == 3.6  # 13 - 9 - 0.4 (fees)


def test_record_kalshi_state_with_settlements():
    """Test recording Kalshi state with settlements included."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)

    with get_session(engine) as session:
        adapter = _FakeAdapterWithSettlements()
        counts = record_kalshi_state(
            session,
            adapter,
            "Haifeng",
            snapshot_ts=datetime(2026, 3, 26, 20, 10, tzinfo=UTC),
            include_settlements=True,
        )

        assert counts["balances"] == 1
        assert counts["positions"] == 0
        assert counts["orders"] == 0
        assert counts["settlements"] == 3
