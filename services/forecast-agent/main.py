"""Prophet Arena forecast agent — Opus 4.7 + Kalshi market data + web search.

Wire contract mirrors ``ai_prophet_core.forecast.schemas.Event``. Prompt design
ports the canonical ProphetArena agent prompt
(``ProphetArena-Engine/app/services/llms/prompts.py``): exact-outcomes
constraint, structured JSON output with ``probabilities`` / ``rationale`` /
``analysis``, and a "CURRENT ONLINE TRADING DATA" section populated from
Kalshi's ``/markets`` snapshot for the event_ticker.

Deploy:
    set ANTHROPIC_API_KEY. Optional: KALSHI_API_KEY_ID,
    KALSHI_PRIVATE_KEY_B64 (Kalshi public market reads work without auth).
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import anthropic
import uvicorn
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from agent_prompts import AgentPrompts, parse_agent_response
from kalshi_client import KalshiForecastClient

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Prophet Arena Forecast Agent (Opus 4.7)")


# ---------------------------------------------------------------------------
# Wire contract — mirrors ai_prophet_core.forecast.schemas
# ---------------------------------------------------------------------------

class EventRequest(BaseModel):
    """Accepts both the ai_prophet_core.forecast.Event shape (outcomes) and the
    ProphetArena-Engine EventDB shape (markets). Every field except a label
    list is optional so we never reject a payload over missing context.
    """

    model_config = ConfigDict(extra="allow")

    event_ticker: str | None = None
    market_ticker: str | None = None
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    category: str | None = None
    rules: str | None = None
    close_time: str | None = None
    # Canonical outcome list — try both common names.
    outcomes: list[str] | None = None
    markets: list[str] | None = None


class MarketProbability(BaseModel):
    market: str
    probability: float = Field(ge=0.0, le=1.0)


class PredictionResponse(BaseModel):
    probabilities: list[MarketProbability]
    rationale: str | None = None


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("FORECAST_MODEL", "claude-opus-4-7")
DEFAULT_MAX_TOKENS = int(os.environ.get("FORECAST_MAX_TOKENS", "1500"))
DEFAULT_WEB_SEARCH_MAX_USES = int(os.environ.get("FORECAST_WEB_SEARCH_MAX_USES", "1"))
KALSHI_ENABLED = os.environ.get("FORECAST_KALSHI_ENABLED", "true").lower() == "true"
KALSHI_TIMEOUT_SEC = int(os.environ.get("FORECAST_KALSHI_TIMEOUT_SEC", "3"))


_client: anthropic.Anthropic | None = None
_kalshi: KalshiForecastClient | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _get_kalshi() -> KalshiForecastClient:
    global _kalshi
    if _kalshi is None:
        _kalshi = KalshiForecastClient(timeout_sec=KALSHI_TIMEOUT_SEC)
    return _kalshi


def _event_markets(event: EventRequest) -> list[str]:
    """Return the candidate outcome labels in priority order:
    explicit `outcomes`, then `markets` (ProphetArena name), then any
    extra-allowed alias the platform may attach, then a single binary
    fallback using market_ticker.
    """
    if event.outcomes:
        return [str(o) for o in event.outcomes if o]
    if event.markets:
        return [str(m) for m in event.markets if m]
    extras = event.model_dump()
    for alias in ("outcome_labels", "market_names", "candidates"):
        v = extras.get(alias)
        if isinstance(v, list) and v:
            return [str(x) for x in v if x]
    if event.market_ticker:
        return [event.market_ticker]
    return ["YES"]


# ---------------------------------------------------------------------------
# Kalshi market snapshot — implied probability per outcome (last_price/100)
# ---------------------------------------------------------------------------

def _price_to_probability(market: dict) -> float | None:
    """Prefer last_price; fall back to mid of yes_bid/yes_ask."""
    last = market.get("last_price")
    if isinstance(last, (int, float)) and last > 0:
        return float(last) / 100.0
    bid = market.get("yes_bid")
    ask = market.get("yes_ask")
    if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) and (bid + ask) > 0:
        return (float(bid) + float(ask)) / 2.0 / 100.0
    return None


def _outcome_label_for_market(market: dict) -> str:
    """Pick the most-likely outcome label Kalshi attaches to a market row."""
    for key in ("yes_sub_title", "subtitle", "title", "ticker"):
        v = market.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def fetch_market_stats(event: EventRequest) -> dict[str, float]:
    """Return ``{outcome_label: implied_probability}`` for the event.

    Tries ``/markets?event_ticker=...`` first (covers multi-outcome events
    where each outcome is its own market). Falls back to a single
    ``/markets/{market_ticker}`` lookup for binary YES/NO markets.

    Returns ``{}`` on any failure — the bot still runs without it.
    """
    if not KALSHI_ENABLED:
        return {}
    if not event.event_ticker and not event.market_ticker:
        return {}

    try:
        client = _get_kalshi()
        snapshot: dict[str, float] = {}

        if event.event_ticker:
            markets = client.get_markets(event_ticker=event.event_ticker, status="open")
            for m in markets:
                label = _outcome_label_for_market(m)
                prob = _price_to_probability(m)
                if label and prob is not None:
                    snapshot[label] = round(prob, 4)

        if snapshot:
            return snapshot

        if not event.market_ticker:
            return {}
        single = client.get_market(event.market_ticker)
        if single:
            prob = _price_to_probability(single)
            if prob is not None:
                if event.outcomes and len(event.outcomes) == 2:
                    return {event.outcomes[0]: prob, event.outcomes[1]: round(1.0 - prob, 4)}
                return {"YES": prob, "NO": round(1.0 - prob, 4)}
        return {}
    except Exception as exc:  # never let Kalshi failures break the forecast
        logger.warning("Kalshi snapshot failed for %s: %s", event.market_ticker or event.event_ticker, exc)
        return {}


# ---------------------------------------------------------------------------
# Prompt — uses canonical AgentPrompts (see agent_prompts.py)
# ---------------------------------------------------------------------------

def _build_event_context(event: EventRequest) -> str:
    """Per-event context prefixed to AgentPrompts.create_user_prompt.

    The canonical AgentPrompts only takes `market_stats`; this block carries
    the rest of the platform payload (ticker, category, close_time, description,
    plus any extra fields like market_data) so the model can use them.
    """
    parts: list[str] = ["EVENT CONTEXT:"]
    if event.event_ticker:
        parts.append(f"  Event ticker: {event.event_ticker}")
    if event.market_ticker:
        parts.append(f"  Market ticker: {event.market_ticker}")
    if event.title:
        parts.append(f"  Title: {event.title}")
    if event.subtitle:
        parts.append(f"  Subtitle: {event.subtitle}")
    if event.category:
        parts.append(f"  Category: {event.category}")
    if event.close_time:
        parts.append(f"  Closes (UTC): {event.close_time}")
    if event.description:
        parts.append(f"\nDescription:\n{event.description}")

    known = {
        "event_ticker", "market_ticker", "title", "subtitle", "description",
        "category", "rules", "close_time", "outcomes", "markets",
    }
    extras = {
        k: v for k, v in event.model_dump().items()
        if k not in known and v is not None
    }
    if extras:
        parts.append(
            "\nAdditional context from the platform:\n"
            + json.dumps(extras, indent=2, default=str)
        )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Response extraction + probability normalization
# ---------------------------------------------------------------------------

def _extract_final_text(response: anthropic.types.Message) -> str:
    texts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(texts).strip()


def _normalize_probabilities(
    raw: list[dict[str, Any]],
    expected_markets: list[str],
) -> list[MarketProbability]:
    """Clamp + label-align probabilities, but DO NOT renormalize to sum=1.

    For multi-outcome events, each probability is treated as the independent
    probability of that outcome resolving YES — outcomes are not assumed
    mutually exclusive. The PA evaluator handles per-outcome Brier scoring,
    so a renormalization here would distort the model's calibrated estimates.

    Still applied: clamp to [0, 1], auto-rescale 0-100 → 0-1, case-insensitive
    realignment to the expected label set, and fill missing labels with 0.0.
    """
    by_market: dict[str, float] = {}
    for item in raw:
        market = str(item["market"])
        probability = float(item["probability"])
        if probability > 1.0:
            probability /= 100.0
        by_market[market] = max(0.0, min(1.0, probability))

    if expected_markets and not any(m in by_market for m in expected_markets):
        lower = {k.lower(): v for k, v in by_market.items()}
        by_market = {
            m: lower[m.lower()] for m in expected_markets if m.lower() in lower
        }

    aligned = (
        [(m, by_market.get(m, 0.0)) for m in expected_markets]
        if expected_markets
        else list(by_market.items())
    )
    aligned = [pair for pair in aligned if pair[0]]
    if not aligned:
        raise ValueError("no usable probabilities after alignment")

    return [MarketProbability(market=m, probability=p) for m, p in aligned]


def _uniform_fallback(markets: list[str], reason: str) -> PredictionResponse:
    n = max(len(markets), 1)
    return PredictionResponse(
        probabilities=[
            MarketProbability(market=m, probability=1.0 / n) for m in markets
        ],
        rationale=f"Uniform fallback: {reason}",
    )


# ---------------------------------------------------------------------------
# Forecast
# ---------------------------------------------------------------------------

def forecast(event: EventRequest) -> PredictionResponse:
    client = _get_client()
    markets = _event_markets(event)
    market_stats = fetch_market_stats(event)
    logger.info(
        "predict %s outcomes=%d kalshi_stats=%d",
        event.market_ticker or event.event_ticker or "<no-ticker>",
        len(markets),
        len(market_stats),
    )

    # Canonical AgentPrompts (UnifiedProphetArena) wrapped with extra event
    # context. The system prompt is verbatim AgentPrompts.create_task_prompt;
    # we append event metadata (category, close_time, extras) and the canonical
    # user prompt as the user message.
    system_prompt = AgentPrompts.create_task_prompt(
        event_title=event.title or "the event",
        market_names=markets,
        rules=event.rules or event.description,
        avoid_market_search=False,
    )
    user_prompt = _build_event_context(event) + "\n\n" + AgentPrompts.create_user_prompt(
        market_stats=market_stats or None,
    )

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": DEFAULT_WEB_SEARCH_MAX_USES,
                }
            ],
        )
    except anthropic.APIError as exc:
        logger.exception("Anthropic API error for %s", event.market_ticker)
        raise HTTPException(status_code=502, detail=f"upstream LLM error: {exc}") from exc

    text = _extract_final_text(response)
    if not text:
        logger.warning("%s: empty response", event.market_ticker)
        return _uniform_fallback(markets, "empty model response")

    try:
        parsed = parse_agent_response(text, markets)
        # AgentPrompts emits a {market: prob} dict; convert to the wire-format
        # list-of-dicts the hackathon expects, then renormalize to sum to 1.
        raw_list = [{"market": m, "probability": p} for m, p in parsed["probabilities"].items()]
        probabilities = _normalize_probabilities(raw_list, markets)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "%s: parse failure (%s); raw=%r",
            event.market_ticker or event.event_ticker, exc, text[:500],
        )
        return _uniform_fallback(markets, f"parse failure: {exc}")

    rationale = parsed.get("rationale") or None

    return PredictionResponse(probabilities=probabilities, rationale=rationale)


# ---------------------------------------------------------------------------
# HTTP endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root() -> dict[str, Any]:
    return {
        "service": "prophet-arena-forecast-agent",
        "model": DEFAULT_MODEL,
        "web_search_max_uses": DEFAULT_WEB_SEARCH_MAX_USES,
        "kalshi_enabled": KALSHI_ENABLED,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": DEFAULT_MODEL}


@app.post("/predict", response_model=PredictionResponse)
def predict_endpoint(event: EventRequest) -> PredictionResponse:
    logger.info(
        "predict %s: %s",
        event.market_ticker or event.event_ticker or "<no-ticker>",
        event.title or "<no-title>",
    )
    return forecast(event)


# Defensive aliases: some submission flows POST to the root or to /forecast
# without a /predict suffix. Route them to the same handler so a missing path
# segment doesn't 405.
@app.post("/", response_model=PredictionResponse)
def predict_root(event: EventRequest) -> PredictionResponse:
    return predict_endpoint(event)


@app.post("/forecast", response_model=PredictionResponse)
def predict_forecast_alias(event: EventRequest) -> PredictionResponse:
    return predict_endpoint(event)


def main() -> None:
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
