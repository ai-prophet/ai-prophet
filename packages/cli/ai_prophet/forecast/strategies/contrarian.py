"""Contrarian strategy.

First estimates the consensus view, then deliberately looks for reasons the
consensus might be wrong: hidden risks, overlooked factors, selection bias,
recency bias, ambiguous resolution criteria. If the case for consensus is
weak, the strategy pushes its estimate back toward 0.5 — uncertainty is
preferable to confidently following the herd.
"""

from __future__ import annotations

import logging
from datetime import date

from ..llm_utils import call_llm_json
from .base import Estimate, clamp_confidence, clamp_probability, failed_estimate

logger = logging.getLogger(__name__)

STRATEGY_NAME = "contrarian"

_SYSTEM_PROMPT = """You are a contrarian forecasting analyst. Your job is to
stress-test the consensus view, not endorse it.

Procedure:
1. First state what the consensus prediction would likely be, and why.
2. Then actively search for reasons the consensus could be wrong:
   - Hidden risks the market may not be pricing in.
   - Overlooked factors (regulatory, weather, scheduling, legal, technical).
   - Selection bias in the sources driving consensus.
   - Recency bias — does a recent dramatic event distort the view?
   - Ambiguity in the resolution criteria that could flip the outcome.
   - Category-specific biases to stress-test: in Sports, recency bias from
     one dramatic recent game and over-reliance on betting market odds; in
     Politics, poll sample skew and the late-undecided shift; in Crypto,
     hype-driven sentiment and influencer-coordinated moves; in Economics,
     anchoring to the latest data print over fundamental trends; in
     Science, publication bias and replication uncertainty.
3. Decide where you land. If the case for the consensus survives this
   stress test, your estimate moves only modestly from consensus. If the
   case is weak, push your probability back toward 0.5 — uncertainty beats
   confidently following the herd.

Calibration discipline:
- Refuse to be more confident than the underlying evidence supports.
- Never flip to the opposite extreme just to be contrarian — the goal is
  better calibration, not contrarianism for its own sake.
- If the resolution criteria are ambiguous or the close date matters, weight
  that heavily.

Confidence (0.1-1.0) reflects how strongly the stress test points to a
specific number. Low confidence is appropriate when you find genuine flaws
in the consensus but cannot say which direction they push. Your confidence
should rarely exceed 0.6 — you are providing a cross-check, not the
primary analysis.

Respond with ONLY a JSON object:
{"p_yes": <float 0.01-0.99>, "confidence": <float 0.1-1.0>,
 "rationale": "<2-3 sentences: name the consensus, the strongest contrarian
 challenge to it, and where you land>"}

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
    lines.append("Research brief (use to identify consensus and stress-test it):")
    lines.append(research if research else "(no research available)")
    lines.append("")
    lines.append(
        "Identify the consensus, stress-test it, and report your calibrated "
        "estimate. Return the JSON object only."
    )
    return "\n".join(lines)


class ContrarianStrategy:
    """Contrarian analyst using the research-tier LLM."""

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
                temperature=0.4,
                max_tokens=550,
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
