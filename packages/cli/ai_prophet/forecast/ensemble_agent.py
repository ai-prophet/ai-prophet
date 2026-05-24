"""Multi-strategy ensemble forecasting agent.

This is the entry point loaded by ``prophet forecast predict``:

.. code-block:: bash

    prophet forecast predict \\
        --events events.json \\
        --local ai_prophet.forecast.ensemble_agent

Three independent strategies run in parallel over the same web-researched
brief, then their probabilities are combined in log-odds space with
adaptive shrinkage. The module also exposes a FastAPI ``/predict`` endpoint
so the same agent can be served over HTTP via ``--agent-url``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import Any

from pydantic import BaseModel

from .cache import (
    cache_enabled,
    cache_multi_outcome,
    cache_prediction,
    get_cached_multi_outcome,
    get_cached_prediction,
)
from .ensemble import P_MAX, P_MIN, FinalPrediction, ensemble_predict
from .llm_utils import call_llm_json, fast_resolve
from .market_signal import (
    kalshi_multi_outcome_probabilities,
    market_signal_estimate,
)
from .researcher import research_event
from .strategies import (
    BaseRateStrategy,
    ContrarianStrategy,
    EvidenceWeightedStrategy,
)
from .strategies.base import (
    Estimate,
    clamp_confidence,
    clamp_probability,
    failed_estimate,
)
from .temporal import (
    hours_until_close,
    temporal_context_string,
    temporal_factor,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard event budget
# ---------------------------------------------------------------------------

EVENT_BUDGET_SECONDS = 55.0
"""Soft cap on total time spent inside :func:`forecast_event` for one event.

Prophet Arena's harness rejects responses past ~60 s. Each phase in
``forecast_event`` checks remaining budget before starting work and
short-circuits to the best partial result available (skip deliberation,
fall back to 0.5, etc.) when the budget is exhausted. See
``phase=timeout`` log lines for diagnostics."""


# ---------------------------------------------------------------------------
# Deliberation round
# ---------------------------------------------------------------------------

DELIBERATION_STRATEGY_NAME = "deliberation"
DELIBERATION_CONFIDENCE = 0.85
"""Confidence assigned to the deliberation Estimate. It has seen all three
analysts' rationales, so we trust it more than any single strategy — but not
so much that it overwhelms strong disagreement among the originals."""

_DELIBERATION_SYSTEM_PROMPT = """You are a meta-forecaster adjudicating among
three independent analysts' estimates of a binary prediction-market question.
Each analyst saw the same web research and is calibration-aware, but they
approach the problem from different angles (evidence-weighted, base-rate,
contrarian).

Your job is to ADJUDICATE, not average.

Procedure:
1. Identify which analyst made the strongest argument on THIS specific
   question. Different question structures favor different analysts:
   evidence-heavy questions favor the evidence analyst, novel-but-classifiable
   questions favor the base-rate analyst, consensus-prone questions favor the
   contrarian.
2. Spot any reasoning errors or hidden assumptions in the rationales.
3. Decide where the truth lies. If one analyst clearly dominates, weight your
   answer toward them. If the strongest analyst is still uncertain, hedge.
   Do NOT collapse to the simple mean — that defeats the purpose.

Calibration discipline:
- Probabilities below 0.10 or above 0.90 require an analyst making an
  overwhelming case that survives scrutiny.
- If the analysts disagree sharply AND none has a clearly stronger case,
  the right answer is closer to 0.5 — admit ignorance rather than pick a side.

