"""Unit tests for server response models."""



from ai_prophet_core import ReasoningResponse
from ai_prophet_core.client_models import (
    CandidatesResponse,
    ClaimTickResponse,
    CreateExperimentResponse,
    FillData,
    HealthResponse,
    MarketQuote,
    MarketSnapshot,
    ProgressResponse,
    ReasoningEntry,
    TradeIntentBatchRequest,
    TradeIntentRequest,
    TradeSubmissionResult,
    UpsertParticipantResponse,
)


def test_create_experiment_response():
    data = {"experiment_id": "exp_123", "status": "RUNNING", "created": True}
    resp = CreateExperimentResponse.model_validate(data)
    assert resp.experiment_id == "exp_123"
    assert resp.created is True


def test_upsert_participant_response():
    data = {"participant_idx": 2, "created": True}
    resp = UpsertParticipantResponse.model_validate(data)
    assert resp.participant_idx == 2


def test_claim_tick_response_success():
    data = {"tick_id": "2026-02-08T12:00:00Z", "snapshot_id": "snap_1",
            "lease_expires_at": "2026-02-08T12:10:00Z", "reclaim_count": 0}
    resp = ClaimTickResponse.model_validate(data)
    assert resp.tick_id == "2026-02-08T12:00:00Z"
    assert resp.candidate_set_id == "snap_1"
    assert resp.no_tick_available is None


def test_claim_tick_response_no_tick():
    data = {"no_tick_available": True, "reason": "experiment_completed"}
    resp = ClaimTickResponse.model_validate(data)
    assert resp.no_tick_available is True
    assert resp.reason == "experiment_completed"


def test_market_quote_model():
    data = {"best_bid": "0.45", "best_ask": "0.55", "volume_24h": 12345.67,
            "ts": "2026-01-20T05:30:00Z"}
    quote = MarketQuote.model_validate(data)
    assert quote.best_bid == "0.45"


def test_candidates_response_model():
    data = {
        "tick_ts": "2026-01-20T06:00:00Z",
        "data_asof_ts": "2026-01-20T05:31:39Z",
        "candidate_set_id": "snap_123",
        "market_count": 1,
        "markets": [{
            "market_id": "market_123", "question": "Will X happen?",
            "short_label": "X",
            "description": "Details...",
            "resolution_time": "2026-02-01T00:00:00Z",
            "source": "kalshi",
            "source_url": "https://kalshi.com/markets/ABC",
            "topic": "politics",
            "family": "ABC",
            "quote": {"best_bid": "0.45", "best_ask": "0.55",
                      "volume_24h": 1000.0, "ts": "2026-01-20T05:30:00Z"},
        }],
    }
    resp = CandidatesResponse.model_validate(data)
    assert resp.market_count == 1
    assert resp.markets[0].market_id == "market_123"
    assert resp.markets[0].short_label == "X"
    assert resp.markets[0].source == "kalshi"
    assert resp.markets[0].source_url == "https://kalshi.com/markets/ABC"
    assert resp.markets[0].topic == "politics"
    assert resp.markets[0].family == "ABC"


def test_market_snapshot_model_exposes_snapshot_alias():
    data = {
        "candidate_set_id": "snap_123",
        "requested_asof_ts": "2026-01-20T05:30:00Z",
        "data_asof_ts": "2026-01-20T05:31:39Z",
        "market_count": 1,
        "markets": [{
            "market_id": "market_123",
            "question": "Will X happen?",
            "description": "Details...",
            "resolution_time": "2026-02-01T00:00:00Z",
            "quote": {
                "best_bid": "0.45",
                "best_ask": "0.55",
                "volume_24h": 1000.0,
                "ts": "2026-01-20T05:30:00Z",
            },
        }],
    }
    resp = MarketSnapshot.model_validate(data)
    assert resp.snapshot_id == "snap_123"
    assert resp.data_asof_ts.isoformat() == "2026-01-20T05:31:39+00:00"


def test_trade_intent_request():
    data = {"market_id": "mkt_1", "action": "BUY", "side": "YES",
            "shares": "100.00", "idempotency_key": "k1"}
    intent = TradeIntentRequest.model_validate(data)
    assert intent.idempotency_key == "k1"


def test_trade_intent_batch_request():
    data = {
        "experiment_id": "exp_1", "participant_idx": 0,
        "tick_id": "2026-01-20T06:00:00Z", "candidate_set_id": "snap_123",
        "intents": [{"market_id": "mkt_1", "action": "BUY", "side": "YES",
                      "shares": "100.00", "idempotency_key": "k1"}],
    }
    batch = TradeIntentBatchRequest.model_validate(data)
    assert batch.experiment_id == "exp_1"
    assert len(batch.intents) == 1


def test_fill_data_model():
    data = {"fill_id": "f1", "intent_id": "i1", "market_id": "mkt_1",
            "action": "BUY", "side": "YES", "shares": "100.0", "price": "0.50",
            "notional": "50.0", "filled_at": "2026-01-20T06:00:00Z"}
    fill = FillData.model_validate(data)
    assert fill.fill_id == "f1"


def test_trade_submission_result():
    data = {
        "tick_ts": "2026-01-20T06:00:00Z", "data_asof_ts": "2026-01-20T05:31:39Z",
        "candidate_set_id": "snap_123", "accepted": 1, "rejected": 0,
        "fills": [{"fill_id": "f1", "intent_id": "i1", "market_id": "mkt_1",
                    "action": "BUY", "side": "YES", "shares": "100.0",
                    "price": "0.50", "notional": "50.0",
                    "filled_at": "2026-01-20T06:00:00Z"}],
        "rejections": [],
    }
    result = TradeSubmissionResult.model_validate(data)
    assert result.accepted == 1


def test_progress_response():
    data = {"experiment_id": "exp_1", "status": "RUNNING", "n_ticks": 96,
            "completed": 10, "skipped": 2, "failed_stuck": 0, "in_progress": 1}
    resp = ProgressResponse.model_validate(data)
    assert resp.completed == 10


def test_health_response():
    data = {"status": "ok", "version": "1.0.0", "service": "prophet-arena-core-api"}
    health = HealthResponse.model_validate(data)
    assert health.status == "ok"


def test_reasoning_response_is_exported_from_package_root():
    assert ReasoningResponse.__name__ == "ReasoningResponse"


def test_reasoning_entry_preserves_wire_tick_id_and_parses_tick_ts():
    entry = ReasoningEntry.model_validate(
        {
            "participant_idx": 1,
            "tick_id": "2026-01-20T06:00:00+00:00",
            "reasoning": {"forecast": []},
        }
    )

    assert entry.tick_id == "2026-01-20T06:00:00+00:00"
    assert entry.tick_ts.isoformat() == "2026-01-20T06:00:00+00:00"


def test_progress_response_exposes_parsed_timestamp_properties():
    resp = ProgressResponse.model_validate(
        {
            "experiment_id": "exp_1",
            "status": "RUNNING",
            "n_ticks": 96,
            "completed": 10,
            "skipped": 2,
            "failed_stuck": 0,
            "in_progress": 1,
            "last_completed_tick": "2026-01-20T06:00:00+00:00",
            "last_activity_at": "2026-01-20T06:05:00+00:00",
        }
    )

    assert resp.last_completed_tick_ts.isoformat() == "2026-01-20T06:00:00+00:00"
    assert resp.last_activity_at_ts.isoformat() == "2026-01-20T06:05:00+00:00"
