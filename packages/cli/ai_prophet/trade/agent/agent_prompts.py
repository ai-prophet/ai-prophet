"""Port of the canonical ProphetArena AgentPrompts.

Source (verbatim where possible):
    UnifiedProphetArena/ProphetArena/app/services/llms/prompts.py :: AgentPrompts

The trader uses these prompts once per candidate market — same workflow as the
forecast track — and feeds the resulting p_yes into the betting strategy.
"""

from __future__ import annotations

import json
import re
from typing import Any

# Match raw ASCII control characters (excluding tab/newline/CR which JSON
# explicitly allows when whitespace, and which json.loads tolerates outside
# string literals). Anything else inside a string literal triggers
# "Invalid control character" — strip them.
_BAD_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


class AgentPrompts:
    """Prompts for agent prediction tasks (binary or multi-outcome)."""

    @staticmethod
    def create_task_prompt(
        event_title: str,
        market_names: list[str],
        rules: str | None = None,
        avoid_market_search: bool = False,
    ) -> str:
        market_list_str = "\n".join(f"- {m}" for m in market_names)
        json_example = ",\n                ".join(
            f'"{m}": <probability_value_from_0_to_1>' for m in market_names
        )
        rules_block = (
            f"\n  An example rule for one of the event outcomes is: {rules}\n"
            if rules
            else ""
        )
        avoid_market_block = ""
        if avoid_market_search:
            avoid_market_block = """
                CRITICAL RESTRICTION:
                - Do NOT search for or use any prediction market data, betting odds, or market prices
                - Do NOT reference Polymarket, Kalshi, PredictIt, Metaculus, or any other prediction/betting platforms
                - Base your predictions ONLY on factual news, expert analysis, and primary sources
                - Your prediction should be independent of any existing market consensus
            """

        return f""" You are an AI assistant specialized in analyzing and predicting real-world events.
                You have deep expertise in predicting the outcome of the event: "{event_title}"
                {rules_block}
                Note that this event occurs in the future. Your goal is to extract meaningful insights and provide well-reasoned predictions based on the given data.
                You will be predicting the probability (as a float value from 0 to 1) of ONLY the following possible outcomes:
                {market_list_str}
                {avoid_market_block}
                IMPORTANT CONSTRAINTS:
                1. You MUST ONLY provide probabilities for the exact possible outcomes listed above
                2. Do NOT create or invent any additional outcomes
                3. Use exactly the same outcome names as provided (case-sensitive)
                4. Ensure all probabilities are between 0 and 1
                5. For MULTI-OUTCOME events (more than 2 outcomes), the probabilities DO NOT have to sum to 1.0. Each probability is the standalone probability that THAT specific outcome resolves YES, independent of the others. Outcomes are not mutually exclusive in general — assign each one its own honest probability.

                Your response MUST be in JSON format with the following structure:
                ```json
                {{
                    "rationale": "<short_concise_3_sentence_rationale>",
                    "probabilities": {{
                        {json_example}
                    }}
                }}
                ```

                In the rationale section of your response, please provide a short, concise, 3 sentence rationale that explains:
                - How you weighed different pieces of information
                - Your reasoning for the probability distribution you assigned
                - Any key factors or uncertainties you considered
        """.strip()

    @staticmethod
    def create_user_prompt(market_stats: dict | None = None) -> str:
        base_prompt = (
            "Please analyze the event described in the system prompt and provide "
            "your prediction following the specified format."
        )
        if not market_stats:
            return base_prompt
        market_stats_info = f"""
            CURRENT ONLINE TRADING DATA:
            You also have access to the predicted outcome probability (last trading price of each outcome turned out to be yes) from a popular prediction market at the moment of your prediction:
            {json.dumps(market_stats, indent=2)}

            Note: Market data can provide insights into the current consensus of the market influenced by traders of various beliefs and private information. However, you should not rely on market data alone to make your prediction.
            Please consider both the market data and the information sources to help you make a well-calibrated prediction.
            """
        return f"{market_stats_info}\n\n{base_prompt}".strip()


def parse_response(text: str, expected_markets: list[str]) -> dict[str, Any]:
    """Extract ``{rationale, probabilities: {market: prob}}`` from a model reply.

    Tolerates markdown fences, surrounding prose, and 0-100-scaled probabilities.
    Returns ``{rationale: str, probabilities: dict[str, float]}``.
    """
    if not text:
        raise ValueError("empty model response")

    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned.rsplit("```", 1)[0]

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object in response: {cleaned[:300]!r}")

    candidate = _BAD_CONTROL_CHARS_RE.sub(" ", cleaned[start:end + 1])
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        # Last-resort: loosen via strict=False so json.loads tolerates any
        # stray control chars we missed inside string literals.
        data = json.loads(candidate, strict=False)
    raw_probs = data.get("probabilities")
    if raw_probs is None:
        raise KeyError("response missing 'probabilities'")

    if isinstance(raw_probs, dict):
        items: list[tuple[str, float]] = [
            (str(k), float(v)) for k, v in raw_probs.items()
        ]
    elif isinstance(raw_probs, list):
        items = [
            (str(item["market"]), float(item["probability"])) for item in raw_probs
        ]
    else:
        raise TypeError("'probabilities' must be dict or list")

    # Rescale 0-100 → 0-1 if the model emitted percentages
    if any(p > 1.0 for _m, p in items):
        items = [(m, p / 100.0) for m, p in items]

    by_market = {m: max(0.0, min(1.0, p)) for m, p in items}

    # Case-insensitive realignment to the expected label set
    expected_lower = {m.lower(): m for m in expected_markets}
    if expected_markets and not any(m in by_market for m in expected_markets):
        realigned: dict[str, float] = {}
        for m, p in by_market.items():
            canonical = expected_lower.get(m.lower())
            if canonical:
                realigned[canonical] = p
        by_market = realigned

    rationale = data.get("rationale")
    if not isinstance(rationale, str):
        rationale = ""

    return {"rationale": rationale.strip(), "probabilities": by_market}


def yes_probability(parsed: dict[str, Any]) -> float | None:
    """Pick the YES-side probability from a parsed AgentPrompts response."""
    probs: dict[str, float] = parsed.get("probabilities") or {}
    if not probs:
        return None
    # Prefer an explicit YES label, then case-insensitive variants
    for key in ("YES", "Yes", "yes"):
        if key in probs:
            return float(probs[key])
    for k, v in probs.items():
        if k.strip().lower() in {"yes", "y", "true"}:
            return float(v)
    # Single-outcome shape: return the only probability
    if len(probs) == 1:
        return float(next(iter(probs.values())))
    return None


__all__ = ["AgentPrompts", "parse_response", "yes_probability"]
