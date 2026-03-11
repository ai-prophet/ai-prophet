"""Betting adapter helpers for pipeline integration.

This module provides the glue between the CLI's pipeline and the
core betting engine.  The betting engine consumes forecasts produced
by the normal trading pipeline — no separate LLM path is needed.
"""

from __future__ import annotations

import logging
from typing import Any

from ai_prophet.trade.core.tick_context import CandidateMarket

logger = logging.getLogger(__name__)


def build_betting_reasoning(
    candidate_markets: tuple[CandidateMarket, ...],
    forecasts: dict[str, float],
    intents: list[dict],
) -> dict[str, Any]:
    """Build compact reasoning payload for betting participants."""
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
        m_id: {"p_yes": p_yes, "rationale": "betting_pipeline"}
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
