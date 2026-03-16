"""Core Pydantic models for Prophet Arena.

Shared across client, server, and indexer.

The SDK keeps API wire payload models in ``client_models.py``. This module
contains richer in-process domain models; quote-like observation data may use
floats, while deterministic accounting converts to ``Decimal`` at execution
boundaries via ``decimal_utils``.
"""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_prophet_core.ruleset import VALID_TICK_MINUTES
from ai_prophet_core.time import is_tick_boundary

# --- Enums -------------------------------------------------------------------

class TradeAction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class TradeSide(str, Enum):
    YES = "YES"
    NO = "NO"


class SizeType(str, Enum):
    NOTIONAL = "NOTIONAL"
    SHARES = "SHARES"


class Confidence(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RecommendationDirection(str, Enum):
    """LLM pipeline trade recommendation.

    Note: SELL is handled at the execution layer (TradeAction.SELL) but is not
    surfaced to the LLM pipeline in v1. Position exits happen at resolution.
    """
    BUY_YES = "BUY_YES"
    BUY_NO = "BUY_NO"
    HOLD = "HOLD"


# --- Market Data Models -------------------------------------------------------

class Market(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_id: str
    question: str
    short_label: str | None = None
    description: str | None = None
    resolution_time: datetime
    created_at: datetime
    source: str
    source_market_id: str
    source_url: str | None = None
    topic: str | None = None
    family: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Quote(BaseModel):
    """Market quote snapshot.

    Prices use float intentionally: quotes are external market observation data,
    not internal accounting. Decimal conversion happens at trade execution time
    via decimal_utils (q_price, q_shares, q_cash).
    """
    model_config = ConfigDict(frozen=True)

    quote_id: str
    market_id: str
    ts: datetime
    ingested_at: datetime
    best_bid: float = Field(ge=0.0, le=1.0)
    best_ask: float = Field(ge=0.0, le=1.0)
    bid_size: float = Field(ge=0.0)
    ask_size: float = Field(ge=0.0)
    volume_24h: float = Field(ge=0.0)

    @field_validator("best_ask")
    @classmethod
    def ask_gte_bid(cls, v: float, info) -> float:
        if "best_bid" in info.data and v < info.data["best_bid"]:
            raise ValueError("best_ask must be >= best_bid")
        return v


# --- Candidate Universe Models ------------------------------------------------

class CandidateSetSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    as_of_ts: datetime
    market_ids: list[str]
    filter_metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- Experiment Models --------------------------------------------------------

class Experiment(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    experiment_slug: str
    status: Literal["CREATED", "RUNNING", "COMPLETED", "ABORTED"]
    config_hash: str
    config_json: dict[str, Any] = Field(default_factory=dict)
    n_ticks: int
    completed_ticks: int = 0
    created_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    last_activity_at: datetime | None = None


class Participant(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    participant_idx: int
    model: str
    rep: int = 0
    starting_cash: float
    created_at: datetime


# --- Trade Models -------------------------------------------------------------

class TradeIntent(BaseModel):
    """Trade intent for internal execution."""

    intent_id: str
    experiment_id: str
    participant_idx: int
    tick_ts: datetime
    market_id: str
    action: Literal[TradeAction.BUY, TradeAction.SELL]
    side: TradeSide
    size_type: SizeType
    size: float = Field(gt=0.0)
    submitted_at: datetime

    @field_validator("tick_ts")
    @classmethod
    def validate_tick(cls, v: datetime) -> datetime:
        if not is_tick_boundary(v):
            raise ValueError(f"tick_ts must be on valid boundary (minutes: {VALID_TICK_MINUTES})")
        return v


class Fill(BaseModel):
    model_config = ConfigDict(frozen=True)

    fill_id: str
    intent_id: str
    experiment_id: str
    participant_idx: int
    market_id: str
    action: Literal[TradeAction.BUY, TradeAction.SELL]
    side: TradeSide
    shares: float
    price: float = Field(ge=0.0, le=1.0)
    notional: float
    fee: float = Field(ge=0.0)
    filled_at: datetime
    quote_id: str


class Position(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    participant_idx: int
    market_id: str
    side: TradeSide
    shares: float
    avg_entry_price: float = Field(ge=0.0, le=1.0)
    current_price: float = Field(ge=0.0, le=1.0)
    unrealized_pnl: float
    realized_pnl: float
    updated_at: datetime


# --- Portfolio Models ---------------------------------------------------------

class Portfolio(BaseModel):
    model_config = ConfigDict(frozen=True)

    experiment_id: str
    participant_idx: int
    tick_ts: datetime
    cash: float
    positions: list[Position]
    equity: float
    total_pnl: float


