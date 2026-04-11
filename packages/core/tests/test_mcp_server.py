from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ai_prophet_core import mcp_server


@pytest.fixture(autouse=True)
def reset_betting_engine_cache():
    mcp_server._close_betting_engine()
    yield
    mcp_server._close_betting_engine()


def test_get_current_markets_uses_market_snapshot_fields(monkeypatch):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def get_market_snapshot(self):
            return SimpleNamespace(
                snapshot_id="snap-1",
                requested_asof_ts=datetime(2026, 3, 1, 11, 55, tzinfo=UTC),
                data_asof_ts=datetime(2026, 3, 1, 11, 56, tzinfo=UTC),
                market_count=1,
                markets=[
                    SimpleNamespace(
                        market_id="kalshi:TEST",
                        question="Will this resolve YES?",
                        description="test",
                        resolution_time=datetime(2026, 3, 20, 12, 0, tzinfo=UTC),
                        topic="testing",
                        quote=SimpleNamespace(
                            best_bid="0.48",
                            best_ask="0.52",
                            volume_24h=1000.0,
                        ),
                    )
                ],
            )

    monkeypatch.setattr(mcp_server, "_get_client", lambda: FakeClient())

    result = mcp_server.get_current_markets()

    assert result["snapshot_id"] == "snap-1"
    assert result["requested_as_of_ts"] == "2026-03-01T11:55:00+00:00"
    assert result["data_as_of_ts"] == "2026-03-01T11:56:00+00:00"
    assert result["market_count"] == 1


def test_get_betting_engine_uses_db_backing(monkeypatch):
    captured: dict[str, object] = {}
    calls = {"db": 0, "engine": 0}

    monkeypatch.setattr(
        "ai_prophet_core.betting.LiveBettingSettings.from_env",
        lambda: SimpleNamespace(enabled=True, paper=True, kalshi="kalshi-config"),
    )
    monkeypatch.setattr(
        "ai_prophet_core.betting.db.create_db_engine",
        lambda: calls.__setitem__("db", calls["db"] + 1) or "db-engine",
    )
    class FakeEngine(SimpleNamespace):
        def close(self):
            captured["closed"] = True

    def build_engine(**kwargs):
        calls["engine"] += 1
        captured.update(kwargs)
        return FakeEngine(enabled=kwargs["enabled"])

    monkeypatch.setattr("ai_prophet_core.betting.BettingEngine", build_engine)

    engine = mcp_server._get_betting_engine()
    cached = mcp_server._get_betting_engine()

    assert captured["db_engine"] == "db-engine"
    assert captured["kalshi_config"] == "kalshi-config"
    assert engine is cached
    assert calls == {"db": 1, "engine": 1}

    mcp_server._close_betting_engine()
    assert captured["closed"] is True
    assert mcp_server._betting_engine is None


def test_forecast_to_trade_reports_disabled_engine(monkeypatch):
    monkeypatch.setattr(
        mcp_server,
        "_get_betting_engine",
        lambda: SimpleNamespace(enabled=False),
    )

    result = mcp_server.forecast_to_trade(
        market_id="kalshi:TEST",
        p_yes=0.72,
        yes_ask=0.55,
        no_ask=0.45,
    )

    assert result == {
        "market_id": "kalshi:TEST",
        "order_placed": False,
        "status": "DISABLED",
        "reason": "betting engine disabled",
    }


def test_forecast_to_trade_reports_strategy_skip(monkeypatch):
    class FakeEngine:
        enabled = True

        def trade_from_forecast(self, **_kwargs):
            return None

    monkeypatch.setattr(mcp_server, "_get_betting_engine", lambda: FakeEngine())

    result = mcp_server.forecast_to_trade(
        market_id="kalshi:TEST",
        p_yes=0.72,
        yes_ask=0.55,
        no_ask=0.45,
    )

    assert result == {
        "market_id": "kalshi:TEST",
        "order_placed": False,
        "status": "SKIP",
        "reason": "strategy passed",
    }
