from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

# mcp_server depends on the `fastmcp` extra (``pip install ai-prophet-core[mcp]``).
# Skip cleanly when running tests without it (e.g. default CI install).
pytest.importorskip("fastmcp")

from ai_prophet_core import mcp_server  # noqa: E402


def test_claim_tick_exposes_only_candidate_set_id(monkeypatch):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def claim_tick(self, experiment_id, lease_owner):
            assert experiment_id == "exp-1"
            assert lease_owner
            return SimpleNamespace(
                tick_id="2026-03-01T12:00:00+00:00",
                snapshot_id="snap-1",
                candidate_set_id="snap-1",
                lease_expires_at="2026-03-01T12:10:00+00:00",
                reclaim_count=0,
                no_tick_available=None,
                retry_after_sec=None,
                reason=None,
            )

    monkeypatch.setattr(mcp_server, "_get_client", lambda: FakeClient())

    result = mcp_server.claim_tick("exp-1")

    assert result == {
        "tick_id": "2026-03-01T12:00:00+00:00",
        "candidate_set_id": "snap-1",
        "lease_expires_at": "2026-03-01T12:10:00+00:00",
        "reclaim_count": 0,
    }
    assert "snapshot_id" not in result


def test_get_current_markets_uses_market_snapshot_fields(monkeypatch):
    class FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def get_market_snapshot(self):
            return SimpleNamespace(
                candidate_set_id="snap-1",
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

    assert result["candidate_set_id"] == "snap-1"
    assert result["requested_as_of_ts"] == "2026-03-01T11:55:00+00:00"
    assert result["data_as_of_ts"] == "2026-03-01T11:56:00+00:00"
    assert result["market_count"] == 1


def test_forecast_submission_tool_is_not_exposed():
    assert not hasattr(mcp_server, "submit_forecast")


def test_betting_tools_are_not_exposed():
    """Kalshi exchange execution is not part of the MCP surface."""
    assert not hasattr(mcp_server, "forecast_to_trade")
    assert not hasattr(mcp_server, "place_trade")
    assert not hasattr(mcp_server, "_get_betting_engine")
