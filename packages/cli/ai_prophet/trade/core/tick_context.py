"""TickContext - immutable snapshot used for one tick execution.

Stages should consume this context instead of querying time or server state
directly. Keeping tick data in one immutable object reduces drift between
stages and makes execution/debugging deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from ai_prophet_core.ruleset import VALID_TICK_MINUTES
from ai_prophet_core.time import is_tick_boundary


@dataclass(frozen=True)
class Position:
    """Position with current market data attached."""
    market_id: str
    side: str  # "YES" or "NO"
    shares: Decimal
    avg_entry_price: Decimal
    current_price: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    updated_at: datetime


@dataclass(frozen=True)
class CandidateMarket:
    """Market with quote data for decision-making.

    Includes both YES and NO prices (NO derived from YES).
    """
    market_id: str
    question: str
    description: str | None
    resolution_time: datetime

    # YES token prices (from server)
    yes_bid: float
    yes_ask: float
    yes_mark: float  # midpoint

    # NO token prices (derived)
    no_bid: float  # = 1 - yes_ask
    no_ask: float  # = 1 - yes_bid
    no_mark: float  # = 1 - yes_mark

    # Market data
    volume_24h: float
    quote_ts: datetime

    # Position context (if agent holds this market)
    existing_position: Position | None = None

    @classmethod
    def from_server_response(
        cls,
        market_data: dict,
        existing_position: Position | None = None
    ) -> CandidateMarket:
        """Create from server /candidates response."""
        quote = market_data["quote"]

        # YES prices from server
        yes_bid = float(quote["best_bid"])
        yes_ask = float(quote["best_ask"])
        yes_mark = (yes_bid + yes_ask) / 2.0

        # NO prices derived (complement math)
        no_bid = 1.0 - yes_ask
        no_ask = 1.0 - yes_bid
        no_mark = 1.0 - yes_mark

        return cls(
            market_id=market_data["market_id"],
            question=market_data["question"],
            description=market_data.get("description"),
            resolution_time=market_data["resolution_time"],
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            yes_mark=yes_mark,
            no_bid=no_bid,
            no_ask=no_ask,
            no_mark=no_mark,
            volume_24h=quote.get("volume_24h", 0.0),
            quote_ts=quote["ts"],
            existing_position=existing_position,
        )

    def get_bid(self, side: str) -> float:
        """Get bid price for the given side."""
        return self.yes_bid if side == "YES" else self.no_bid

    def get_ask(self, side: str) -> float:
        """Get ask price for the given side."""
        return self.yes_ask if side == "YES" else self.no_ask

    def get_mark(self, side: str) -> float:
        """Get mark price for the given side."""
        return self.yes_mark if side == "YES" else self.no_mark

    def has_position(self) -> bool:
        """Check if agent has an existing position in this market."""
        return self.existing_position is not None

    def get_position_side(self) -> str | None:
        """Get the side of existing position, if any."""
        return self.existing_position.side if self.existing_position else None


@dataclass(frozen=True)
class TickContext:
    """Immutable snapshot of everything needed for one tick execution.

    This is the ONLY interface between server state and agent logic.
    No stage should ever query time or server directly.

    All timestamps and data are server-authoritative and coherent.
    """

    # Authority (server-provided)
    run_id: str
    tick_ts: datetime          # Server-normalized hour boundary
    data_asof_ts: datetime     # Server's coherent data cutoff
    candidate_set_id: str      # Must match in trade submission
    submission_deadline: datetime
    server_now: datetime

    # Market universe (≤256 markets)
    candidates: tuple[CandidateMarket, ...]

    # Portfolio state
    cash: Decimal
    equity: Decimal
    total_pnl: Decimal
    positions: tuple[Position, ...]

    # Trade history summary
    total_fills: int
    fills_this_tick: int = 0  # Will be updated during execution
    memory_summary: str | None = None
    memory_by_market: dict[str, str] | None = None

    def __post_init__(self):
        """Validate invariants."""
        # Ensure tick_ts is on valid boundary (derived from TICK_INTERVAL_SECONDS in ruleset)
        if not is_tick_boundary(self.tick_ts):
            raise ValueError(f"tick_ts must be on valid boundary (minutes: {VALID_TICK_MINUTES}): {self.tick_ts}")

        # Ensure data_asof_ts <= tick_ts
        if self.data_asof_ts > self.tick_ts:
            raise ValueError(f"data_asof_ts ({self.data_asof_ts}) > tick_ts ({self.tick_ts})")

        # Ensure submission_deadline > tick_ts
        if self.submission_deadline <= self.tick_ts:
            raise ValueError(
                f"submission_deadline ({self.submission_deadline}) must be after tick_ts ({self.tick_ts})"
            )

    def get_candidate(self, market_id: str) -> CandidateMarket | None:
        """Get candidate market by ID."""
        for candidate in self.candidates:
            if candidate.market_id == market_id:
                return candidate
        return None

    def get_position(self, market_id: str) -> Position | None:
        """Get position by market ID."""
        for position in self.positions:
            if position.market_id == market_id:
                return position
        return None

    def has_position(self, market_id: str) -> bool:
        """Check if we have a position in this market."""
        return self.get_position(market_id) is not None

    def time_until_deadline(self) -> float:
        """Seconds until submission deadline (based on server_now)."""
        delta = self.submission_deadline - self.server_now
        return delta.total_seconds()

    def is_past_deadline(self) -> bool:
        """Check if we're past the submission deadline."""
        return self.server_now >= self.submission_deadline

    @property
    def num_candidates(self) -> int:
        """Number of candidate markets."""
        return len(self.candidates)

    @property
    def num_positions(self) -> int:
        """Number of open positions."""
        return len(self.positions)

    @property
    def available_cash(self) -> Decimal:
        """Cash available for trading (same as cash for now)."""
        return self.cash

    @classmethod
    def from_server_responses(
        cls,
        run_id: str,
        tick_info: dict,
        candidates_response: dict,
        portfolio_response: dict,
    ) -> TickContext:
        """Create TickContext from server API responses.

        Args:
            run_id: Run identifier
            tick_info: Response from /ticks/next
            candidates_response: Response from /candidates
            portfolio_response: Response from /portfolio/{run_id}

        Returns:
            TickContext instance
        """
        def _as_datetime(value: datetime | str | None) -> datetime:
            if isinstance(value, datetime):
                return value
            if isinstance(value, str):
                return datetime.fromisoformat(value)
            raise ValueError(f"Expected datetime or ISO string, got: {type(value).__name__}")

        # Parse candidates
        candidates = []
        for market_data in candidates_response["markets"]:
            # Check if we have a position in this market
            position = None
            for pos_data in portfolio_response.get("positions", []):
                if pos_data["market_id"] == market_data["market_id"]:
                    position = Position(
                        market_id=pos_data["market_id"],
                        side=pos_data["side"],
                        shares=Decimal(pos_data["shares"]),
                        avg_entry_price=Decimal(pos_data["avg_entry_price"]),
                        current_price=Decimal(pos_data.get("current_price", "0")),
                        unrealized_pnl=Decimal(pos_data.get("unrealized_pnl", "0")),
                        realized_pnl=Decimal(pos_data.get("realized_pnl", "0")),
                        updated_at=_as_datetime(
                            pos_data.get("updated_at") or tick_info["server_now_ts"]
                        ),
                    )
                    break

            candidate = CandidateMarket.from_server_response(
                market_data,
                existing_position=position
            )
            candidates.append(candidate)

        # Parse positions
        positions = []
        for pos_data in portfolio_response.get("positions", []):
            position = Position(
                market_id=pos_data["market_id"],
                side=pos_data["side"],
                shares=Decimal(pos_data["shares"]),
                avg_entry_price=Decimal(pos_data["avg_entry_price"]),
                current_price=Decimal(pos_data.get("current_price", "0")),
                unrealized_pnl=Decimal(pos_data.get("unrealized_pnl", "0")),
                realized_pnl=Decimal(pos_data.get("realized_pnl", "0")),
                updated_at=_as_datetime(
                    pos_data.get("updated_at") or tick_info["server_now_ts"]
                ),
            )
            positions.append(position)

        return cls(
            run_id=run_id,
            tick_ts=_as_datetime(tick_info["tick_ts"]),
            data_asof_ts=_as_datetime(tick_info["data_asof_ts"]),
            candidate_set_id=tick_info["candidate_set_id"],
            submission_deadline=_as_datetime(tick_info["submission_deadline_ts"]),
            server_now=_as_datetime(tick_info["server_now_ts"]),
            candidates=tuple(candidates),
            cash=Decimal(portfolio_response["cash"]),
            equity=Decimal(portfolio_response["equity"]),
            total_pnl=Decimal(portfolio_response["total_pnl"]),
            positions=tuple(positions),
            total_fills=portfolio_response.get("total_fills", 0),
        )
