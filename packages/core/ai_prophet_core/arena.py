"""Benchmark session primitive for Prophet Arena tick-based experiments.

Thin wrapper around ServerAPIClient that manages the tick lifecycle:
create experiment, upsert participants, claim tick, fetch candidates,
submit intents, finalize, complete tick.

Does NOT handle concurrency, timeouts, pipeline stages, memory,
tracing, or any CLI-specific orchestration. Those belong in the CLI's
ExperimentRunner which delegates API calls to this session.

Agent builders who don't want tick semantics should use
ServerAPIClient.get_market_snapshot() directly instead.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .client import ServerAPIClient
from .client_models import (
    CandidatesResponse,
    FillData,
    PortfolioResponse,
    RejectionData,
    TradeIntentRequest,
)


@dataclass(frozen=True)
class TickLease:
    """Result of claiming a tick. Check ``available`` before using fields."""
    available: bool
    tick_id: str | None = None
    candidate_set_id: str | None = None
    reason: str | None = None
    retry_after_sec: int | None = None

    @property
    def tick_ts(self) -> datetime | None:
        if not self.tick_id:
            return None
        return datetime.fromisoformat(self.tick_id)


@dataclass(frozen=True)
class SubmissionResult:
    """Result of submitting trade intents for a tick."""
    accepted: int
    rejected: int
    fills: list[FillData] = field(default_factory=list)
    rejections: list[RejectionData] = field(default_factory=list)


def _default_idempotency_key(
    experiment_id: str, participant_idx: int, tick_id: str, index: int,
) -> str:
    return f"{experiment_id}:{participant_idx}:{tick_id}:{index}"


class BenchmarkSession:
    """Tick-lifecycle primitive for Prophet Arena benchmark runs.

    Usage::

        api = ServerAPIClient(base_url="...", api_key="...")
        session = BenchmarkSession(api)
        resp = session.create_experiment(slug="my-run", ...)
        session.upsert_participant(model="custom:mine")

        while True:
            lease = session.claim_tick()
            if not lease.available:
                break
            candidates = session.get_candidates(lease)
            portfolio = session.get_portfolio(participant_idx=0)
            # ... agent logic ...
            session.submit_intents(lease, participant_idx=0, intents=[...])
            session.finalize(lease, participant_idx=0)
            session.complete_tick(lease)
    """

    def __init__(self, api: ServerAPIClient) -> None:
        self.api = api
        self.experiment_id: str | None = None
        self._lease_owner_id: str = str(uuid.uuid4())

    def create_experiment(
        self,
        slug: str,
        config_hash: str,
        config_json: dict,
        n_ticks: int,
    ):
        resp = self.api.create_or_get_experiment(
            slug=slug,
            config_hash=config_hash,
            config_json=config_json,
            n_ticks=n_ticks,
        )
        self.experiment_id = resp.experiment_id
        return resp

    def upsert_participant(
        self,
        model: str,
        rep: int = 0,
        starting_cash: float = 10000.0,
    ):
        return self.api.upsert_participant(
            self._require_experiment_id(),
            model=model,
            rep=rep,
            starting_cash=starting_cash,
        )

    def claim_tick(self, lease_sec: int = 600) -> TickLease:
        claim = self.api.claim_tick(
            self._require_experiment_id(),
            self._lease_owner_id,
            lease_sec=lease_sec,
        )
        if claim.no_tick_available:
            return TickLease(
                available=False,
                reason=claim.reason,
                retry_after_sec=claim.retry_after_sec,
            )
        return TickLease(
            available=True,
            tick_id=claim.tick_id,
            candidate_set_id=claim.candidate_set_id,
        )

    def get_candidates(self, lease: TickLease) -> CandidatesResponse:
        tick_ts = lease.tick_ts
        if tick_ts is None:
            raise ValueError("TickLease has no tick_id")
        return self.api.get_candidates(tick_ts, snapshot_id=lease.candidate_set_id)

    def get_portfolio(self, participant_idx: int) -> PortfolioResponse | None:
        return self.api.get_portfolio(self._require_experiment_id(), participant_idx)

    def get_progress(self):
        return self.api.get_progress(self._require_experiment_id())

    def submit_intents(
        self,
        lease: TickLease,
        participant_idx: int,
        intents: list[TradeIntentRequest],
        *,
        idempotency_key_fn: Callable[[str, int, str, int], str] | None = None,
    ) -> SubmissionResult:
        """Submit trade intents for a claimed tick.

        Builds idempotency keys internally by default. Pass
        ``idempotency_key_fn(experiment_id, participant_idx, tick_id, index)``
        to override (e.g. for distributed runners).
        """
        exp_id = self._require_experiment_id()
        tick_id = lease.tick_id
        if tick_id is None:
            raise ValueError("TickLease has no tick_id")

        key_fn = idempotency_key_fn or _default_idempotency_key
        keyed_intents = []
        for i, intent in enumerate(intents):
            keyed_intents.append(TradeIntentRequest(
                market_id=intent.market_id,
                action=intent.action,
                side=intent.side,
                shares=intent.shares,
                idempotency_key=key_fn(exp_id, participant_idx, tick_id, i),
            ))

        result = self.api.submit_trade_intents(
            experiment_id=exp_id,
            participant_idx=participant_idx,
            tick_id=tick_id,
            candidate_set_id=lease.candidate_set_id or "",
            intents=keyed_intents,
        )
        return SubmissionResult(
            accepted=result.accepted,
            rejected=result.rejected,
            fills=result.fills,
            rejections=result.rejections,
        )

    def finalize(
        self,
        lease: TickLease,
        participant_idx: int,
        status: str = "COMPLETED",
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        tick_id = lease.tick_id
        if tick_id is None:
            raise ValueError("TickLease has no tick_id")
        self.api.finalize_participant(
            self._require_experiment_id(),
            participant_idx,
            tick_id,
            status,
            error_code=error_code,
            error_detail=error_detail,
        )

    def complete_tick(self, lease: TickLease) -> None:
        tick_id = lease.tick_id
        if tick_id is None:
            raise ValueError("TickLease has no tick_id")
        self.api.complete_tick(self._require_experiment_id(), tick_id)

    def close(self) -> None:
        self.api.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _require_experiment_id(self) -> str:
        if self.experiment_id is None:
            raise RuntimeError("BenchmarkSession not initialized: call create_experiment() first")
        return self.experiment_id