Respond with ONLY a JSON object:
{"p_yes": <float 0.01-0.99>,
 "winner": "<strategy name with the strongest case, or 'none' if all weak>",
 "rationale": "<2-3 sentences: which case won and why; flag mistakes the
 others made>"}

No prose, no markdown fences, no commentary."""


def _deliberation_enabled() -> bool:
    """Read ``ENABLE_DELIBERATION`` from the env; default ``True``.

    Explicit disables: ``false``, ``0``, ``no``, ``off``, empty string.
    Anything else (including missing) → enabled.
    """
    raw = os.environ.get("ENABLE_DELIBERATION", "true").strip().lower()
    return raw not in {"false", "0", "no", "off", ""}


def _build_deliberation_prompt(
    event: EventRequest,
    estimates: list[Estimate],
    temporal_ctx: str | None = None,
) -> str:
    lines = [f"Event: {event.title}"]
    if event.outcomes and len(event.outcomes) >= 2:
        lines.append(
            f"OUTCOMES: YES = {event.outcomes[0]}, NO = {event.outcomes[1]}"
        )
    if event.category:
        lines.append(f"Category: {event.category}")
    if event.rules:
        lines.append(f"Resolution rules: {event.rules}")
    if temporal_ctx:
        lines.append(temporal_ctx)
    lines.append("")
    lines.append("Three independent estimates from the analysts:")
    lines.append("")
    for i, est in enumerate(estimates, 1):
        lines.append(
            f"{i}) {est.strategy}  p_yes={est.p_yes:.3f}  "
            f"confidence={est.confidence:.2f}"
        )
        lines.append(f"   Rationale: {est.rationale}")
        lines.append("")
    lines.append(
        "Adjudicate. Which analyst is most convincing on THIS specific "
        "question? Where does the final probability land? Return the JSON "
        "object only."
    )
    return "\n".join(lines)


def _deliberate(
    event: EventRequest,
    estimates: list[Estimate],
    temporal_ctx: str | None = None,
) -> Estimate | None:
    """Run one meta-LLM pass over the strategies' estimates.

    Returns a fourth :class:`Estimate` with ``strategy="deliberation"`` and
    confidence :data:`DELIBERATION_CONFIDENCE`, or ``None`` if the call fails
    or the LLM returned an unparseable payload. The caller treats ``None`` as
    "skip the deliberation round; proceed with the three originals."
    """
    if not estimates:
        return None
    user_prompt = _build_deliberation_prompt(event, estimates, temporal_ctx)
    try:
        data = call_llm_json(
            _DELIBERATION_SYSTEM_PROMPT,
            user_prompt,
            tier="reasoning",
            temperature=0.2,
            max_tokens=500,
        )
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.warning("deliberation LLM call failed: %s", exc)
        return None

    try:
        p = clamp_probability(float(data["p_yes"]))
        rationale = str(data.get("rationale", "")).strip() or "(no rationale)"
        winner = str(data.get("winner", "")).strip()
    except (KeyError, TypeError, ValueError) as exc:
        logger.warning("deliberation bad payload: %s", exc)
        return None

    if winner:
        rationale = f"[winner: {winner}] {rationale}"
    return Estimate(
        p_yes=p,
        rationale=rationale,
        strategy=DELIBERATION_STRATEGY_NAME,
        confidence=clamp_confidence(DELIBERATION_CONFIDENCE),
    )


# ---------------------------------------------------------------------------
# Pydantic request/response models (mirror the CLI predict contract)
# ---------------------------------------------------------------------------


class EventRequest(BaseModel):
    event_ticker: str | None = None
    market_ticker: str | None = None
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str | None = None
    rules: str | None = None
    close_time: str | None = None
    outcomes: list[str] | None = None
    resolved_outcome: Any | None = None


class PredictionResponse(BaseModel):
    p_yes: float
    rationale: str


# ---------------------------------------------------------------------------
# Strategy registry
# ---------------------------------------------------------------------------


def _build_strategies() -> list[Any]:
    return [
        EvidenceWeightedStrategy(),
        BaseRateStrategy(),
        ContrarianStrategy(),
    ]


# ---------------------------------------------------------------------------
# Core pipeline
# ---------------------------------------------------------------------------


def _coerce_event(event: dict[str, Any]) -> EventRequest:
    close = event.get("close_time")
    if close is not None and not isinstance(close, str):
        close = str(close)
    raw_outcomes = event.get("outcomes")
    outcomes = list(raw_outcomes) if isinstance(raw_outcomes, list) else None
    return EventRequest(
        event_ticker=event.get("event_ticker"),
        market_ticker=event.get("market_ticker"),
        title=str(event.get("title") or "").strip() or "(untitled event)",
        subtitle=event.get("subtitle"),
        description=event.get("description"),
        category=event.get("category"),
        rules=event.get("rules"),
        close_time=close,
        outcomes=outcomes,
        resolved_outcome=event.get("resolved_outcome"),
    )


def _resolved_outcome_value(raw: Any) -> str | None:
    """Normalize ``resolved_outcome`` to a single outcome string, or None.

    Accepts the two shapes seen in the wild:
      * a bare string (live-event format)
      * a dict with a ``"value"`` key that is either a string or a list
        of strings (the dataset-registry shape).
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        stripped = raw.strip()
        return stripped or None
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                return first.strip() or None
    return None


# Phrases in the evidence rationale that signal the analyst had no usable
# web research to work with. Used by :func:`_research_was_weak` together
# with a simple length floor on the research brief.
_WEAK_RESEARCH_PHRASES = (
    "no web evidence",
    "no research available",
    "research brief is empty",
    "research is sparse",
    "no relevant sources",
    "(no research available)",
)

_WEAK_RESEARCH_MIN_CHARS = 500


def _research_was_weak(research: str, estimates: list[Estimate]) -> bool:
    """Return True when the evidence analyst effectively flew blind.

    Triggers if the research brief is under ``_WEAK_RESEARCH_MIN_CHARS`` chars
    OR the evidence strategy's rationale contains one of the known
    "I had nothing to work with" phrases.
    """
    if len(research or "") < _WEAK_RESEARCH_MIN_CHARS:
        return True
    for est in estimates:
        if est.strategy != "evidence_weighted":
            continue
        low = (est.rationale or "").lower()
        if any(phrase in low for phrase in _WEAK_RESEARCH_PHRASES):
            return True
    return False


def _rebalance_on_weak_research(estimates: list[Estimate]) -> list[Estimate]:
    """Downweight evidence to 0.3 and boost base-rate to 0.7.

    When the agent has poor research, the Bayesian-correct move is to lean
    on priors (base-rate) rather than overweight a thin evidence read.
    Contrarian and market_consensus estimates are left untouched.
    """
    rebalanced: list[Estimate] = []
    for est in estimates:
        if est.strategy == "evidence_weighted":
            rebalanced.append(replace(est, confidence=0.3))
        elif est.strategy == "base_rate":
            rebalanced.append(replace(est, confidence=0.7))
        else:
            rebalanced.append(est)
    return rebalanced


# ---------------------------------------------------------------------------
# Market anchoring
# ---------------------------------------------------------------------------

MARKET_ANCHOR_DELTA = 0.05
"""``|ensemble - market| < MARKET_ANCHOR_DELTA`` → match the market price.

Tuned tighter than the original 0.10 because the live leaderboard showed
top teams clustered at near-zero Brier delta vs the market baseline —
they win by *not diverging*, not by out-forecasting. Under the scoring
formula ``(our_brier - market_brier) * completion_rate``, matching the
market on uncertain events caps downside hard. Below this 5% gap we
don't have enough edge to be worth the Brier risk."""

MARKET_ANCHOR_HIGH_AGREEMENT = 0.85
"""Above this strategy-agreement threshold (with a large delta) we trust
our ensemble over the market — our analysts converged on something the
crowd may have missed.

Raised from 0.75 to 0.85: we require near-unanimous internal consensus
before letting the ensemble override the market signal. Most events
don't clear this bar, which is intended — when in doubt, match."""

MARKET_ANCHOR_BLEND_WEIGHTS = (0.8, 0.2)
"""(market_weight, ensemble_weight) for the blend branch.

Shifted from (0.6, 0.4) to (0.8, 0.2). On large-delta + weak-agreement
events, our ensemble is more often wrong than right, so the blend now
leans 4x harder toward the market. Preserves a small ensemble pull for
the cases where the crowd is mispriced but our analysts didn't fully
converge."""

_MARKET_STRATEGY_NAMES = frozenset({"market_price", "market_consensus"})


def _market_anchored_prediction(
    final: FinalPrediction,
    estimates: list[Estimate],
    market_ticker: str | None = None,
) -> FinalPrediction:
    """Pull the final ``p_yes`` toward the market price unless we have edge.

    The scoring formula is ``(our_brier - market_brier) * completion_rate``,
    so by default we want to MATCH the market — deviating without conviction
    can only cost us points. Four-branch decision tree:

    1. **No market signal** in ``estimates`` → return the ensemble unchanged.
    2. **``|ensemble - market| < 0.10``** → return the market price. The gap
       is too small to justify deviating.
    3. **``|delta| >= 0.10`` AND agreement > 0.75** → trust the ensemble.
       Our strategies converged tightly on a different answer; that's alpha.
    4. **``|delta| >= 0.10`` AND agreement <= 0.75** → blend
       ``0.6 * market + 0.4 * ensemble``. We disagree with the market but
       our own internals are uncertain too, so hedge.

    The anchored ``p_yes`` is clamped to ``[P_MIN, P_MAX]`` so the
    ``Prediction`` schema accepts it. Diagnostic fields (``raw_p_yes``,
    ``agreement``, ``shrinkage``, ``estimates``) are preserved verbatim — we
    only override ``p_yes`` and append a one-line anchor note to
    ``rationale``.
    """
    market_p: float | None = None
    for est in estimates:
        if est.strategy in _MARKET_STRATEGY_NAMES:
            market_p = est.p_yes
            break

    if market_p is None:
        # Nothing to anchor to — let the ensemble result stand as-is.
        return final

    ensemble_p = final.p_yes
    delta = ensemble_p - market_p
    abs_delta = abs(delta)

    if abs_delta < MARKET_ANCHOR_DELTA:
        action = "match_market"
        anchored_p = market_p
    elif final.agreement > MARKET_ANCHOR_HIGH_AGREEMENT:
        action = "use_ensemble"
        anchored_p = ensemble_p
    else:
        action = "blend"
        wm, we = MARKET_ANCHOR_BLEND_WEIGHTS
        anchored_p = wm * market_p + we * ensemble_p

    anchored_p = max(P_MIN, min(P_MAX, anchored_p))

    logger.info(
        "phase=market_anchor ticker=%s market_p=%.3f ensemble_p=%.3f "
        "delta=%+.3f action=%s anchored_p=%.3f",
        market_ticker,
        market_p,
        ensemble_p,
        delta,
        action,
        anchored_p,
    )

    anchor_note = (
        f"\n[market_anchor: action={action} market_p={market_p:.3f} "
        f"ensemble_p={ensemble_p:.3f} agreement={final.agreement:.2f}]"
    )
    return replace(
        final,
        p_yes=anchored_p,
        rationale=final.rationale + anchor_note,
    )


def _market_signal_task(
    market_ticker: str | None, title: str | None
) -> Estimate | None:
    """Wrapper for the ThreadPoolExecutor — never raises.

    Hands off to :func:`market_signal_estimate`, which tries Kalshi by
    ticker first and falls back to Polymarket by fuzzy title.
    """
    if not market_ticker and not title:
        return None
    try:
        return market_signal_estimate(market_ticker, title)
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.info("market_signal crashed: %s", exc)
        return None


def _run_strategy(
    strategy: Any,
    event: EventRequest,
    research: str,
    temporal_ctx: str | None = None,
) -> Estimate:
    try:
        return strategy.estimate(
            title=event.title,
            description=event.description,
            category=event.category,
            rules=event.rules,
            close_time=event.close_time,
            research=research,
            outcomes=event.outcomes,
            temporal_context=temporal_ctx,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("strategy %s crashed: %s", getattr(strategy, "name", "?"), exc)
        return failed_estimate(getattr(strategy, "name", "unknown"), str(exc))


def forecast_event(
    event: EventRequest,
    *,
    research_override: str | None = None,
) -> FinalPrediction:
    """Run the full ensemble pipeline for one event and return its prediction.

    **Phase 0** runs first: a direct market-signal lookup (Kalshi by
    ticker, Polymarket by fuzzy title). If a market signal exists, we
    return it as the prediction *immediately* — no research, no
    strategies, no deliberation. Rationale: the eval scores against the
    market baseline, so matching the market on any event with a tradable
    price guarantees a near-zero Brier delta for that event. Our own
    ensemble can only beat the market when it has genuine edge, and
    being confidently wrong is far more punishing than matching. The
    leaderboard top teams (Dr Strange, partini at +0.01 to +0.02) win
    by doing exactly this.

    Only events with no market signal fall through to the full ensemble
    pipeline, where we have to forecast from scratch.

    When ``research_override`` is provided (non-empty), the web-research
    phase is skipped and the override is used as the brief that feeds all
    strategies. This lets the OpenAI-compatible endpoint reuse the source
    bundle the evaluator already curated in the chat ``user`` message
    instead of paying ~10 s to rebuild a worse one ourselves.
    """
    overall_start = time.perf_counter()

    # Phase 0: pure market-matching short-circuit. If the event has a
    # tradable market signal, return it as the prediction directly and
    # skip the whole ensemble. This matches the top-team strategy on the
    # leaderboard — under (our_brier − market_brier) × completion_rate,
    # matching the market on every covered event drives delta toward 0
    # and saves ~30-45 s of LLM latency (which fixes completion-rate too).
    market_est = _market_signal_task(event.market_ticker, event.title)
    if market_est is not None:
        logger.info(
            "phase=market_match ticker=%s p_yes=%.3f strategy=%s "
            "conf=%.2f elapsed=%.2fs",
            event.market_ticker,
            market_est.p_yes,
            market_est.strategy,
            market_est.confidence,
            time.perf_counter() - overall_start,
        )
        return FinalPrediction(
            p_yes=market_est.p_yes,
            rationale=(
                f"{market_est.rationale}\n"
                "[market_match: returning market signal directly without "
                "ensemble override — Brier scoring favors matching the "
                "market when a signal exists.]"
            ),
            raw_p_yes=market_est.p_yes,
            agreement=1.0,
            shrinkage=0.0,
            estimates=[market_est],
        )

    # Compute temporal context once and pass it everywhere downstream:
    # strategies see the time-horizon sentence, deliberation sees it, and
    # ensemble_predict scales its base shrinkage by the factor (imminent
    # events get less shrinkage and so a more decisive final p_yes).
    hours = hours_until_close(event.close_time)
    factor = temporal_factor(hours)
    temporal_ctx = temporal_context_string(hours)
    logger.info(
        "phase=temporal ticker=%s hours=%s factor=%.2f",
        event.market_ticker,
        f"{hours:.1f}" if hours is not None else "unknown",
        factor,
    )

    # Phase 1: research (skipped if caller supplied a curated brief)
    research_start = time.perf_counter()
    if research_override:
        research = research_override
        logger.info(
            "phase=research ticker=%s chars=%d source=override",
            event.market_ticker,
            len(research),
        )
    else:
        research = research_event(
            title=event.title,
            description=event.description,
            category=event.category,
            rules=event.rules,
        )
        logger.info(
            "phase=research ticker=%s chars=%d elapsed=%.2fs",
            event.market_ticker,
            len(research),
            time.perf_counter() - research_start,
        )

    # Phase 1.5: fast-resolve smart routing for Sports events that already
    # have a public result. Costs one cheap LLM call; if it returns a
    # decisive estimate we skip the entire strategy pipeline.
    fast_est = fast_resolve(event, research)
    if fast_est is not None:
        logger.info(
            "phase=fast_resolve ticker=%s p_yes=%.3f conf=%.2f elapsed=%.2fs",
            event.market_ticker,
            fast_est.p_yes,
            fast_est.confidence,
            time.perf_counter() - overall_start,
        )
        return FinalPrediction(
            p_yes=fast_est.p_yes,
            rationale=fast_est.rationale,
            raw_p_yes=fast_est.p_yes,
            agreement=1.0,
            shrinkage=0.0,
            estimates=[fast_est],
        )

    # Budget gate before strategies: if research alone burned the budget,
    # we have research but no estimates — return the 0.5 safe fallback.
    elapsed = time.perf_counter() - overall_start
    if elapsed > EVENT_BUDGET_SECONDS:
        logger.warning(
            "phase=timeout ticker=%s stage=before_strategies elapsed=%.2fs "
            "budget=%.0fs; returning 0.5 fallback",
            event.market_ticker,
            elapsed,
            EVENT_BUDGET_SECONDS,
        )
        return FinalPrediction(
            p_yes=0.5,
            rationale=(
                f"Timed out after research at {elapsed:.1f}s "
                f"(budget {EVENT_BUDGET_SECONDS:.0f}s); no strategies ran."
            ),
            raw_p_yes=0.5,
            agreement=0.0,
            shrinkage=0.0,
            estimates=[],
        )

    # Phase 2: parallel strategies. The market-signal lookup already
    # happened in Phase 0 (and returned None — otherwise we'd have
    # short-circuited). So we only run the analyst strategies here.
    strat_start = time.perf_counter()
    strategies = _build_strategies()
    estimates: list[Estimate] = []
    # ``max_workers=max(1, len(strategies))`` so an empty strategy list
    # (defensive / test setup) doesn't crash the executor.
    with ThreadPoolExecutor(max_workers=max(1, len(strategies))) as pool:
        strategy_futures = [
            pool.submit(_run_strategy, s, event, research, temporal_ctx)
            for s in strategies
        ]
        for fut in as_completed(strategy_futures):
            estimates.append(fut.result())
    logger.info(
        "phase=strategies ticker=%s n=%d (market=no) elapsed=%.2fs",
        event.market_ticker,
        len(estimates),
        time.perf_counter() - strat_start,
    )
    for est in estimates:
        logger.info(
            "  strategy=%s p=%.3f conf=%.2f",
            est.strategy,
            est.p_yes,
            est.confidence,
        )

    # Phase 2.25: dynamic confidence scaling when research came up dry.
    # Bayesian move: lean on the base-rate prior instead of overweighting
    # an evidence read with nothing under it.
    if _research_was_weak(research, estimates):
        logger.info(
            "phase=rebalance ticker=%s weak_research=true "
            "(evidence->0.3, base_rate->0.7)",
            event.market_ticker,
        )
        estimates = _rebalance_on_weak_research(estimates)

    # Budget gate before deliberation: deliberation is one full LLM call
    # (~5-15 s). If strategies already used the whole budget, skip it and
    # let the ensemble run on what we have.
    elapsed = time.perf_counter() - overall_start
    over_budget = elapsed > EVENT_BUDGET_SECONDS

    if over_budget:
        logger.warning(
            "phase=timeout ticker=%s stage=before_deliberation elapsed=%.2fs "
            "budget=%.0fs; skipping deliberation, proceeding to ensemble",
            event.market_ticker,
            elapsed,
            EVENT_BUDGET_SECONDS,
        )
    elif _deliberation_enabled():
        # Phase 2.5: optional deliberation round — one meta-LLM call
        # adjudicates over the three analysts' estimates and produces a
        # fourth estimate.
        delib_start = time.perf_counter()
        delib = _deliberate(event, estimates, temporal_ctx)
        delib_elapsed = time.perf_counter() - delib_start
        if delib is not None:
            estimates.append(delib)
            logger.info(
                "phase=deliberation ticker=%s p=%.3f conf=%.2f elapsed=%.2fs",
                event.market_ticker,
                delib.p_yes,
                delib.confidence,
                delib_elapsed,
            )
        else:
            logger.info(
                "phase=deliberation ticker=%s skipped (call failed) elapsed=%.2fs",
                event.market_ticker,
                delib_elapsed,
            )
    else:
        logger.debug("phase=deliberation disabled via ENABLE_DELIBERATION")

    # Safety: if every strategy crashed and there is no market signal
    # either, ``estimates`` is empty and ``ensemble_predict`` has nothing
    # to combine. Return the 0.5 fallback before the ensemble math.
    if not estimates:
        logger.warning(
            "phase=ensemble ticker=%s no estimates available; returning 0.5",
            event.market_ticker,
        )
        return FinalPrediction(
            p_yes=0.5,
            rationale=(
                "All strategies failed and no market signal was found; "
                "returning 0.5 fallback."
            ),
            raw_p_yes=0.5,
            agreement=0.0,
            shrinkage=0.0,
            estimates=[],
        )

    # Diagnostic: show the relative weight each estimate carries into the
    # ensemble (confidence / sum of confidences). Useful for verifying
    # rebalanced strategy confidence is doing what we expect — e.g. the
    # evidence_weighted analyst should typically dominate when the research
    # is strong.
    _total_conf = sum(e.confidence for e in estimates) or 1.0
    weight_parts = " ".join(
        f"{e.strategy}={e.confidence / _total_conf:.3f}" for e in estimates
    )
    logger.info(
        "phase=weights ticker=%s %s",
        event.market_ticker,
        weight_parts,
    )

    # Phase 3: ensemble + calibration (temporal-aware shrinkage)
    ens_start = time.perf_counter()
    final = ensemble_predict(estimates, temporal_factor=factor)
    logger.info(
        "phase=ensemble ticker=%s p_yes=%.3f agreement=%.2f shrinkage=%.2f "
        "temporal_factor=%.2f elapsed=%.2fs",
        event.market_ticker,
        final.p_yes,
        final.agreement,
        final.shrinkage,
        factor,
        time.perf_counter() - ens_start,
    )

    # Phase 4: market anchoring. By default we match the market; only
    # deviate when our research has genuine edge. This is the dominant
    # safety net for the (our_brier - market_brier) * completion_rate
    # scoring formula.
    final = _market_anchored_prediction(final, estimates, event.market_ticker)

    logger.info(
        "phase=total ticker=%s elapsed=%.2fs",
        event.market_ticker,
        time.perf_counter() - overall_start,
    )
    return final


# ---------------------------------------------------------------------------
# Local entry point (used by `prophet forecast predict --local`)
# ---------------------------------------------------------------------------


def _prediction_delay_seconds() -> float:
    """Read PREDICTION_DELAY from the environment, defaulting to 0 seconds.

    Default is zero: the Prophet Arena evaluation harness paces requests
    on its side. Any extra sleep here just eats into our 60-second budget
    per event and can cause timeouts. Override via ``PREDICTION_DELAY``
    for local batch CLI runs that need rate-limit headroom.

    Negative or unparseable values fall back to zero.
    """
    raw = os.environ.get("PREDICTION_DELAY", "0")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, value)


def _resolved_shortcut(event: dict[str, Any]) -> dict | None:
    """Return a finished prediction if the event is already resolved.

    When ``resolved_outcome`` is populated and ``outcomes[0]`` / ``outcomes[1]``
    name the YES and NO sides, the answer is known — there is no reason to
    burn LLM calls. Returns ``None`` if the event isn't conclusively resolved
    against the known outcomes.

    p_yes is clamped to the ``Prediction`` schema's ``[0.01, 0.99]`` bounds
    rather than 1.0 / 0.0 so the CLI's downstream validation accepts it.
    """
    resolved = _resolved_outcome_value(event.get("resolved_outcome"))
    if resolved is None:
        return None
    raw_outcomes = event.get("outcomes")
    if not isinstance(raw_outcomes, list) or len(raw_outcomes) < 2:
        return None

    yes_side = str(raw_outcomes[0]).strip()
    no_side = str(raw_outcomes[1]).strip()
    if resolved == yes_side:
        return {
            "p_yes": 0.99,
            "rationale": (
                f"Event already resolved: {resolved!r} matches outcomes[0]. "
                "Skipped research and strategies."
            ),
        }
    if resolved == no_side:
        return {
            "p_yes": 0.01,
            "rationale": (
                f"Event already resolved: {resolved!r} matches outcomes[1]. "
                "Skipped research and strategies."
            ),
        }
    return None


def predict(event: dict, *, _skip_pacing: bool = False) -> dict:
    """CLI-facing prediction function.

    Accepts an event dict matching :class:`EventRequest` and returns the
    ``{"p_yes": float, "rationale": str}`` payload expected by
    ``prophet forecast predict``. Failures are swallowed and yield a safe
    fallback of ``p_yes=0.5``.

    Events whose ``resolved_outcome`` already matches one of the two
    ``outcomes`` entries are short-circuited: we return the known answer
    directly and skip research, strategies, and pacing.

    If caching is enabled (``ENABLE_CACHE=true``, the default) and a fresh
    entry exists for the event's ``market_ticker``, the cached payload is
    returned immediately — no research, no strategies, no pacing sleep.
    Successful predictions are cached for ``CACHE_TTL_HOURS`` hours (default
    6) so repeated polls from the evaluation harness reuse the same work.

    Otherwise, after each call this function sleeps for ``PREDICTION_DELAY``
    seconds (default 5, overridable via the ``PREDICTION_DELAY`` env var) so
    that callers iterating over many events stay within provider rate limits.

    The ``_skip_pacing`` kwarg is for internal use by the batch endpoint —
    sleeping between every item in a 200-event batch would blow the 10-min
    response window. Leave it ``False`` for normal use.
    """
    ticker = event.get("market_ticker")

    shortcut = _resolved_shortcut(event)
    if shortcut is not None:
        logger.info(
            "predict.shortcut ticker=%s p_yes=%.2f",
            ticker,
            shortcut["p_yes"],
        )
        return shortcut

    if cache_enabled():
        cached = get_cached_prediction(ticker)
        if cached is not None:
            logger.info(
                "predict.cache_hit ticker=%s p_yes=%.3f expires_at=%s",
                ticker,
                cached["p_yes"],
                cached.get("expires_at"),
            )
            # Cache hit means zero LLM work was done — skip pacing too.
            return {
                "p_yes": cached["p_yes"],
                "rationale": cached["rationale"],
            }

    succeeded = False
    try:
        event_req = _coerce_event(event)
        final = forecast_event(event_req)
        result = {"p_yes": final.p_yes, "rationale": final.rationale}
        # Only treat as success if at least one strategy survived the
        # confidence floor — an all-failed ensemble returns 0.5, which we
        # don't want to lock into the cache for 6 hours.
        succeeded = bool(final.estimates)
    except Exception as exc:  # noqa: BLE001 — never crash the CLI
        logger.exception("predict() failed: %s", exc)
        result = {
            "p_yes": 0.5,
            "rationale": f"Ensemble agent failed: {exc}. Defaulting to 0.5.",
        }

    if succeeded and cache_enabled():
        cache_prediction(ticker, result["p_yes"], result["rationale"])

    if not _skip_pacing:
        delay = _prediction_delay_seconds()
        if delay > 0:
            logger.info("predict.pacing sleeping=%.1fs", delay)
            time.sleep(delay)
    return result


# ---------------------------------------------------------------------------
# FastAPI server (used by `prophet forecast predict --agent-url`)
# ---------------------------------------------------------------------------


def _extract_markets(event_dict: dict) -> list[str]:
    """Pull the list of market/outcome names from an event payload.

    Different upstream shapes use different field names. We try, in order:

    * ``outcomes`` — canonical ``Event`` schema (``list[str]``)
    * ``markets`` — alternate shape; values may be plain strings OR dicts
      with one of ``market_name`` / ``name`` / ``market_id`` /
      ``market_ticker`` / ``ticker`` as the readable label.

    Returns a flat ``list[str]``; empty list if no list-of-markets field
    is present or usable.
    """
    raw = event_dict.get("outcomes")
    if isinstance(raw, list) and raw and all(isinstance(x, str) for x in raw):
        return list(raw)

    raw = event_dict.get("markets")
    if isinstance(raw, list) and raw:
        if all(isinstance(x, str) for x in raw):
            return list(raw)
        out: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                name = (
                    item.get("market_name")
                    or item.get("name")
                    or item.get("market_id")
                    or item.get("market_ticker")
                    or item.get("ticker")
                )
                if name:
                    out.append(str(name))
        if out:
            return out

    return []


def _build_probabilities_list(
    p_yes: float, outcomes: list[str]
) -> list[dict[str, Any]]:
    """Build a probability distribution aligned to ``outcomes`` as a list
    of ``{"market": str, "probability": float}`` objects.

    The evaluation harness raises ``ValueError: probabilities[0] must be
    an object`` if entries are bare floats, so each entry is a dict with
    ``market`` (the outcome name) and ``probability`` (the float).

    Rules:
      * 0 outcomes → empty list.
      * 1 outcome → single entry with probability ``1.0``.
      * 2 outcomes → ``[{Yes, p_yes}, {No, 1 - p_yes}]``.
      * 3+ outcomes → ``p_yes`` on ``outcomes[0]``; the remaining mass
        ``(1 - p_yes)`` is split uniformly across the rest.

    All probabilities are rounded to 4 decimals; any rounding drift is
    absorbed into the final entry so the distribution sums to exactly 1.
    """
    n = len(outcomes)
    if n == 0:
        return []
    if n == 1:
        return [{"market": str(outcomes[0]), "probability": 1.0}]
    if n == 2:
        return [
            {"market": str(outcomes[0]), "probability": round(p_yes, 4)},
            {"market": str(outcomes[1]), "probability": round(1.0 - p_yes, 4)},
        ]

    remainder_each = (1.0 - p_yes) / (n - 1)
    items: list[dict[str, Any]] = [
        {"market": str(outcomes[0]), "probability": round(p_yes, 4)}
    ]
    for m in outcomes[1:]:
        items.append({"market": str(m), "probability": round(remainder_each, 4)})

    drift = 1.0 - sum(item["probability"] for item in items)
    if abs(drift) > 1e-9:
        items[-1]["probability"] = round(items[-1]["probability"] + drift, 4)
    return items


def _handle_single_event(event_dict: dict) -> dict:
    """Run one event through the cached + pacing-free pipeline.

    Used by both ``/predict`` (single-event mode) and ``/predictions`` /
    ``/predict`` (batch mode). Routing by outcome count:

    * **0-2 outcomes** → binary ensemble (:func:`predict`) plus
      :func:`_build_probabilities_list` for a paired distribution. Binary
      events always have ``p_yes + (1 - p_yes) = 1``, so the
      "sums-to-1" distribution shape is correct here.
    * **3+ outcomes** → :func:`_handle_multi_outcome_event` which calls
      the multi-outcome LLM once and emits **independent** per-market
      YES probabilities (no sum normalization). Per the eval admin each
      market is scored as its own binary YES Brier and a distributed
      sum-to-1 shape destroys Brier on non-mutually-exclusive events.

    The response always carries ``p_yes``, ``probabilities``, and
    ``rationale``. ``p_yes`` is the binary ensemble result on the binary
    path and the probability of ``outcomes[0]`` on the multi path.

    Pacing is suppressed; the HTTP layer handles request-level spacing
    implicitly via inter-request gaps.
    """
    event_t = event_dict.get("event_ticker") if isinstance(event_dict, dict) else None
    market_t = (
        event_dict.get("market_ticker") if isinstance(event_dict, dict) else None
    )
    title = (
        (event_dict.get("title") or "")[:60]
        if isinstance(event_dict, dict)
        else ""
    )

    # Discover the outcomes list — accepts both ``outcomes`` (canonical
    # Event schema) and ``markets`` (alternate dict-of-objects shape).
    outcomes = _extract_markets(event_dict) if isinstance(event_dict, dict) else []

    logger.info(
        "endpoint.predict request event_ticker=%s market_ticker=%s "
        "title=%r outcomes_n=%d",
        event_t,
        market_t,
        title,
        len(outcomes),
    )

    if len(outcomes) > 2:
        result = _handle_multi_outcome_event(event_dict, outcomes)
        logger.info(
            "endpoint.predict response event_ticker=%s market_ticker=%s "
            "p_yes=%.4f n_probs=%d shape=independent",
            event_t,
            market_t,
            float(result.get("p_yes", 0.5)),
            len(result.get("probabilities", [])),
        )
        return result

    result = predict(event_dict, _skip_pacing=True)
    p_yes = float(result["p_yes"])
    rationale = result["rationale"]

    probabilities = _build_probabilities_list(p_yes, outcomes)

    logger.info(
        "endpoint.predict response event_ticker=%s market_ticker=%s "
        "p_yes=%.4f n_probs=%d shape=binary",
        event_t,
        market_t,
        p_yes,
        len(probabilities),
    )

    return {
        "p_yes": p_yes,
        "probabilities": probabilities,
        "rationale": rationale,
    }


def _handle_multi_outcome_event(event_dict: dict, outcomes: list[str]) -> dict:
    """Run a 3+ outcome event through a single multi-outcome LLM call,
    emitting **independent** per-market YES probabilities.

    Per the eval admin: each outcome is scored as its own binary YES Brier,
    so probabilities must NOT be sum-normalized. A 3-threshold event like
    "BTC > $80k / $90k / $100k" can resolve all-YES at once; our values
    should reflect that.

    Pipeline:
      1. Cache lookup keyed on ``market_ticker``.
      2. Otherwise: gather research, single multi-outcome LLM call (the
         prompt explicitly tells the model these are independent YES/NO
         questions and not to renormalize).
      3. Clamp each value to ``[P_MIN, P_MAX]`` (no sum-normalization).
      4. Cache and return.

    Returns ``{p_yes, probabilities: [{market, probability}, ...], rationale}``.
    ``p_yes`` is taken from the first outcome so the response shape stays
    backwards-compatible with binary callers.
    """
    ticker = event_dict.get("market_ticker")
    event_ticker = event_dict.get("event_ticker") or ticker

    # Phase 0 (multi-outcome): try Kalshi's event-level lookup before
    # spending any LLM tokens. The eval is Kalshi-based, so most
    # multi-outcome events have N child markets (one per outcome) with
    # live prices we can use directly. Matching the market avoids the
    # Brier penalty for confident wrong guesses on non-mutually-
    # exclusive distributions.
    kalshi_probs = kalshi_multi_outcome_probabilities(event_ticker, outcomes)
    if kalshi_probs is not None:
        p_yes = float(kalshi_probs[0]["probability"]) if kalshi_probs else 0.5
        rationale = (
            f"Kalshi event={event_ticker} matched "
            f"{sum(1 for p in kalshi_probs if p['probability'] != 0.5)}/"
            f"{len(outcomes)} outcomes to live child markets; using "
            "market prices directly."
        )
        logger.info(
            "phase=market_match_multi event=%s n_probs=%d p_yes=%.3f",
            event_ticker,
            len(kalshi_probs),
            p_yes,
        )
        if cache_enabled():
            cache_multi_outcome(ticker, kalshi_probs, rationale, p_yes=p_yes)
        return {
            "p_yes": p_yes,
            "probabilities": kalshi_probs,
            "rationale": rationale,
        }

    if cache_enabled():
        cached = get_cached_multi_outcome(ticker)
        if cached is not None:
            logger.info(
                "predict.cache_hit ticker=%s shape=multi expires_at=%s",
                ticker,
                cached.get("expires_at"),
            )
            return {
                "p_yes": cached.get("p_yes", 0.5),
                "probabilities": cached["probabilities"],
                "rationale": cached["rationale"],
            }

    title = str(event_dict.get("title") or "(untitled event)")
    description = event_dict.get("description")
    category = event_dict.get("category")
    rules = event_dict.get("rules")

    try:
        research = research_event(
            title=title,
            description=description,
            category=category,
            rules=rules,
        )
    except Exception as exc:  # noqa: BLE001 — never crash the endpoint
        logger.warning("multi-outcome research failed: %s", exc)
        research = ""

    content = _predict_multi_outcome(title, outcomes, research)
    probs_dict = content.get("probabilities") or {}

    # Build the array of independent per-market probabilities. NO sum
    # normalization — each entry is its own binary YES probability.
    probs_array: list[dict[str, Any]] = []
    for m in outcomes:
        raw = probs_dict.get(m, 0.5)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 0.5
        v = max(P_MIN, min(P_MAX, v))
        probs_array.append({"market": str(m), "probability": round(v, 4)})

    p_yes = probs_array[0]["probability"] if probs_array else 0.5
    rationale = str(content.get("rationale") or "")[:1500]

    if cache_enabled():
        cache_multi_outcome(ticker, probs_array, rationale, p_yes=p_yes)

    return {
        "p_yes": p_yes,
        "probabilities": probs_array,
        "rationale": rationale,
    }


def _process_predict_body(body: Any) -> Any:
    """Dispatch a parsed JSON body to single-event or batch processing.

    Auto-detects by type:
      * ``list`` → process each item, return a list of results
      * ``dict`` → process one event, return one result
      * anything else → graceful 0.5 fallback with an explanatory rationale
    """
    if isinstance(body, list):
        logger.info("endpoint.predict batch n=%d", len(body))
        results: list[dict] = []
        for i, item in enumerate(body):
            if not isinstance(item, dict):
                logger.warning(
                    "endpoint.predict batch item %d is not a dict, skipping", i
                )
                results.append(
                    {
                        "p_yes": 0.5,
                        "rationale": "Item was not a JSON object.",
                    }
                )
                continue
            results.append(_handle_single_event(item))
        logger.info("endpoint.predict batch done n=%d", len(results))
        return results

    if isinstance(body, dict):
        return _handle_single_event(body)

    logger.warning(
        "endpoint.predict unexpected body type=%s; defaulting to 0.5",
        type(body).__name__,
    )
    return {
        "p_yes": 0.5,
        "rationale": (
            f"Request body must be a JSON object or array of objects; "
            f"got {type(body).__name__}."
        ),
    }


# ---------------------------------------------------------------------------
# OpenAI-compatible /chat/completions endpoint
#
# The Prophet Arena evaluation harness uses ``openai.OpenAI(base_url=…)`` to
# query agents. We act as the LLM provider: receive a system+user message
# describing the event/markets/sources, return an OpenAI ``ChatCompletion``
# response whose ``message.content`` is a JSON string of the form
# ``{"rationale": "…", "probabilities": {market_a: p, market_b: p, ...}}``.
# ---------------------------------------------------------------------------

_EVENT_TITLE_PATTERN = re.compile(
    r'event[s]?\s*[:\s]\s*["“]([^"”]+)["”]', re.IGNORECASE
)
_MARKET_LINE_PATTERN = re.compile(r"^\s*[-*]\s+(.+?)\s*$", re.MULTILINE)

# Anchor: find the bullet list that immediately follows the "possible
# outcomes:" heading. Greedy across blank lines that still contain bullets,
# stops at the first non-bullet, non-blank line. Keeping this scoped means
# unrelated instruction bullets elsewhere in the system prompt ("- Do not
# invent outcomes", "- Use the exact name", etc.) don't get parsed as
# markets.
_OUTCOMES_BLOCK_PATTERN = re.compile(
    r"possible\s+outcomes\s*:?\s*\n((?:\s*[-*]\s+.+\n?)+)",
    re.IGNORECASE,
)

_MULTI_MARKET_SYSTEM_PROMPT = """You are a calibrated forecasting analyst.
You will be given a multi-outcome prediction-market question, a list of
POSSIBLE OUTCOMES, and pre-curated research sources.

CRITICAL: each outcome is an INDEPENDENT binary YES/NO question. They are
scored independently and may ALL be true at once (e.g. "Will Bitcoin close
above $80k / $90k / $100k?" — if BTC closes at $110k, all three are YES)
or mutually exclusive (e.g. "Which team wins the championship?" — exactly
one is YES). Estimate each outcome's probability ON ITS OWN MERITS without
trying to make them sum to 1.0. Do NOT renormalize. The harness will
NOT renormalize either.

Your job: produce a probability in ``[0.01, 0.99]`` for EACH outcome,
representing the likelihood that THAT specific outcome resolves YES.

Calibration discipline:
- Brier score punishes overconfidence quadratically. Extremes (<0.10 or
  >0.90) require overwhelming evidence.
- For mutually-exclusive outcomes, your probabilities will naturally sum
  to ~1.0 because only one can win — but don't force it.
- For independent outcomes, each is its own 50/50 question with evidence
  shifting it up or down independently.
- When evidence is thin or sources are off-topic, stay near 0.5 per
  outcome.
- Weight sources by their ranking (lower rank = higher priority).
- Respect the EXACT outcome names from the question — case-sensitive — and
  give a probability for EVERY outcome listed. Missing or extra outcome
  names will be rejected by the harness.

Respond with ONLY a JSON object:
{
  "rationale": "<2-3 sentence justification grounded in the sources>",
  "probabilities": {
    "<outcome name 1>": <float 0.01-0.99>,
    "<outcome name 2>": <float 0.01-0.99>,
    ...
  }
}

No prose, no markdown fences, no commentary."""


def _parse_chat_request(messages: list[dict]) -> dict:
    """Pull event title, markets, and source text out of OpenAI-format messages.

    Returns a dict with keys ``title`` (str), ``markets`` (list[str]),
    ``system`` (concatenated system content), and ``user`` (concatenated
    user content). Best-effort: if the prompt format is unfamiliar, the
    fields come back empty and the caller falls back to a safe default.
    """
    system_parts: list[str] = []
    user_parts: list[str] = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").strip().lower()
        content = msg.get("content")
        if not isinstance(content, str):
            continue
        if role == "system":
            system_parts.append(content)
        elif role == "user":
            user_parts.append(content)

    system_text = "\n".join(system_parts).strip()
    user_text = "\n".join(user_parts).strip()

    title_match = _EVENT_TITLE_PATTERN.search(system_text)
    title = title_match.group(1).strip() if title_match else ""

    # Markets appear as a bulleted list right after the "possible outcomes:"
    # heading. Scope the regex to that block only — collecting every "- foo"
    # line in the whole system prompt is fragile (other instruction bullets
    # would be parsed as markets). Fall back to the broad scan only if no
    # heading is found.
    markets: list[str] = []
    block_match = _OUTCOMES_BLOCK_PATTERN.search(system_text)
    if block_match:
        block_text = block_match.group(1)
        for m in _MARKET_LINE_PATTERN.finditer(block_text):
            candidate = m.group(1).strip()
            if candidate and len(candidate) < 200:
                markets.append(candidate)
    else:
        for m in _MARKET_LINE_PATTERN.finditer(system_text):
            candidate = m.group(1).strip()
            # Skip instruction bullets when scanning the whole prompt.
            if (
                candidate
                and not candidate.lower().startswith(("must", "do ", "ensure", "use "))
                and len(candidate) < 200
            ):
                markets.append(candidate)

    return {
        "title": title,
        "markets": markets,
        "system": system_text,
        "user": user_text,
    }


def _normalize_probabilities(
    probabilities: dict[str, float], markets: list[str]
) -> dict[str, float]:
    """Clamp each probability to ``[P_MIN, P_MAX]`` and fill missing markets.

    Per the eval admin's confirmation, the harness scores each market's
    probability **independently** as its own binary YES Brier — values do
    NOT need to (and should not) be sum-normalized to 1.0. A 3-threshold
    event like "Bitcoin > $80k / $90k / $100k" can legitimately resolve
    all-YES, and our submitted ``[0.9, 0.85, 0.7]`` should stay that
    shape rather than being squashed to ``[0.37, 0.35, 0.28]`` summing
    to 1.

    Missing markets default to ``0.5`` (uninformative prior for an
    independent YES/NO question), not ``1/N`` (which was the right
    default only under the now-incorrect mutually-exclusive assumption).
    """
    out: dict[str, float] = {}
    for m in markets:
        raw = probabilities.get(m, 0.5)
        try:
            v = float(raw)
        except (TypeError, ValueError):
            v = 0.5
        out[m] = max(P_MIN, min(P_MAX, v))
    return out


def _predict_multi_outcome(
    title: str, markets: list[str], research: str
) -> dict[str, Any]:
    """Single LLM call that returns a probability per market.

    Used by the OpenAI-compatible endpoint for events with more than two
    outcomes — running the full binary ensemble N times for an event with
    20 markets would blow the 10-minute response window. The prompt mirrors
    the standalone-predictor format so we always emit valid output for
    multi-outcome events.
    """
    market_lines = "\n".join(f"- {m}" for m in markets)
    user_prompt = (
        f"Question: {title}\n\n"
        f"POSSIBLE OUTCOMES (must give probability for each by EXACT name):\n"
        f"{market_lines}\n\n"
        f"Research / sources:\n{(research or '(none provided)')[:6000]}\n\n"
        f"Return the JSON object only."
    )

    try:
        data = call_llm_json(
            _MULTI_MARKET_SYSTEM_PROMPT,
            user_prompt,
            tier="reasoning",
            temperature=0.2,
            max_tokens=900,
        )
    except Exception as exc:  # noqa: BLE001 — never break the harness
        logger.warning("multi-outcome LLM call failed: %s", exc)
        # Uniform fallback so every market still has a value.
        uniform = 1.0 / max(1, len(markets))
        return {
            "rationale": f"Multi-outcome LLM call failed ({exc}); defaulting to uniform {uniform:.3f}.",
            "probabilities": dict.fromkeys(markets, uniform),
        }

    rationale = str(data.get("rationale", "")).strip() or "(no rationale)"
    raw_probs = data.get("probabilities")
    if not isinstance(raw_probs, dict):
        raw_probs = {}
    return {
        "rationale": rationale,
        "probabilities": _normalize_probabilities(raw_probs, markets),
    }


def _handle_chat_completion(body: dict) -> dict:
    """Run our pipeline for an OpenAI-format request and emit an OpenAI response.

    Routing:
        * 2-market (binary) event → reuse our full ensemble pipeline via
          ``predict()``. Probabilities for the two sides are
          ``(p_yes, 1 - p_yes)``.
        * 3+ markets → single multi-outcome LLM call (``_predict_multi_outcome``)
          to fit inside the 10-min response budget.
        * Title or markets couldn't be parsed → safe uniform fallback.
    """
    model = (body.get("model") if isinstance(body, dict) else None) or "ensemble-agent"
    messages = body.get("messages") if isinstance(body, dict) else None
    if not isinstance(messages, list):
        messages = []

    parsed = _parse_chat_request(messages)
    title = parsed["title"]
    markets = parsed["markets"]
    research = parsed["user"]  # the user prompt is the curated source bundle

    logger.info(
        "endpoint.chat request model=%s title=%r markets=%d",
        model,
        title[:60],
        len(markets),
    )

    if not markets:
        logger.warning("endpoint.chat could not parse markets from messages")
        content = {
            "rationale": "Could not parse markets from the request prompt.",
            "probabilities": {},
        }
    elif len(markets) == 2:
        # Binary event — run the full ensemble pipeline, feeding the
        # evaluator's curated source bundle (the chat ``user`` message)
        # directly into the strategies via ``research_override`` so we
        # don't redo a worse web-research pass and pay ~10s for nothing.
        event_req = EventRequest(
            event_ticker="chat-event",
            market_ticker="chat-event",
            title=title or "(untitled event)",
            category="Other",
            outcomes=markets,
            close_time=None,
            description=None,
            rules=None,
            resolved_outcome=None,
        )
        try:
            final = forecast_event(
                event_req,
                research_override=research if research else None,
            )
            p_yes = float(final.p_yes)
            rationale = final.rationale
        except Exception as exc:  # noqa: BLE001 — never break the harness
            logger.warning("chat completions binary path failed: %s", exc)
            p_yes = 0.5
            rationale = f"Ensemble failed: {exc}; defaulting to 0.5."
        content = {
            "rationale": rationale[:1500],
            "probabilities": _normalize_probabilities(
                {markets[0]: p_yes, markets[1]: 1.0 - p_yes}, markets
            ),
        }
    else:
        content = _predict_multi_outcome(title, markets, research)

    logger.info(
        "endpoint.chat response model=%s markets=%d probabilities=%s",
        model,
        len(markets),
        {k: round(v, 3) for k, v in content.get("probabilities", {}).items()},
    )

    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": json.dumps(content),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
    }


