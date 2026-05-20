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
# Max allowed spread (yes_ask + no_ask). Default disables the cap so the
# strategy will trade wide-spread Kalshi markets too; the within-spread +
# MIN_EDGE guards still prevent zero-edge entries.
MAX_SPREAD = float(os.environ.get("WORKER_MAX_SPREAD", "inf"))

# Minimum price movement (fractional units) since our last fill on a ticker
# before we will trade it again. Throttles tick-to-tick churn on LLM noise.
# Anri-trading's BettingEngine enforces this in process_forecasts; we
# replicate it here because this pipeline bypasses BettingEngine and ships
# intents straight to the PA server. Set to 0 to disable.
MIN_PRICE_MOVEMENT = float(os.environ.get("WORKER_MIN_PRICE_MOVEMENT", "0.10"))

# Strategy works internally in *fractional* shares (0..~1); the PA server's
# TradeIntentRequest.shares and PositionData.shares are in *contracts*
# (1 contract = $1 max payout) — same as anri-trading's BettingEngine
# (engine.py:408 multiplies by 100 before submitting to Kalshi) and main's
# action stage (shares = size_usd / price). Convert at the boundary.
SHARES_SCALE = float(os.environ.get("WORKER_SHARES_SCALE", "100"))

# Substring match (case-insensitive) against market metadata fields. Markets
# matching any of these get dropped before the LLM call — mirrors
# anri-trading's EXCLUDED_MARKET_CATEGORIES + _contains_excluded_market_marker.
EXCLUDED_MARKERS = ("mentions",)


