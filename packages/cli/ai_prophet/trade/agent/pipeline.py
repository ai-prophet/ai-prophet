"""Agent pipeline orchestrator.

Single-step workflow that mirrors ``UnifiedProphetArena AgentPrompts``:

    For each candidate market:
        1. AgentPrompts → Claude Opus 4.7 (+ native web_search) → {p_yes, rationale}
        2. The ported anri-trading betting strategy (DefaultBettingStrategy /
           RebalancingStrategy) decides side + shares + price via
           ``strategy.evaluate(...)`` and returns a ``BetSignal``.
        3. ``BetSignal`` is converted to a trade intent for the runner.

The PipelineResult interface (``intents``, ``forecasts``, ``reasoning``) is
preserved so the runner does not need to change.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import anthropic
from ai_prophet_core.betting import (
    DefaultBettingStrategy,
    PortfolioSnapshot,
    RebalancingStrategy,
)
from ai_prophet_core.betting.strategy import BetSignal, BettingStrategy
from ai_prophet_core.client import ServerAPIClient

from ai_prophet.search import SearchClient
from ai_prophet.trade.core import EventStore, TickContext
from ai_prophet.trade.core.config import ClientConfig
from ai_prophet.trade.llm import LLMClient
from ai_prophet.trade.llm.base import vprint

from .agent_prompts import AgentPrompts, parse_response, yes_probability

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result types — interface preserved for the runner
# ---------------------------------------------------------------------------

@dataclass
class PipelineResult:
    """Output of a pipeline execution.

    ``forecasts`` maps market_id → ``{p_yes, rationale}`` so callers can
    trigger side effects (e.g. external betting) without re-running.
    """
    intents: list[dict[str, Any]]
    forecasts: dict[str, dict[str, Any]] | None = None
    reasoning: dict[str, Any] | None = None


class PipelineError(Exception):
    """Pipeline execution error with any completed forecast output."""

    def __init__(
        self,
        message: str,
        *,
        stage_name: str | None = None,
        forecasts: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.stage_name = stage_name
        self.forecasts = forecasts


# ---------------------------------------------------------------------------
# Tuning knobs (env-overridable)
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("WORKER_MODEL", "claude-opus-4-7")
DEFAULT_MAX_TOKENS = int(os.environ.get("WORKER_MAX_TOKENS", "2000"))
DEFAULT_WEB_SEARCH_MAX_USES = int(os.environ.get("WORKER_WEB_SEARCH_MAX_USES", "2"))
DEFAULT_MAX_MARKETS = int(os.environ.get("WORKER_MAX_MARKETS", "30"))
DEFAULT_STRATEGY = os.environ.get("WORKER_STRATEGY", "rebalancing")
# Pre-filter: skip markets where YES ask is out of this range before LLM call.
PRICE_PREFILTER_MIN = float(os.environ.get("WORKER_PRICE_MIN", "0.10"))
PRICE_PREFILTER_MAX = float(os.environ.get("WORKER_PRICE_MAX", "0.90"))

# Substring match (case-insensitive) against market metadata fields. Markets
# matching any of these get dropped before the LLM call — mirrors
# anri-trading's EXCLUDED_MARKET_CATEGORIES + _contains_excluded_market_marker.
EXCLUDED_MARKERS = ("mentions",)


def _build_strategy(name: str) -> BettingStrategy:
    name = (name or "default").lower()
    if name == "rebalancing":
        return RebalancingStrategy()
    return DefaultBettingStrategy()


def _is_excluded_market(market) -> bool:
    """Substring match against question/short_label/topic/family/market_id."""
    fields = (
        market.question,
        getattr(market, "short_label", None),
        getattr(market, "topic", None),
        getattr(market, "family", None),
        market.market_id,
    )
    for field in fields:
        if not field:
            continue
        lowered = str(field).lower()
        for marker in EXCLUDED_MARKERS:
            if marker in lowered:
                return True
    return False


# ---------------------------------------------------------------------------
# AgentPipeline
# ---------------------------------------------------------------------------

class AgentPipeline:
    """Single-step trading agent.

    Per candidate market: AgentPrompts → Opus 4.7 (web_search) →
    p_yes → ported betting strategy → trade intent.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        event_store: EventStore | None,
        api_client: ServerAPIClient,
        config: dict[str, Any] | None = None,
        client_config: ClientConfig | None = None,
    ):
        # llm_client is preserved for interface compat but bypassed: we call
        # anthropic.Anthropic directly so we can attach the native
        # web_search server tool, which the LLMClient wrapper doesn't expose.
        self.llm_client = llm_client
        self.event_store = event_store
        self.api_client = api_client
        self.config = config or {}
        self.search_client: SearchClient | None = None  # unused; kept for close()

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY is required for AgentPipeline (Opus 4.7 + web_search)"
            )
        self._anthropic = anthropic.Anthropic(api_key=api_key)

        self.model = self.config.get("model", DEFAULT_MODEL)
        self.max_tokens = int(self.config.get("max_tokens", DEFAULT_MAX_TOKENS))
        self.web_search_max_uses = int(
            self.config.get("web_search_max_uses", DEFAULT_WEB_SEARCH_MAX_USES)
        )
        self.max_markets = int(self.config.get("max_markets", DEFAULT_MAX_MARKETS))
        self.strategy: BettingStrategy = _build_strategy(
            self.config.get("strategy_name", DEFAULT_STRATEGY)
        )

        # Optional callback fired with (market_id, p_yes, ...) after each forecast.
        self.on_forecast: Callable[..., None] | None = self.config.get("on_forecast")

        logger.info(
            "AgentPipeline initialized: model=%s strategy=%s max_markets=%d "
            "web_search_max_uses=%d",
            self.model, self.strategy.name, self.max_markets, self.web_search_max_uses,
        )

    # ------------------------------------------------------------------
    # Public API — preserves the contract the runner depends on
    # ------------------------------------------------------------------

    def execute(
        self,
        tick_ctx: TickContext,
        run_id: str,
        on_stage_start: Callable[[str, int, int], None] | None = None,
        publish_reasoning: bool = False,
    ) -> PipelineResult:
        if not tick_ctx.candidates:
            raise PipelineError("TickContext must be created with candidates already populated")

        logger.info(
            "Pipeline tick %s: %d candidates, cash=%s, %d positions",
            tick_ctx.tick_ts, len(tick_ctx.candidates),
            tick_ctx.cash, len(tick_ctx.positions),
        )
        if on_stage_start:
            on_stage_start("agent", 1, 1)

        candidates = self._select_markets(tick_ctx)
        logger.info(
            "Analyzing %d/%d markets after pre-filter (price ∈ [%.2f, %.2f]) and cap (%d)",
            len(candidates), len(tick_ctx.candidates),
            PRICE_PREFILTER_MIN, PRICE_PREFILTER_MAX, self.max_markets,
        )

        intents: list[dict[str, Any]] = []
        forecasts: dict[str, dict[str, Any]] = {}
        signals_log: dict[str, dict[str, Any]] = {}

        for market in candidates:
            try:
                parsed = self._agent_forecast(market)
            except Exception as exc:
                logger.warning("Forecast failed for %s: %s", market.market_id, exc)
                continue

            p_yes = yes_probability(parsed)
            if p_yes is None:
                logger.warning("No YES probability in response for %s", market.market_id)
                continue

            forecasts[market.market_id] = {
                "p_yes": float(p_yes),
                "rationale": parsed.get("rationale", ""),
            }

            if self.on_forecast:
                try:
                    self.on_forecast(
                        market_id=market.market_id,
                        p_yes=float(p_yes),
                        yes_ask=market.yes_ask,
                        no_ask=market.no_ask,
                        question=market.question,
                    )
                except Exception:
                    logger.exception("on_forecast callback failed for %s", market.market_id)

            signal = self._evaluate(market, p_yes, tick_ctx)
            if signal is None:
                signals_log[market.market_id] = {
                    "skip_reason": self.strategy.last_skip_reason or "no signal",
                }
                continue

            intent = self._signal_to_intent(signal, market, tick_ctx, parsed.get("rationale", ""))
            if intent is None:
                continue
            intents.append(intent)
            signals_log[market.market_id] = {
                "side": signal.side, "shares": float(signal.shares),
                "price": float(signal.price), "cost": float(signal.cost),
            }

        logger.info(
            "Pipeline complete: %d intents from %d forecasts (%d skipped)",
            len(intents), len(forecasts), len(forecasts) - len(intents),
        )

        reasoning = None
        if publish_reasoning:
            reasoning = self._build_reasoning(tick_ctx, forecasts, signals_log)

        if self.event_store:
            try:
                self.event_store.write_tick_complete(tick_ts=tick_ctx.tick_ts)
            except Exception:
                logger.exception("event_store.write_tick_complete failed")

        return PipelineResult(
            intents=intents,
            forecasts=forecasts or None,
            reasoning=reasoning,
        )

    def close(self) -> None:
        for client_attr in ("api_client", "llm_client", "search_client"):
            obj = getattr(self, client_attr, None)
            if obj is None:
                continue
            try:
                close = getattr(obj, "close", None)
                if callable(close):
                    close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_markets(self, tick_ctx: TickContext) -> list:
        """Pre-filter by price range + excluded markers, then cap at max_markets.

        Sorts by 24h volume descending so we spend LLM budget on the most
        active markets first.
        """
        eligible = [
            m for m in tick_ctx.candidates
            if PRICE_PREFILTER_MIN <= m.yes_ask <= PRICE_PREFILTER_MAX
            and PRICE_PREFILTER_MIN <= m.no_ask <= PRICE_PREFILTER_MAX
            and not _is_excluded_market(m)
        ]
        eligible.sort(key=lambda m: (-(m.volume_24h or 0), m.market_id))
        return eligible[: self.max_markets]

    def _agent_forecast(self, market) -> dict[str, Any]:
        """One AgentPrompts → Opus 4.7 + web_search call. Returns parsed JSON."""
        market_names = ["YES", "NO"]
        system_prompt = AgentPrompts.create_task_prompt(
            event_title=market.question,
            market_names=market_names,
            rules=market.description,
            avoid_market_search=False,
        )
        user_prompt = AgentPrompts.create_user_prompt(
            market_stats={
                "YES": round(market.yes_ask, 4),
                "NO": round(market.no_ask, 4),
            },
        )

        response = self._anthropic.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": self.web_search_max_uses,
                }
            ],
        )

        text = "\n".join(
            b.text for b in response.content if getattr(b, "type", None) == "text"
        ).strip()
        return parse_response(text, market_names)

    def _evaluate(self, market, p_yes: float, tick_ctx: TickContext) -> BetSignal | None:
        """Set portfolio snapshot for this market on the strategy and evaluate."""
        snapshot = self._portfolio_snapshot(market, tick_ctx)
        self.strategy._portfolio = snapshot
        try:
            return self.strategy.evaluate(
                market_id=market.market_id,
                p_yes=float(p_yes),
                yes_ask=float(market.yes_ask),
                no_ask=float(market.no_ask),
            )
        finally:
            self.strategy._portfolio = None

    def _portfolio_snapshot(self, market, tick_ctx: TickContext) -> PortfolioSnapshot:
        """Build the PortfolioSnapshot the strategy expects for this market.

        The strategy interprets ``market_position_shares`` as CONTRACTS
        (100 contracts = $1 max payout). Position.shares from the server is
        already in contract units.
        """
        pos = tick_ctx.get_position(market.market_id)
        if pos is None:
            return PortfolioSnapshot()
        side_lower = (pos.side or "").lower()
        if side_lower not in {"yes", "no"}:
            return PortfolioSnapshot()
        return PortfolioSnapshot(
            market_position_side=side_lower,
            market_position_shares=Decimal(str(pos.shares)),
        )

    def _signal_to_intent(
        self,
        signal: BetSignal,
        market,
        tick_ctx: TickContext,
        rationale: str,
    ) -> dict[str, Any] | None:
        """Convert a BetSignal (fractional shares) into the dict shape the
        runner sends to the PA server via TradeIntentRequest.
        """
        side_upper = (signal.side or "").upper()
        if side_upper not in {"YES", "NO"}:
            logger.warning("Unknown side %r from strategy for %s", signal.side, market.market_id)
            return None
        # Strategy uses fractional shares (0-1 scale). Server's TradeIntentRequest
        # accepts a string — runner passes whatever we put here.
        shares_str = f"{float(signal.shares):.4f}"
        return {
            "run_id": tick_ctx.run_id,
            "tick_ts": tick_ctx.tick_ts,
            "market_id": market.market_id,
            "question": market.question,
            "action": "BUY",
            "side": side_upper,
            "shares": shares_str,
            "rationale": rationale or "",
        }

    def _build_reasoning(
        self,
        tick_ctx: TickContext,
        forecasts: dict[str, dict[str, Any]],
        signals_log: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        questions = {m.market_id: m.question for m in tick_ctx.candidates}
        return {
            "model": self.model,
            "strategy": self.strategy.name,
            "candidates_analyzed": list(forecasts.keys()),
            "forecasts": {
                mid: {
                    "question": questions.get(mid),
                    "p_yes": f.get("p_yes"),
                    "rationale": f.get("rationale"),
                }
                for mid, f in forecasts.items()
            },
            "signals": signals_log,
        }


__all__ = ["AgentPipeline", "PipelineResult", "PipelineError"]
