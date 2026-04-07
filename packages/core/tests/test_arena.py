"""Tests for BenchmarkSession tick lifecycle primitive."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock

from ai_prophet_core.arena import BenchmarkSession, TickLease, SubmissionResult


def _mock_api():
    return Mock()


def test_create_experiment_sets_experiment_id():
    api = _mock_api()
    api.create_or_get_experiment.return_value = SimpleNamespace(
        experiment_id="exp-1", status="RUNNING", created=True,
    )
    session = BenchmarkSession(api)
    resp = session.create_experiment(slug="test", config_hash="h", config_json={}, n_ticks=10)
    assert session.experiment_id == "exp-1"
    assert resp.created is True


def test_upsert_participant_uses_experiment_id():
    api = _mock_api()
    api.create_or_get_experiment.return_value = SimpleNamespace(
        experiment_id="exp-1", status="RUNNING", created=True,
    )
    api.upsert_participant.return_value = SimpleNamespace(participant_idx=0, created=True)

    session = BenchmarkSession(api)
    session.create_experiment(slug="test", config_hash="h", config_json={}, n_ticks=10)
    resp = session.upsert_participant(model="gpt-4o")

    api.upsert_participant.assert_called_once_with("exp-1", model="gpt-4o", rep=0, starting_cash=10000.0)
    assert resp.participant_idx == 0


def test_claim_tick_returns_available_lease():
    api = _mock_api()
    api.claim_tick.return_value = SimpleNamespace(
        no_tick_available=False,
        tick_id="2026-01-01T00:00:00+00:00",
        snapshot_id="snap-1",
        candidate_set_id="snap-1",
        reason=None,
        retry_after_sec=None,
    )

    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"
    lease = session.claim_tick()

    assert lease.available is True
    assert lease.tick_id == "2026-01-01T00:00:00+00:00"
    assert lease.candidate_set_id == "snap-1"


def test_claim_tick_returns_unavailable_lease():
    api = _mock_api()
    api.claim_tick.return_value = SimpleNamespace(
        no_tick_available=True,
        tick_id=None,
        snapshot_id=None,
        candidate_set_id=None,
        reason="experiment_completed",
        retry_after_sec=None,
    )

    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"
    lease = session.claim_tick()

    assert lease.available is False
    assert lease.reason == "experiment_completed"


def test_submit_intents_builds_idempotency_keys():
    api = _mock_api()
    api.submit_trade_intents.return_value = SimpleNamespace(
        accepted=2, rejected=0, fills=[], rejections=[],
    )

    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"

    lease = TickLease(available=True, tick_id="2026-01-01T00:00:00+00:00", candidate_set_id="snap-1")

    from ai_prophet_core.client_models import TradeIntentRequest
    intents = [
        TradeIntentRequest(market_id="m1", action="BUY", side="YES", shares="100", idempotency_key=""),
        TradeIntentRequest(market_id="m2", action="BUY", side="NO", shares="50", idempotency_key=""),
    ]

    result = session.submit_intents(lease, participant_idx=0, intents=intents)

    call_args = api.submit_trade_intents.call_args
    submitted = call_args.kwargs.get("intents") or call_args[1].get("intents")
    assert submitted[0].idempotency_key == "exp-1:0:2026-01-01T00:00:00+00:00:0"
    assert submitted[1].idempotency_key == "exp-1:0:2026-01-01T00:00:00+00:00:1"
    assert result.accepted == 2


def test_submit_intents_custom_key_fn():
    api = _mock_api()
    api.submit_trade_intents.return_value = SimpleNamespace(
        accepted=1, rejected=0, fills=[], rejections=[],
    )

    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"

    lease = TickLease(available=True, tick_id="tick-1", candidate_set_id="snap-1")

    from ai_prophet_core.client_models import TradeIntentRequest
    intents = [
        TradeIntentRequest(market_id="m1", action="BUY", side="YES", shares="100", idempotency_key=""),
    ]

    custom_fn = lambda exp, idx, tick, i: f"custom:{exp}:{i}"
    session.submit_intents(lease, participant_idx=0, intents=intents, idempotency_key_fn=custom_fn)

    call_args = api.submit_trade_intents.call_args
    submitted = call_args.kwargs.get("intents") or call_args[1].get("intents")
    assert submitted[0].idempotency_key == "custom:exp-1:0"


def test_finalize_calls_api():
    api = _mock_api()
    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"

    lease = TickLease(available=True, tick_id="tick-1", candidate_set_id="snap-1")
    session.finalize(lease, participant_idx=0, status="COMPLETED")

    api.finalize_participant.assert_called_once_with(
        "exp-1", 0, "tick-1", "COMPLETED", error_code=None, error_detail=None,
    )


def test_complete_tick_calls_api():
    api = _mock_api()
    session = BenchmarkSession(api)
    session.experiment_id = "exp-1"

    lease = TickLease(available=True, tick_id="tick-1", candidate_set_id="snap-1")
    session.complete_tick(lease)

    api.complete_tick.assert_called_once_with("exp-1", "tick-1")


def test_require_experiment_id_raises_before_init():
    session = BenchmarkSession(_mock_api())
    try:
        session._require_experiment_id()
        assert False, "Should have raised"
    except RuntimeError as e:
        assert "not initialized" in str(e)


def test_tick_lease_tick_ts_property():
    lease = TickLease(available=True, tick_id="2026-03-01T12:00:00+00:00")
    assert lease.tick_ts == datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

    empty = TickLease(available=False)
    assert empty.tick_ts is None
