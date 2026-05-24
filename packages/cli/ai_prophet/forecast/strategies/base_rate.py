"""Base-rate strategy.

Anchors the forecast to the "outside view": identify the reference class
of events this question belongs to, estimate the historical base rate, then
only modestly update toward the inside view based on event-specific evidence.
Uses the cheaper "research" tier.
"""

from __future__ import annotations

import logging
from datetime import date

from ..llm_utils import call_llm_json
from .base import Estimate, clamp_confidence, clamp_probability, failed_estimate

logger = logging.getLogger(__name__)

STRATEGY_NAME = "base_rate"

_SYSTEM_PROMPT = """You are a base-rate forecasting analyst. Your job is to
anchor every estimate to the outside view before considering the inside view.

Procedure:
1. Identify the reference class: what broader category of events does this
   belong to? (e.g. "a sitting US president loses re-election", "a token
   exceeds an all-time high within N months", "a named hurricane makes
   landfall before its forecast cone closes"). When the event provides a
   Category, prefer a reference class drawn from that domain — Sports →
   similar matchups, teams, or series in the same league; Politics →
   similar elections or legislative votes; Economics → similar Fed meetings
   or data releases; Crypto → similar price milestones over comparable
   windows; Science → similar institutional announcements or replications.
2. Estimate the historical base rate for that reference class. State it
   numerically. If you genuinely do not know, say so and stay near 0.5.
3. Look at the event-specific evidence in the research brief. Only update
   the base rate if the evidence is meaningfully stronger than what is
   typical for the reference class.
4. Resist the inside view. Base rates dominate; specific evidence adjusts.

Calibration discipline:
- A reference class with a 30% base rate is your prior; specific evidence
  should rarely push it past 50% unless that evidence is exceptional.
- Avoid extreme probabilities unless the base rate itself is extreme AND
  specific evidence agrees.
- If you cannot identify a defensible reference class, your confidence is low.

Confidence (0.1-1.0) tracks how well-defined the reference class is and how
much the specific evidence agrees with it. Your confidence should rarely
exceed 0.6 — you are providing a cross-check, not the primary analysis.

Respond with ONLY a JSON object:
{"p_yes": <float 0.01-0.99>, "confidence": <float 0.1-1.0>,
 "rationale": "<2-3 sentences: name the reference class, state the base
 rate, and explain the adjustment>"}

No prose, no markdown fences, no commentary."""


def _build_user_prompt(
    *,
    title: str,
    description: str | None,
    category: str | None,
    rules: str | None,
    close_time: str | None,
    research: str,
    outcomes: list[str] | None = None,
    temporal_context: str | None = None,
) -> str:
    lines = [
        f"Today: {date.today().isoformat()}",
        f"Event: {title}",
    ]
    if category:
        lines.append(f"Category: {category}")
    if outcomes and len(outcomes) >= 2:
        lines.append(f"OUTCOMES: YES = {outcomes[0]}, NO = {outcomes[1]}")
    if description:
        lines.append(f"Description: {description}")
    if rules:
        lines.append(f"Resolution rules: {rules}")
    if close_time:
        lines.append(f"Market closes at: {close_time}")
    if temporal_context:
        lines.append(temporal_context)
    lines.append("")
    lines.append("Research brief (use to identify reference class and adjust):")
    lines.append(research if research else "(no research available)")
    lines.append("")
    lines.append(
        "Identify the reference class, state the historical base rate, then "
        "adjust modestly using the specific evidence. Return the JSON object only."
    )
    return "\n".join(lines)


class BaseRateStrategy:
    """Base-rate analyst using the cheap research-tier LLM."""

    name = STRATEGY_NAME

    def estimate(
        self,
        *,
        title: str,
        description: str | None = None,
        category: str | None = None,
        rules: str | None = None,
        close_time: str | None = None,
        research: str = "",
        outcomes: list[str] | None = None,
        temporal_context: str | None = None,
    ) -> Estimate:
        user = _build_user_prompt(
            title=title,
            description=description,
            category=category,
            rules=rules,
            close_time=close_time,
            research=research,
            outcomes=outcomes,
            temporal_context=temporal_context,
        )
        try:
            data = call_llm_json(
                _SYSTEM_PROMPT,
                user,
                tier="research",
                temperature=0.3,
                max_tokens=500,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s failed: %s", STRATEGY_NAME, exc)
            return failed_estimate(STRATEGY_NAME, str(exc))

        try:
            p = clamp_probability(float(data["p_yes"]))
            conf = clamp_confidence(float(data.get("confidence", 0.5)))
            rationale = str(data.get("rationale", "")).strip() or "(no rationale provided)"
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("%s bad payload: %s", STRATEGY_NAME, exc)
            return failed_estimate(STRATEGY_NAME, f"bad payload: {exc}")

        return Estimate(
            p_yes=p,
            rationale=rationale,
            strategy=STRATEGY_NAME,
            confidence=conf,
        )
