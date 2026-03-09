"""Live betting adapter helpers kept separate from benchmark orchestration."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime
from typing import Any

from ai_prophet.trade.core.tick_context import CandidateMarket

logger = logging.getLogger(__name__)


def is_live_betting_model(model_spec: str) -> bool:
    """Return True when this participant uses live_betting strategy."""
    try:
        from ai_prophet_core.betting.config import BETTING_MODEL_SPECS

        return model_spec in BETTING_MODEL_SPECS
    except ImportError:
        return False


def execute_live_betting_strategy(
    model_spec: str,
    tick_ts: datetime,
    candidate_markets: list[CandidateMarket],
    experiment_id: str,
    live_betting_hook: Any = None,
) -> tuple[list[dict], dict[str, float]]:
    """Execute live_betting strategy in a single batched forecast pass."""
    try:
        from ai_prophet_core.betting.config import get_pipeline_config
        from ai_prophet_core.betting.strategy import compute_bet
        from ai_prophet.trade.llm import create_llm_client

        config = get_pipeline_config(model_spec)
        if not config:
            logger.error("No live_betting config for model: %s", model_spec)
            return [], {}

        provider = config["provider"]
        api_model = config["api_model"]
        llm_provider = "gemini" if provider == "google" else provider
        api_key_env = f"{llm_provider.upper()}_API_KEY"
        api_key = os.environ.get(api_key_env)
        if not api_key:
            logger.error("Missing API key for %s (%s)", llm_provider, api_key_env)
            return [], {}

        llm_client = create_llm_client(
            provider=llm_provider,
            model=api_model,
            api_key=api_key,
        )
        try:
            forecasts = _batch_forecast_markets(
                llm_client=llm_client,
                markets=candidate_markets,
            )
        finally:
            close = getattr(llm_client, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.debug("Failed to close live-betting LLM client", exc_info=True)

        intents: list[dict] = []
        for market_id, p_yes in forecasts.items():
            market = next((m for m in candidate_markets if m.market_id == market_id), None)
            if not market:
                continue

            yes_ask = market.yes_ask
            no_ask = 1.0 - market.yes_bid
            bet = compute_bet(
                prediction_prob=p_yes,
                yes_ask=yes_ask,
                no_ask=no_ask,
            )

            if live_betting_hook:
                live_betting_hook.on_forecast(
                    model_name=model_spec,
                    tick_ts=tick_ts,
                    market_id=market_id,
                    p_yes=p_yes,
                    yes_ask=yes_ask,
                    no_ask=no_ask,
                    question=market.question,
                )

            if bet is None:
                continue

            intents.append(
                {
                    "run_id": str(experiment_id),
                    "tick_ts": tick_ts.isoformat(),
                    "market_id": market_id,
                    "question": market.question,
                    "action": "BUY",
                    "side": bet["side"].upper(),
                    "shares": f"{bet['shares']:.2f}",
                    "rationale": (
                        f"Live betting: p_yes={p_yes:.3f}, "
                        f"strategy={bet['side']} {bet['shares']:.4f}"
                    ),
                }
            )
        return intents, forecasts
    except Exception as e:
        logger.error("Live betting strategy failed: %s", e, exc_info=True)
        return [], {}


def build_live_betting_reasoning(
    candidate_markets: tuple[CandidateMarket, ...],
    forecasts: dict[str, float],
    intents: list[dict],
) -> dict[str, Any]:
    """Build compact reasoning payload for live-betting participants."""
    candidates = [
        {
            "market_id": m.market_id,
            "question": m.question,
            "yes_mark": round(m.yes_mark, 4),
            "volume_24h": m.volume_24h,
        }
        for m in candidate_markets
    ]

    forecasts_payload = {
        m_id: {"p_yes": p_yes, "rationale": "live_betting_batch"}
        for m_id, p_yes in forecasts.items()
    }

    market_lookup = {m.market_id: m for m in candidate_markets}
    decisions: dict[str, dict[str, Any]] = {}
    for intent in intents:
        market_id = str(intent.get("market_id", ""))
        if not market_id:
            continue
        side = str(intent.get("side", "")).upper()
        recommendation = "BUY_YES" if side == "YES" else "BUY_NO"
        shares = float(intent.get("shares", 0.0))
        market = market_lookup.get(market_id)
        if market and side in {"YES", "NO"}:
            price = market.yes_ask if side == "YES" else market.no_ask
            size_usd = shares * price
        else:
            size_usd = 0.0
        decisions[market_id] = {
            "recommendation": recommendation,
            "size_usd": round(size_usd, 4),
            "rationale": str(intent.get("rationale", "")),
        }

    return {
        "candidates": candidates,
        "forecasts": forecasts_payload,
        "decisions": decisions,
    }


def _batch_forecast_markets(
    llm_client: Any,
    markets: list[CandidateMarket],
) -> dict[str, float]:
    """Forecast probabilities for all candidate markets in one LLM call."""
    from ai_prophet.trade.llm import LLMMessage, LLMRequest

    markets_list = "\n".join([f"- {m.question}" for m in markets])
    json_example = ",\n        ".join([f'"{m.market_id}": <probability_0_to_1>' for m in markets])

    system_prompt = """You are an assistant forecasting prediction markets.
Return only JSON with rationale and probabilities for the provided market IDs.
Probabilities must be numeric floats in [0, 1]."""
    user_prompt = f"""Forecast YES probabilities for these markets:
{markets_list}

Return JSON:
{{
  "rationale": "<short rationale>",
  "probabilities": {{
        {json_example}
  }}
}}"""

    request = LLMRequest(
        messages=[
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content=user_prompt),
        ]
    )
    raw_content = llm_client.generate(request).content
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw_content, re.DOTALL)
    json_str = match.group(1).strip() if match else raw_content.strip()
    payload = json.loads(json_str)

    probs = payload.get("probabilities", {}) if isinstance(payload, dict) else {}
    result: dict[str, float] = {}
    for market_id, value in probs.items():
        if isinstance(value, (int, float)):
            result[market_id] = float(value)
    return result
