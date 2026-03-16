"""Experiment orchestration engine used by the CLI.

Orchestrator with no authoritative state:
- Registers experiment + participants with Core on startup.
- Claims ticks via lease. Core enforces single IN_PROGRESS.
- For each participant: persist plan -> submit intents -> finalize.
- Completion is Core-authoritative (n_ticks count in DB, not local).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ai_prophet_core.client import (
    APIClientError,
    APIConnectionError,
    APIServerError,
    APITimeoutError,
    ServerAPIClient,
    TradeIntentRequest,
)

from ai_prophet.trade.agent.reasoning_memory import build_memory_context
from ai_prophet.trade.core.config import ClientConfig
from ai_prophet.trade.core.tick_context import CandidateMarket, Position, TickContext
from ai_prophet.trade.memory import LocalReasoningStore
from ai_prophet.trade.trace import TraceSink

logger = logging.getLogger(__name__)

PARTICIPANT_TICK_BUDGET_SEC = 600
MAX_CONCURRENT_PARTICIPANTS = 4
TRANSIENT_API_RETRY_SEC = 15


def compute_config_hash(config: dict) -> str:
    """Deterministic hash of experiment config."""
    canonical = json.dumps(config, sort_keys=True, default=str)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()[:16]}"


def make_idempotency_key(
    experiment_id: str, participant_idx: int, tick_id: str, intent_index: int,
) -> str:
    """Deterministic key. Intent index is stable because intents are pre-sorted."""
    return f"{experiment_id}:{participant_idx}:{tick_id}:{intent_index}"


def prepare_intents(raw_intents: list[dict]) -> list[dict]:
    """Sort intents deterministically for stable idempotency keys."""
    return sorted(raw_intents, key=lambda i: (i.get("market_id", ""), i.get("side", "")))


def _bump_slug(slug: str) -> str:
    """Increment version suffix: baseline_v01 -> baseline_v02, foo -> foo_v2."""
    m = re.search(r"_v(\d+)$", slug)
    if m:
        n = int(m.group(1)) + 1
        return slug[:m.start()] + f"_v{n:02d}"
    return f"{slug}_v2"


def _is_transient_api_error(exc: Exception) -> bool:
    """Return True for retryable API failures."""
    return isinstance(exc, (APIServerError, APIConnectionError, APITimeoutError))


def _parse_tick_id(tick_id: str) -> datetime:
    """Parse wire-format ``tick_id`` into a timezone-aware datetime."""
    tick_ts = datetime.fromisoformat(tick_id)
    if tick_ts.tzinfo is None:
        tick_ts = tick_ts.replace(tzinfo=UTC)
    return tick_ts


class ExperimentRunner:
    """Stateless orchestrator for a benchmark experiment.

    The ``prophet trade`` CLI is the supported out-of-the-box interface for
    this trade benchmark package. Direct embedding is supported for advanced
    use cases, but non-CLI callers are expected to provide explicit pipeline
    wiring via ``build_pipeline``.
    """

    def __init__(
        self,
        api_url: str,
        api_key: str | None,
        experiment_slug: str,
        models: list[dict],
        config: dict | None = None,
        n_ticks: int = 96,
        starting_cash: float = 10000.0,
        trace_dir: Path | None = None,
        build_pipeline: Callable | None = None,
        publish_reasoning: bool = False,
        betting_engine: Any = None,
        client_config: ClientConfig | None = None,
        memory_dir: Path | None = None,
        memory_max_rows: int = 1000,
    ):
        """
        Args:
            api_url: Core API base URL.
            api_key: Optional Core API key used for authenticated requests.
            experiment_slug: Stable slug (restarts resume).
            models: List of {"model": "provider:name", "rep": 0} dicts.
            config: Experiment config dict (included in config_hash).
            n_ticks: Target number of completed ticks.
            starting_cash: Per-participant starting cash.
            trace_dir: Directory for local trace files.
            build_pipeline: Callable(participant_cfg) -> AgentPipeline. None = headless mode.
            publish_reasoning: If True, persist per-stage reasoning in plan_json.
            betting_engine: Optional BettingEngine for exchange betting integration.
            client_config: Explicit runtime config for defaults and prompt memory limits.
            memory_dir: Directory for local reasoning memory files.
            memory_max_rows: Max reasoning rows persisted per participant.
        """
        self.api = ServerAPIClient(base_url=api_url, api_key=api_key)
        self.client_config = client_config or ClientConfig.get()
        self.memory_config = self.client_config.memory
        self.slug = experiment_slug
        self.config = config or {}
        self.config_hash = compute_config_hash(self.config)
        self.models = models
        self.n_ticks = n_ticks
        self.starting_cash = starting_cash
        self.lease_owner_id = str(uuid.uuid4())
        self.build_pipeline = build_pipeline
        self.publish_reasoning = publish_reasoning
        self.betting_engine = betting_engine
        self.memory_dir = (memory_dir or Path("~/.pa_memory")).expanduser()
        self.memory_max_rows = memory_max_rows

        self.trace_sink: TraceSink | None = None
        if trace_dir:
            self.trace_sink = TraceSink(base_dir=trace_dir)
        self.local_memory_store: LocalReasoningStore | None = None

        self.experiment_id: str | None = None
        self.participants: dict[int, dict] = {}
        self._is_resumed: bool = False
        self._timed_out: set[tuple[int, str]] = set()
        self._timed_out_lock = threading.Lock()

    def init(self) -> None:
        """Register experiment and participants with Core."""
        # Resolve slug conflicts robustly: if a slug already exists with a
        # different config hash, keep bumping version suffix until available.
        for _ in range(50):
            try:
                resp = self.api.create_or_get_experiment(
                    slug=self.slug,
                    config_hash=self.config_hash,
                    config_json=self.config,
                    n_ticks=self.n_ticks,
                )
                break
            except APIClientError as e:
                msg = str(e)
                if "Client error 409" in msg and "different config_hash" in msg:
                    previous = self.slug
                    self.slug = _bump_slug(self.slug)
                    logger.warning(
                        "Slug conflict for %s (different config hash); retrying with %s",
                        previous,
                        self.slug,
                    )
                    continue
                raise
        else:
            raise RuntimeError("Unable to resolve experiment slug after repeated 409 conflicts")

        self.experiment_id = resp.experiment_id
        self._is_resumed = not resp.created
        self.local_memory_store = LocalReasoningStore(
            self.memory_dir,
            experiment_slug=self.slug,
            max_rows=self.memory_max_rows,
        )
        logger.info(f"Experiment {self.experiment_id} (slug={self.slug}, created={resp.created})")
        logger.info("Memory store initialized at %s/%s", self.memory_dir, self.slug)

        for m in self.models:
            model_name = m["model"]
            rep = m.get("rep", 0)
            p = self.api.upsert_participant(
                self.experiment_id, model=model_name, rep=rep,
                starting_cash=self.starting_cash,
            )
            self.participants[p.participant_idx] = {
                "model": model_name, "rep": rep, "participant_idx": p.participant_idx,
            }
            logger.info(f"Participant {p.participant_idx}: {model_name} rep={rep}")

    def run(self) -> None:
        """Main tick loop. Exits when Core says experiment_completed."""
        self.init()
        exp_id = self._require_experiment_id()

        try:
            while True:
                try:
                    claim = self.api.claim_tick(
                        exp_id, self.lease_owner_id, lease_sec=600,
                    )
                except Exception as e:
                    if _is_transient_api_error(e):
                        logger.warning(
                            "claim_tick transient error (%s); retry in %ss",
                            e,
                            TRANSIENT_API_RETRY_SEC,
                        )
                        time.sleep(TRANSIENT_API_RETRY_SEC)
                        continue
                    raise

                if claim.no_tick_available:
                    if claim.reason == "experiment_completed":
                        logger.info("Experiment completed.")
                        break
                    retry = claim.retry_after_sec or 15
                    logger.info(f"No tick available (reason={claim.reason}), retry in {retry}s")
                    time.sleep(retry)
                    continue

                tick_id = claim.tick_id
                snapshot_id = claim.snapshot_id
                if tick_id is None or snapshot_id is None:
                    raise RuntimeError("claim_tick returned no tick_id/snapshot_id for active lease")
                logger.info(f"Claimed tick {tick_id} (snapshot={snapshot_id})")

                try:
                    self._process_tick(tick_id, snapshot_id)
                except Exception as e:
                    if _is_transient_api_error(e):
                        logger.warning(
                            "Tick %s processing hit transient API error (%s); "
                            "will retry after lease/backoff",
                            tick_id,
                            e,
                        )
                        continue
                    raise

                try:
                    self.api.complete_tick(exp_id, tick_id)
                    logger.info(f"Tick {tick_id} completed")
                except Exception as e:
                    logger.warning(f"Tick complete returned non-200: {e}")
        finally:
            try:
                self.api.close()
            except Exception:
                pass
            if self.trace_sink:
                self.trace_sink.close()

    def _process_tick(self, tick_id: str, snapshot_id: str) -> None:
        """Process all participants for one tick with budget enforcement.

        Fetches candidates once for the whole tick, then fans out to
        participants. Caps concurrency to avoid overwhelming the Core API
        when running many participants.
        """
        tick_ts = _parse_tick_id(tick_id)

        # Fetch candidates once (same for all participants in this tick).
        candidates_resp = self.api.get_candidates(tick_ts, snapshot_id=snapshot_id)
        candidate_markets = tuple(
            CandidateMarket.from_server_response(m.model_dump())
            for m in candidates_resp.markets
        )
        data_asof = candidates_resp.data_asof_ts
        if data_asof.tzinfo is None:
            data_asof = data_asof.replace(tzinfo=UTC)

        # Shared context passed to each participant.
        tick_shared = {
            "tick_ts": tick_ts,
            "candidate_markets": candidate_markets,
            "data_asof": data_asof,
            "candidate_set_id": candidates_resp.candidate_set_id,
        }

        workers = min(MAX_CONCURRENT_PARTICIPANTS, len(self.participants))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(self._process_participant, idx, tick_id, snapshot_id, tick_shared): idx
                for idx in self.participants
            }
            done, not_done = wait(futures.keys(), timeout=PARTICIPANT_TICK_BUDGET_SEC)

            for future in not_done:
                idx = futures[future]
                future.cancel()
                self._mark_timed_out(idx, tick_id)
                model = self.participants[idx].get("model", "unknown")
                self._finalize(idx, tick_id, "TIMEOUT")
                logger.warning(
                    f"Participant {idx} ({model}) timed out on tick {tick_id} "
                    f"(budget={PARTICIPANT_TICK_BUDGET_SEC}s exceeded)"
                )

            for future in done:
                idx = futures[future]
                exc = future.exception()
                if exc:
                    self._finalize(
                        idx, tick_id, "FAILED",
                        error_code="PIPELINE_ERROR",
                        error_detail=str(exc)[:1024],
                    )
                    logger.error(f"Participant {idx} failed on tick {tick_id}: {exc}")
        self._clear_timed_out_tick(tick_id)

    def _process_participant(
        self, idx: int, tick_id: str, snapshot_id: str, tick_shared: dict,
    ) -> None:
        """Process a single participant for one tick.

        1. Generate plan via pipeline (or skip if no pipeline).
        2. PUT plan to Core. Core returns existing on restart.
        3. Submit intents from the authoritative plan.
        4. Finalize COMPLETED.
        """
        if not self.build_pipeline:
            self._finalize(idx, tick_id, "SKIPPED")
            return

        if self._is_timed_out(idx, tick_id):
            logger.info("Participant %s already timed out for tick %s; skipping", idx, tick_id)
            return

        plan_json, generated_reasoning = self._generate_plan(idx, tick_id, snapshot_id, tick_shared)
        if self._is_timed_out(idx, tick_id):
            logger.info("Participant %s timed out after plan generation for tick %s", idx, tick_id)
            return

        # PUT plan to Core. If already persisted (restart recovery),
        # Core returns the old one and we use that.
        exp_id = self._require_experiment_id()
        plan_resp = self.api.put_plan(
            exp_id, idx, tick_id, snapshot_id, plan_json=plan_json,
        )
        authoritative_plan = plan_resp.plan_json or plan_json
        already_persisted = plan_resp.already_persisted
        if already_persisted:
            logger.info(f"Participant {idx}: reusing persisted plan for tick {tick_id}")

        if self.local_memory_store:
            reasoning_for_memory = (
                (authoritative_plan or {}).get("reasoning")
                if already_persisted
                else generated_reasoning
            )
            if reasoning_for_memory:
                try:
                    self.local_memory_store.append_reasoning(
                        participant_idx=idx,
                        tick_id=tick_id,
                        reasoning=reasoning_for_memory,
                    )
                except Exception as e:
                    logger.warning(f"Participant {idx}: local memory append failed (non-fatal): {e}")

        intents = authoritative_plan.get("intents", [])
        if intents:
            if self._is_timed_out(idx, tick_id):
                logger.info("Participant %s timed out before intent submit for tick %s", idx, tick_id)
                return
            self._submit_intents(idx, tick_id, snapshot_id, intents)

        self._finalize(idx, tick_id, "COMPLETED")

        if self.trace_sink:
            self.trace_sink.end_tick(self.slug, idx, tick_id)

    def _generate_plan(
        self, idx: int, tick_id: str, snapshot_id: str, tick_shared: dict,
    ) -> tuple[dict, dict[str, Any] | None]:
        """Run the agent pipeline and return a plan dict.

        Uses pre-fetched candidates from tick_shared to avoid redundant
        API calls across participants.
        """
        tick_ts: datetime = tick_shared["tick_ts"]
        candidate_markets: tuple[CandidateMarket, ...] = tick_shared["candidate_markets"]
        market_ids = [m.market_id for m in candidate_markets]
        exp_id = self._require_experiment_id()

        cfg = self.participants[idx]

        build_pipeline = self.build_pipeline
        if build_pipeline is None:
            raise RuntimeError("build_pipeline is required to generate plans")
        pipeline = build_pipeline(cfg)

        # Fetch real portfolio state from server. Falls back to starting_cash
        # for the very first tick or if the server doesn't support the endpoint.
        cash = Decimal(str(self.starting_cash))
        equity = Decimal(str(self.starting_cash))
        total_pnl = Decimal("0")
        positions: tuple[Position, ...] = ()
        total_fills = 0

        portfolio = self.api.get_portfolio(exp_id, idx)
        if portfolio is not None:
            cash = Decimal(portfolio.cash)
            equity = Decimal(portfolio.equity)
            total_pnl = Decimal(portfolio.total_pnl)
            total_fills = portfolio.total_fills
            positions = tuple(
                Position(
                    market_id=p.market_id,
                    side=p.side,
                    shares=Decimal(p.shares),
                    avg_entry_price=Decimal(p.avg_entry_price),
                    current_price=Decimal(p.current_price),
                    unrealized_pnl=Decimal(p.unrealized_pnl),
                    realized_pnl=Decimal(p.realized_pnl),
                    updated_at=p.updated_at or tick_ts,
                )
                for p in portfolio.positions
            )
            logger.info(f"Participant {idx}: portfolio loaded (cash={cash}, equity={equity}, positions={len(positions)})")
        elif self._is_resumed:
            logger.warning(
                f"Participant {idx}: resumed experiment but portfolio unavailable, "
                f"using starting_cash=${self.starting_cash}"
            )

        # Build memory from local JSONL history (always-on, no API reads).
        memory_summary = ""
        memory_by_market: dict[str, str] = {}
        reasoning_entries = []
        if self.local_memory_store:
            reasoning_entries = self.local_memory_store.read_recent_reasoning(
                participant_idx=idx,
                limit=self.memory_config.recent_ticks_limit,
            )
        if reasoning_entries:
            try:
                memory_ctx = build_memory_context(
                    entries=reasoning_entries,
                    current_market_ids=market_ids,
                    market_history_limit=self.memory_config.market_history_limit,
                )
                memory_summary = memory_ctx.summary
                memory_by_market = memory_ctx.by_market
                logger.info(f"Participant {idx} memory: {len(reasoning_entries)} entries -> {len(memory_by_market)} markets, {len(memory_summary)} chars")
                if memory_summary:
                    logger.info(f"Participant {idx} memory summary:\n{memory_summary}")
            except Exception as e:
                logger.warning(f"Participant {idx}: memory build failed (non-fatal): {e}")
        else:
            logger.info(f"Participant {idx} memory: empty (no local history)")

        tick_ctx = TickContext(
            run_id=f"{exp_id}:{idx}",
            tick_ts=tick_ts,
            data_asof_ts=tick_shared["data_asof"],
            candidate_set_id=tick_shared["candidate_set_id"],
            submission_deadline=tick_ts + timedelta(seconds=PARTICIPANT_TICK_BUDGET_SEC),
            server_now=datetime.now(UTC),
            candidates=tuple(candidate_markets),
            cash=cash,
            equity=equity,
            total_pnl=total_pnl,
            positions=positions,
            total_fills=total_fills,
            memory_summary=memory_summary,
            memory_by_market=memory_by_market,
        )

        try:
            result = pipeline.execute(
                tick_ctx,
                f"{exp_id}:{idx}",
                publish_reasoning=True,
            )
        finally:
            # Pipeline holds HTTP clients; close them deterministically.
            try:
                pipeline.close()
            except Exception:
                pass
        sorted_intents = prepare_intents(result.intents)

        plan_json: dict = {
            "intents": sorted_intents,
            "tick_id": tick_id,
            "snapshot_id": snapshot_id,
        }
        if self.publish_reasoning and result.reasoning:
            plan_json["reasoning"] = result.reasoning

        if self.trace_sink:
            self.trace_sink.write(
                self.slug, exp_id, idx, tick_id,
                stage="plan", event_type="plan_generated",
                payload=plan_json,
            )

        return plan_json, result.reasoning

    def _submit_intents(
        self, idx: int, tick_id: str, snapshot_id: str, raw_intents: list[dict],
    ) -> None:
        """Build idempotency keys and submit intents to Core."""
        exp_id = self._require_experiment_id()
        intent_requests = []
        for i, intent in enumerate(raw_intents):
            intent_requests.append(TradeIntentRequest(
                market_id=intent["market_id"],
                action=intent["action"],
                side=intent["side"],
                shares=str(intent.get("shares", "0")),
                idempotency_key=make_idempotency_key(
                    exp_id, idx, tick_id, i,
                ),
            ))

        if intent_requests:
            result = self.api.submit_trade_intents(
                experiment_id=exp_id,
                participant_idx=idx,
                tick_id=tick_id,
                candidate_set_id=snapshot_id,
                intents=intent_requests,
            )
            logger.info(
                f"Participant {idx}: {result.accepted} accepted, {result.rejected} rejected"
            )
            if result.rejected and result.rejections:
                for rejection in result.rejections:
                    logger.warning(
                        "Participant %s trade intent rejected: intent_id=%s reason=%s",
                        idx,
                        rejection.intent_id,
                        rejection.reason,
                    )
            elif result.rejected:
                logger.warning(
                    "Participant %s reported %s rejected intents without rejection details",
                    idx,
                    result.rejected,
                )

    def _finalize(self, idx: int, tick_id: str, status: str, **kwargs) -> None:
        if status != "TIMEOUT" and self._is_timed_out(idx, tick_id):
            logger.info(
                "Skipping late finalize for participant %s tick %s (status=%s after TIMEOUT)",
                idx,
                tick_id,
                status,
            )
            return
        try:
            exp_id = self._require_experiment_id()
            self.api.finalize_participant(
                exp_id, idx, tick_id, status, **kwargs,
            )
        except Exception as e:
            logger.error(f"Failed to finalize participant {idx} tick {tick_id}: {e}")

    def _mark_timed_out(self, idx: int, tick_id: str) -> None:
        with self._timed_out_lock:
            self._timed_out.add((idx, tick_id))

    def _is_timed_out(self, idx: int, tick_id: str) -> bool:
        with self._timed_out_lock:
            return (idx, tick_id) in self._timed_out

    def _clear_timed_out_tick(self, tick_id: str) -> None:
        with self._timed_out_lock:
            self._timed_out = {entry for entry in self._timed_out if entry[1] != tick_id}

    def _require_experiment_id(self) -> str:
        if self.experiment_id is None:
            raise RuntimeError("ExperimentRunner is not initialized")
        return self.experiment_id
