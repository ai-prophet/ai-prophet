"""Port of UnifiedProphetArena AgentPrompts (canonical agent-mode prompt).

Source:
    UnifiedProphetArena/ProphetArena/app/services/llms/prompts.py :: AgentPrompts

Kept as a sibling module (not imported from ai_prophet_core) so this service
stays self-contained at deploy time. Matches the wording verbatim where the
spec allows; the optional 5-field `analysis` block is intentionally omitted
(commented out in the source too).
"""

from __future__ import annotations

import json
from typing import Any


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


def parse_agent_response(text: str, expected_markets: list[str]) -> dict[str, Any]:
    """Extract ``{rationale, probabilities: {market: prob}}`` from a model reply.

    Tolerates markdown fences, surrounding prose, and 0-100-scaled probabilities.
    Realigns labels case-insensitively to the expected set.
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

    data = json.loads(cleaned[start:end + 1])
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

    if any(p > 1.0 for _m, p in items):
        items = [(m, p / 100.0) for m, p in items]
    by_market = {m: max(0.0, min(1.0, p)) for m, p in items}

    expected_lower = {m.lower(): m for m in expected_markets}
    if expected_markets and not any(m in by_market for m in expected_markets):
        realigned: dict[str, float] = {}
        for m, p in by_market.items():
            canonical = expected_lower.get(m.lower())
            if canonical:
                realigned[canonical] = p
        if realigned:
            by_market = realigned

    rationale = data.get("rationale")
    if not isinstance(rationale, str):
        rationale = ""

    return {"rationale": rationale.strip(), "probabilities": by_market}


__all__ = ["AgentPrompts", "parse_agent_response"]
