from __future__ import annotations

from datetime import UTC, datetime

from db_models import KalshiBalanceSnapshot
from services.api.main import _build_snapshot_backed_pnl_series


def test_snapshot_backed_pnl_series_anchors_final_point_to_target_net_pnl():
    snapshots = [
        KalshiBalanceSnapshot(
            instance_name="Haifeng",
            balance=490.0,
            portfolio_value=0.0,
            updated_ts=datetime(2026, 3, 24, 23, 10, tzinfo=UTC),
            snapshot_ts=datetime(2026, 3, 24, 23, 10, tzinfo=UTC),
            raw_json=None,
        ),
        KalshiBalanceSnapshot(
            instance_name="Haifeng",
            balance=429.97,
            portfolio_value=65.25,
            updated_ts=datetime(2026, 3, 27, 5, 0, tzinfo=UTC),
            snapshot_ts=datetime(2026, 3, 27, 5, 0, tzinfo=UTC),
            raw_json=None,
        ),
    ]

    series = _build_snapshot_backed_pnl_series(
        snapshots,
        realized_events=[(datetime(2026, 3, 27, 4, 55, tzinfo=UTC), 1.56)],
        target_net_pnl=7.30,
    )

    assert len(series) == 2
    assert series[-1]["timestamp"] == "2026-03-27T05:00:00+00:00"
    assert series[-1]["pnl"] == 7.3
    assert series[0]["pnl"] == 2.08
    assert series[-1]["cash_pnl"] == 1.56
    assert series[-1]["cash_spent"] == 59.51


def test_snapshot_backed_pnl_series_returns_empty_without_snapshots():
    assert _build_snapshot_backed_pnl_series([], [], 0.0) == []
