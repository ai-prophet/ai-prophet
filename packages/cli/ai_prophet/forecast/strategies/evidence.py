"""Evidence-weighted strategy.

Reads the compiled research brief and weighs each piece of evidence by
recency, source reliability, and direct relevance. Calibration-aware: it
must justify extreme probabilities (<0.10 or >0.90) with multiple strong,
recent, independent sources. Uses the higher-quality "reasoning" tier.
"""

from __future__ import annotations

import logging
from datetime import date

from ..llm_utils import call_llm_json
from .base import Estimate, clamp_confidence, clamp_probability, failed_estimate

logger = logging.getLogger(__name__)

STRATEGY_NAME = "evidence_weighted"

_SYSTEM_PROMPT = """You are a calibrated forecasting analyst. You estimate the
probability that a binary event resolves YES given research notes from the
open web.

How to weigh evidence:
- Source reliability: official primary sources > major outlets > blogs > forums.
- Recency: newer evidence updates older priors. Note when sources are stale.
- Relevance: prefer sources that speak directly to the resolution criteria.
- Independence: two outlets repeating one wire story count as ~one source.
- Category-appropriate sourcing: weight the canonical sources for the event's
  Category higher (official league data for Sports, central bank releases and
  major financial press for Economics, on-chain data for Crypto, primary
  research and journals for Science, polls and official statements for
  Politics). Discount tangential or off-domain sources.

Calibration discipline:
- Brier score punishes overconfidence severely. Be honest about uncertainty.
- Probabilities below 0.10 or above 0.90 require multiple strong, recent,
  independent sources pointing the same direction.
- If the research brief is thin, contradictory, or off-topic, stay near 0.5.
- If resolution depends on a future event with real uncertainty, do not
  collapse to 0 or 1 even if the current trend is strong.

Your confidence should reflect research quality: 0.7+ when you have
multiple recent authoritative sources, 0.5-0.7 when sources are limited
or older, 0.3-0.5 when evidence is thin.

You are the primary analyst on this question — when the research is
genuinely strong, do not under-report your confidence out of false
modesty. The ensemble downweights you anyway if your peers disagree
sharply.

Respond with ONLY a JSON object:
{"p_yes": <float 0.01-0.99>, "confidence": <float 0.1-1.0>,
 "rationale": "<2-3 sentence justification grounded in the research>"}

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
    lines.append("Research brief:")
    lines.append(research if research else "(no research available)")
    lines.append("")
    lines.append(
        "Weigh the evidence above by reliability, recency, and relevance. "
        "Be calibration-aware: extreme probabilities require overwhelming "
        "evidence. Return the JSON object only."
    )
    return "\n".join(lines)


class EvidenceWeightedStrategy:
    """Evidence-weighted analyst using the reasoning tier LLM."""

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
                tier="reasoning",
                temperature=0.2,
                max_tokens=600,
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
