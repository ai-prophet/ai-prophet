"""Wire-format request and response models for the Core API.

Tick identity convention:
- ``tick_id`` is the wire-format ISO timestamp string used by lease/finalize APIs.
- ``tick_ts`` is the parsed ``datetime`` representation used internally.

These models mirror the API payload shape closely. When the API transmits
timestamps or decimal-like values as strings, the wire models preserve that
shape and expose parsed convenience properties where needed.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

# --- Experiment Models --------------------------------------------------------

class CreateExperimentRequest(BaseModel):
    experiment_slug: str
    config_hash: str
    config_json: dict = Field(default_factory=dict)
    n_ticks: int


class CreateExperimentResponse(BaseModel):
    experiment_id: str
    status: str
    created: bool


class UpsertParticipantRequest(BaseModel):
    model: str
    rep: int = 0
    starting_cash: float = 10000.0


class UpsertParticipantResponse(BaseModel):
    participant_idx: int
    created: bool


# --- Tick Claim Models --------------------------------------------------------

class ClaimTickRequest(BaseModel):
    lease_owner_id: str
    lease_sec: int = 600


class ClaimTickResponse(BaseModel):
    """Tick claim result. Check no_tick_available first."""
    # Success fields
    tick_id: str | None = None
    snapshot_id: str | None = None
    snapshot_hash: str | None = None
    lease_expires_at: str | None = None
    reclaim_count: int | None = None
    # Failure fields
    no_tick_available: bool | None = None
    retry_after_sec: int | None = None
    reason: str | None = None

    @property
    def tick_ts(self) -> datetime | None:
        """Parsed datetime view of ``tick_id`` for internal use."""
        if not self.tick_id:
            return None
        return datetime.fromisoformat(self.tick_id)

    @property
    def lease_expires_at_ts(self) -> datetime | None:
        """Parsed datetime view of ``lease_expires_at`` for internal use."""
        if not self.lease_expires_at:
            return None
        return datetime.fromisoformat(self.lease_expires_at)


# --- Plan / Finalize Models ---------------------------------------------------

class PlanRequest(BaseModel):
    snapshot_id: str
    plan_json: dict


class PutPlanResponse(BaseModel):
    plan_json: dict = Field(default_factory=dict)
    already_persisted: bool = False


class FinalizeRequest(BaseModel):
    status: str
    error_code: str | None = None
    error_detail: str | None = None


class FinalizeResponse(BaseModel):
    status: str | None = None
    detail: str | None = None


class CompleteTickResponse(BaseModel):
    status: str | None = None
    detail: str | None = None


class ReasoningEntry(BaseModel):
    participant_idx: int
    tick_id: str
    reasoning: dict[str, Any]

    @field_validator("tick_id", mode="before")
    @classmethod
    def normalize_tick_id(cls, value: Any) -> str:
        if value is None:
            raise ValueError("tick_id is required")
        if isinstance(value, datetime):
            return value.isoformat()
        return str(value)

    @property
    def tick_ts(self) -> datetime:
        """Parsed datetime view of ``tick_id`` for internal use."""
        return datetime.fromisoformat(self.tick_id)


class ReasoningResponse(BaseModel):
    experiment_id: str
    total: int
    reasoning: list[ReasoningEntry]


# --- Candidates Models --------------------------------------------------------

class MarketQuote(BaseModel):
    best_bid: str
    best_ask: str
    volume_24h: float
    ts: datetime


class MarketData(BaseModel):
    market_id: str
    question: str
    description: str | None = None
    resolution_time: datetime
    quote: MarketQuote


class CandidatesResponse(BaseModel):
    tick_ts: datetime
    data_asof_ts: datetime
    candidate_set_id: str
    market_count: int
    markets: list[MarketData]


# --- Trade Submission Models --------------------------------------------------

class TradeIntentRequest(BaseModel):
    """Single trade intent for submission."""
    market_id: str
    action: str
    side: str
    shares: str
    idempotency_key: str


class TradeIntentBatchRequest(BaseModel):
    """Batch of trade intents."""
    experiment_id: str
    participant_idx: int
    tick_id: str
    candidate_set_id: str
    intents: list[TradeIntentRequest]


class FillData(BaseModel):
    fill_id: str
    intent_id: str
    market_id: str
    action: str
    side: str
    shares: str
    price: str
    notional: str
    filled_at: datetime


class RejectionData(BaseModel):
    intent_id: str
    reason: str


class TradeSubmissionResult(BaseModel):
    tick_ts: datetime
    data_asof_ts: datetime
    candidate_set_id: str
    accepted: int
    rejected: int
    fills: list[FillData]
    rejections: list[RejectionData]


# --- Portfolio Models ---------------------------------------------------------

class PositionData(BaseModel):
    market_id: str
    side: str
    shares: str
    avg_entry_price: str
    current_price: str = "0"
    unrealized_pnl: str = "0"
    realized_pnl: str = "0"
    updated_at: datetime | None = None


class PortfolioResponse(BaseModel):
    experiment_id: str
    participant_idx: int
    cash: str
    equity: str
    total_pnl: str = "0"
    positions: list[PositionData] = Field(default_factory=list)
    total_fills: int = 0


# --- Health / Progress Models -------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    version: str
    service: str
    timestamp: datetime | None = None
    database: dict | None = None


class ProgressResponse(BaseModel):
    experiment_id: str
    status: str
    n_ticks: int
    completed: int
    skipped: int
    failed_stuck: int
    in_progress: int
    last_completed_tick: str | None = None
    last_activity_at: str | None = None

    @property
    def last_completed_tick_ts(self) -> datetime | None:
        """Parsed datetime view of ``last_completed_tick``."""
        if not self.last_completed_tick:
            return None
        return datetime.fromisoformat(self.last_completed_tick)

    @property
    def last_activity_at_ts(self) -> datetime | None:
        """Parsed datetime view of ``last_activity_at``."""
        if not self.last_activity_at:
            return None
        return datetime.fromisoformat(self.last_activity_at)
