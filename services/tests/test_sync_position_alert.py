#!/usr/bin/env python3

from __future__ import annotations

from datetime import timezone
from unittest.mock import MagicMock, patch


UTC = timezone.utc


def test_alert_on_position_snapshot_mismatch_can_log_autocorrect_without_name_error():
    from kalshi_sync_service import _alert_on_position_snapshot_mismatch

    adapter = MagicMock()
    adapter.get_positions.return_value = [{"ticker": "TEST", "position_fp": "3.0"}]

    first_session = MagicMock()
    first_session.query().filter().all.return_value = []
    second_session = MagicMock()

    first_ctx = MagicMock()
    first_ctx.__enter__.return_value = first_session
    first_ctx.__exit__.return_value = False
    second_ctx = MagicMock()
    second_ctx.__enter__.return_value = second_session
    second_ctx.__exit__.return_value = False

    with patch("ai_prophet_core.betting.db.get_session", side_effect=[first_ctx, second_ctx]):
        with patch("kalshi_state.get_latest_position_snapshots", return_value={}):
            with patch("kalshi_state.sync_trading_positions_from_snapshots", return_value=1) as mock_sync:
                with patch("kalshi_sync_service.log_sync_event") as mock_log_sync_event:
                    _alert_on_position_snapshot_mismatch(MagicMock(), adapter, "TestInstance")

    mock_sync.assert_called_once()
    second_session.add.assert_called_once()
    second_session.commit.assert_called_once()
    mock_log_sync_event.assert_not_called()