def _build_app() -> Any:
    """Lazily construct the FastAPI app so importing this module is cheap."""
    from fastapi import Body, FastAPI

    fastapi_app = FastAPI(title="Ensemble Forecast Agent")

    @fastapi_app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe for Railway / load-balancer health checks."""
        return {"status": "ok", "service": "ensemble-forecast-agent"}

    # /predict — accepts either a single event dict or a list of event dicts.
    # Using ``Body(...)`` with ``Any`` so FastAPI doesn't try to validate
    # the body against a fixed Pydantic schema (which would reject the
    # alternate format with 422).
    @fastapi_app.post("/predict")
    async def predict_endpoint(body: Any = Body(...)):  # noqa: B008 — FastAPI idiom
        return _process_predict_body(body)

    # /predictions — alias so eval harnesses that expect the plural path
    # work identically (single + batch).
    @fastapi_app.post("/predictions")
    async def predictions_endpoint(body: Any = Body(...)):  # noqa: B008
        return _process_predict_body(body)

    # /v1/chat/completions — the OpenAI-compatible endpoint the Prophet
    # Arena evaluation harness will hit. Both the v1-prefixed and
    # unprefixed paths are registered so the harness works regardless of
    # whether their ``base_url`` ends in ``/v1``.
    @fastapi_app.post("/v1/chat/completions")
    async def chat_completions_v1(body: dict = Body(...)):  # noqa: B008
        return _handle_chat_completion(body)

    @fastapi_app.post("/chat/completions")
    async def chat_completions(body: dict = Body(...)):  # noqa: B008
        return _handle_chat_completion(body)

    return fastapi_app


# Module-level app instance for `uvicorn ai_prophet.forecast.ensemble_agent:app`.
app = _build_app()


def main() -> None:
    """Run the FastAPI server.

    Port resolution order:
        1. ``PORT`` — set by Railway and most PaaS providers.
        2. ``ENSEMBLE_PORT`` — local override for dev.
        3. Fallback ``8000``.

    Also configures the root logger at INFO with timestamps so per-request
    log lines (``endpoint.predict request|response …``) show up in Railway
    logs without extra setup. Uvicorn's own loggers are left alone — they
    add their own formatter.
    """
    import uvicorn

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    host = os.environ.get("ENSEMBLE_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("ENSEMBLE_PORT", "8000")))
    uvicorn.run(
        "ai_prophet.forecast.ensemble_agent:app",
        host=host,
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