def _build_strategy(name: str) -> BettingStrategy:
    name = (name or "default").lower()
    if name == "rebalancing":
        return RebalancingStrategy(max_spread=MAX_SPREAD)
    return DefaultBettingStrategy(max_spread=MAX_SPREAD)


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

        # Persistent across ticks: market_id → (last fill price, side as
        # "YES"/"NO"). Populated whenever the pipeline emits an intent so
        # subsequent ticks can throttle re-entry until the market has moved
        # ≥ MIN_PRICE_MOVEMENT. Falls back to PA-reported avg_entry_price
        # for markets we haven't touched in this process.
        self._last_fill_by_market: dict[str, tuple[float, str]] = {}

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

        # Mutable cash budget that shrinks as we accumulate intents in this
        # tick. The strategy reads it via the per-market PortfolioSnapshot;
        # without this, every market in the loop saw the full starting cash
        # and the strategy gate could never fire across multiple buys.
        remaining_cash = float(tick_ctx.cash)

        for market in candidates:
            # 10¢ movement gate: skip BEFORE the (expensive) LLM call when the
            # market hasn't moved enough since our last fill.
            movement_skip = self._movement_skip_reason(market)
            if movement_skip is not None:
                logger.info(
                    "Skip %s pre-LLM: %s (yes_ask=%.3f, no_ask=%.3f)",
                    market.market_id, movement_skip,
                    float(market.yes_ask), float(market.no_ask),
                )
                signals_log[market.market_id] = {"skip_reason": movement_skip}
                continue

            try:
                parsed = self._agent_forecast(market, tick_ctx)
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

            signal = self._evaluate(market, p_yes, tick_ctx, cash=remaining_cash)
            if signal is None:
                reason = self.strategy.last_skip_reason or "no signal"
                logger.info(
                    "Skip %s: %s (p_yes=%.3f, yes_ask=%.3f, no_ask=%.3f, cash=%.2f)",
                    market.market_id, reason, float(p_yes),
                    float(market.yes_ask), float(market.no_ask), remaining_cash,
                )
                signals_log[market.market_id] = {"skip_reason": reason}
                continue

            new_intents = self._signal_to_intents(
                signal, market, tick_ctx, parsed.get("rationale", "")
            )
            if not new_intents:
                continue
            intents.extend(new_intents)

            # Decrement remaining_cash for subsequent markets. Sell proceeds
            # are credited back. Approximates the NO bid as 1 - yes_ask (and
            # vice versa), the same approximation BettingEngine uses for sell
            # legs (engine.py: sell_price = 1 - opposite_ask).
            sell_portion = float((signal.metadata or {}).get("sell_portion") or 0.0)
            buy_portion = max(0.0, float(signal.shares) - sell_portion)
            buy_cost_dollars = buy_portion * SHARES_SCALE * float(signal.price)
            sell_price_approx = max(0.0, 1.0 - float(signal.price))
            sell_proceeds_dollars = sell_portion * SHARES_SCALE * sell_price_approx
            remaining_cash += sell_proceeds_dollars - buy_cost_dollars

            # Record the fill so the movement gate throttles re-entry next
            # tick. Mirrors anri-trading's "last fill price + side" tracking.
            self._last_fill_by_market[market.market_id] = (
                float(signal.price),
                signal.side.upper(),
            )

            signals_log[market.market_id] = {
                "side": signal.side,
                "shares": float(signal.shares),
                "price": float(signal.price),
                "cost": float(signal.cost),
                "remaining_cash": round(remaining_cash, 2),
                "intents": [
                    {"action": i["action"], "side": i["side"], "shares": i["shares"]}
                    for i in new_intents
                ],
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

    def _agent_forecast(self, market, tick_ctx: TickContext) -> dict[str, Any]:
        """One AgentPrompts → Opus 4.7 + web_search call. Returns parsed JSON.

        Feeds the LLM richer context than just question+prices: resolution
        time, 24h volume, source metadata, current portfolio state and any
        existing position in this market.
        """
        market_names = ["YES", "NO"]
        system_prompt = AgentPrompts.create_task_prompt(
            event_title=market.question,
            market_names=market_names,
            rules=market.description,
            avoid_market_search=False,
        )
        user_prompt = (
            self._build_event_context(market, tick_ctx)
            + "\n\n"
            + AgentPrompts.create_user_prompt(
                market_stats={
                    "YES": round(market.yes_ask, 4),
                    "NO": round(market.no_ask, 4),
                },
            )
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

    def _build_event_context(self, market, tick_ctx: TickContext) -> str:
        """Per-market context block prefixed to AgentPrompts.create_user_prompt."""
        parts: list[str] = ["MARKET CONTEXT:"]
        parts.append(f"  Market ID: {market.market_id}")
        if market.question:
            parts.append(f"  Question: {market.question}")
        if market.resolution_time is not None:
            parts.append(f"  Resolves at (UTC): {market.resolution_time}")
        if (market.volume_24h or 0) > 0:
            parts.append(f"  24h volume: {market.volume_24h:,.0f}")
        for attr, label in (
            ("source", "Source"),
            ("source_url", "Source URL"),
            ("topic", "Topic"),
            ("family", "Family"),
            ("short_label", "Short label"),
        ):
            value = getattr(market, attr, None)
            if value:
                parts.append(f"  {label}: {value}")
        if market.description:
            parts.append(f"\nDescription:\n{market.description}")

        # Portfolio context — what we already hold and how much cash is free.
        parts.append("\nPORTFOLIO STATE:")
        try:
            parts.append(f"  Cash available: ${float(tick_ctx.cash):,.2f}")
        except Exception:
            pass
        try:
            parts.append(f"  Equity: ${float(tick_ctx.equity):,.2f}")
        except Exception:
            pass
        pos = tick_ctx.get_position(market.market_id)
        if pos is not None:
            parts.append(
                f"  Existing position in this market: {pos.side} "
                f"{pos.shares} shares @ avg ${pos.avg_entry_price} "
                f"(current ${pos.current_price}; "
                f"unrealized PnL ${pos.unrealized_pnl})"
            )
        else:
            parts.append("  No existing position in this market.")
        return "\n".join(parts)

    def _evaluate(
        self,
        market,
        p_yes: float,
        tick_ctx: TickContext,
        cash: float | None = None,
    ) -> BetSignal | None:
        """Set portfolio snapshot for this market on the strategy and evaluate.

        ``cash`` overrides ``tick_ctx.cash`` so the caller can pass a budget
        that's been decremented by intents emitted earlier in the same tick.
        """
        snapshot = self._portfolio_snapshot(market, tick_ctx, cash=cash)
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

    def _last_known_fill(self, market) -> tuple[float, str] | None:
        """Return (fill_price, side) for ``market`` if we have any anchor.

        Prefers the in-memory cache populated whenever we emit an intent
        (gives last-fill granularity within this process). Falls back to
        the PA-reported avg_entry_price of any open position so the gate
        still applies across a process restart.
        """
        cached = self._last_fill_by_market.get(market.market_id)
        if cached is not None:
            return cached
        pos = getattr(market, "existing_position", None)
        if pos is None or not pos.side:
            return None
        side = pos.side.upper()
        if side not in {"YES", "NO"}:
            return None
        try:
            return float(pos.avg_entry_price), side
        except (TypeError, ValueError):
            return None

    def _movement_skip_reason(self, market) -> str | None:
        """Return a skip reason if ``market`` hasn't moved enough since last fill.

        Mirrors BettingEngine.process_forecasts' 10¢ gate: re-derive the
        yes/no fill prices from (fill_price, side) and require either
        yes_ask or no_ask to have moved ≥ MIN_PRICE_MOVEMENT.
        """
        if MIN_PRICE_MOVEMENT <= 0:
            return None
        last = self._last_known_fill(market)
        if last is None:
            return None
        fill_price, side = last
        fill_yes = fill_price if side == "YES" else 1.0 - fill_price
        fill_no = 1.0 - fill_yes
        max_deviation = max(
            abs(float(market.yes_ask) - fill_yes),
            abs(float(market.no_ask) - fill_no),
        )
        if max_deviation < MIN_PRICE_MOVEMENT:
            return (
                f"Market unchanged: {max_deviation*100:.1f}¢ since last fill "
                f"(need {MIN_PRICE_MOVEMENT*100:.0f}¢)"
            )
        return None

    def _portfolio_snapshot(
        self,
        market,
        tick_ctx: TickContext,
        cash: float | None = None,
    ) -> PortfolioSnapshot:
        """Build the PortfolioSnapshot the strategy expects for this market.

        The strategy interprets ``market_position_shares`` as CONTRACTS
        (100 contracts = $1 max payout). Position.shares from the server is
        already in contract units.

        Cash/equity/total_pnl MUST be threaded through — RebalancingStrategy
        gates the BUY portion on ``port.cash`` (strategy.py:344), so an empty
        snapshot (default cash=0) makes every buy silently zero out and the
        pipeline reports "no signal" for every market.
        """
        pos = tick_ctx.get_position(market.market_id)
        effective_cash = tick_ctx.cash if cash is None else Decimal(str(cash))
        base_kwargs = {
            "cash": effective_cash,
            "equity": tick_ctx.equity,
            "total_pnl": tick_ctx.total_pnl,
            "position_count": len(tick_ctx.positions),
        }
        if pos is None:
            return PortfolioSnapshot(**base_kwargs)
        side_lower = (pos.side or "").lower()
        if side_lower not in {"yes", "no"}:
            return PortfolioSnapshot(**base_kwargs)
        return PortfolioSnapshot(
            market_position_side=side_lower,
            market_position_shares=Decimal(str(pos.shares)),
            **base_kwargs,
        )

    def _signal_to_intents(
        self,
        signal: BetSignal,
        market,
        tick_ctx: TickContext,
        rationale: str,
    ) -> list[dict[str, Any]]:
        """Convert a BetSignal into one or more trade intents for the runner.

        Mirrors the NET-flip logic in anri-trading's BettingEngine
        (engine.py:419-490): if we hold the opposite side of what the
        strategy wants, emit a SELL of the existing position first so we
        actually flip — not hedge — and so the strategy's sizing math is
        consistent (it sized the BUY assuming the SELL cash is available).

        Three cases:
          1. No existing position, or position on the same side as desired
             → single BUY for ``signal.shares``.
          2. Opposite-side position AND desired size <= held size
             → single SELL of ``signal.shares`` on the held side (no BUY
             needed; we're just trimming the existing position).
          3. Opposite-side position AND desired size > held size
             → SELL the full held position, then BUY the remainder on the
             new side.
        """
        desired_side = (signal.side or "").upper()
        if desired_side not in {"YES", "NO"}:
            logger.warning(
                "Unknown side %r from strategy for %s",
                signal.side, market.market_id,
            )
            return []

        # Convert strategy fractional shares → PA-server contract units.
        desired_contracts = float(signal.shares) * SHARES_SCALE
        if desired_contracts <= 0:
            return []

        base = {
            "run_id": tick_ctx.run_id,
            "tick_ts": tick_ctx.tick_ts,
            "market_id": market.market_id,
            "question": market.question,
            "rationale": rationale or "",
        }

        pos = tick_ctx.get_position(market.market_id)
        held_side = (pos.side or "").upper() if pos else None
        # PositionData.shares is already in contract units — same scale as
        # what we emit. No conversion needed.
        held_contracts = float(pos.shares) if pos else 0.0

        # Same side or no position — vanilla BUY.
        if held_side is None or held_side == desired_side or held_contracts <= 0:
            return [{
                **base,
                "action": "BUY",
                "side": desired_side,
                "shares": f"{desired_contracts:.4f}",
            }]

        # Opposite-side held — flip.
        sell_contracts = min(desired_contracts, held_contracts)
        remaining_buy_contracts = desired_contracts - sell_contracts

        intents: list[dict[str, Any]] = [{
            **base,
            "action": "SELL",
            "side": held_side,
            "shares": f"{sell_contracts:.4f}",
        }]
        if remaining_buy_contracts > 1e-4:
            intents.append({
                **base,
                "action": "BUY",
                "side": desired_side,
                "shares": f"{remaining_buy_contracts:.4f}",
            })
        logger.info(
            "NET flip on %s: SELL %.4f %s + BUY %.4f %s (held=%.4f, want=%.4f contracts)",
            market.market_id, sell_contracts, held_side, remaining_buy_contracts,
            desired_side, held_contracts, desired_contracts,
        )
        return intents

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
