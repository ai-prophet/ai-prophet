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
    model_config = ConfigDict(extra="allow")

    event_ticker: str
    market_ticker: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str
    rules: str | None = None
    close_time: str
    outcomes: list[str] | None = None


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
    if event.outcomes:
        return list(event.outcomes)
    return [event.market_ticker]


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

    try:
        client = _get_kalshi()
        snapshot: dict[str, float] = {}

        markets = client.get_markets(event_ticker=event.event_ticker, status="open")
        for m in markets:
            label = _outcome_label_for_market(m)
            prob = _price_to_probability(m)
            if label and prob is not None:
                snapshot[label] = round(prob, 4)

        if snapshot:
            return snapshot

        single = client.get_market(event.market_ticker)
        if single:
            prob = _price_to_probability(single)
            if prob is not None:
                if event.outcomes and len(event.outcomes) == 2:
                    return {event.outcomes[0]: prob, event.outcomes[1]: round(1.0 - prob, 4)}
                return {"YES": prob, "NO": round(1.0 - prob, 4)}
        return {}
    except Exception as exc:  # never let Kalshi failures break the forecast
        logger.warning("Kalshi snapshot failed for %s: %s", event.market_ticker, exc)
        return {}


# ---------------------------------------------------------------------------
# Prompt — ported from ProphetArena PredictionPrompts (prompts.py)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "You are an AI assistant specialized in analyzing and predicting "
    "real-world events. You provide calibrated probability forecasts grounded "
    "in evidence you gather via web_search and in the live prediction-market "
    "trading data provided."
)


def _build_task_prompt(event_title: str, market_names: list[str]) -> str:
    """Port of PredictionPrompts.create_task_prompt."""
    market_list_str = "\n".join(f"- {m}" for m in market_names)
    json_example = ",\n                ".join(
        f'"{m}": <probability_value_from_0_to_1>' for m in market_names
    )
    return f""" You are an AI assistant specialized in analyzing and predicting real-world events.
                You have deep expertise in predicting the outcome of the event: "{event_title}"

                Note that this event occurs in the future. You will be given live market trading data and may use the web_search tool to gather additional sources.
                Based on the collected information, your goal is to extract meaningful insights and provide well-reasoned predictions.
                You will be predicting the probability (as a float value from 0 to 1) of ONLY the following possible outcomes:
                {market_list_str}

                IMPORTANT CONSTRAINTS:
                1. You MUST ONLY provide probabilities for the exact possible outcomes listed above
                2. Do NOT create or invent any additional outcomes
                3. Use exactly the same outcome names as provided (case-sensitive)
                4. Ensure all probabilities are between 0 and 1
                5. Probabilities MUST sum to 1.0 across the listed outcomes

                Your response MUST be in JSON format with the following structure:
                ```json
                {{
                    "probabilities": {{
                        {json_example}
                    }},
                    "rationale": "<text_explaining_your_rationale>",
                    "analysis": {{
                        "sources_used": [
                            {{"title": "Source Title", "url": "https://..."}}
                        ],
                        "evidence_extracted": [
                            {{"source": "Source Name or URL", "evidence": "Specific evidence or data point extracted"}}
                        ],
                        "combination_weighting": "<text justification>",
                        "uncertainties_counterpoints": "<text justification>",
                        "mapping_to_final_probs": "<text justification>"
                    }}
                }}
                ```

                In the rationale section, provide a short, concise, 3 sentence rationale that explains:
                - How you weighed different pieces of information
                - Your reasoning for the probability distribution you assigned
                - Any key factors or uncertainties you considered

                In the analysis section, be extremely specific and detailed. The analysis must be a JSON object with exactly these 5 fields:
                1. sources_used: array of {{title, url}} objects covering every external source you used
                2. evidence_extracted: array of {{source, evidence}} objects citing the specific facts you used from each source
                3. combination_weighting: how you combined evidence across sources, which were weighted most heavily and why
                4. uncertainties_counterpoints: conflicting signals, missing data, caveats
                5. mapping_to_final_probs: how each cited piece of evidence justifies your probability assignments
        """.strip()


def _build_user_prompt(event: EventRequest, market_stats: dict[str, float]) -> str:
    """Port of PredictionPrompts.create_user_prompt + event context."""
    parts: list[str] = []
    parts.append("EVENT CONTEXT:")
    parts.append(f"  Event ticker: {event.event_ticker}")
    parts.append(f"  Market ticker: {event.market_ticker}")
    parts.append(f"  Title: {event.title}")
    if event.subtitle:
        parts.append(f"  Subtitle: {event.subtitle}")
    parts.append(f"  Category: {event.category}")
    parts.append(f"  Closes (UTC): {event.close_time}")
    if event.description:
        parts.append(f"\nDescription:\n{event.description}")
    if event.rules:
        parts.append(f"\nResolution rules:\n{event.rules}")

    # Forward any extra fields the platform attaches.
    known = {
        "event_ticker", "market_ticker", "title", "subtitle", "description",
        "category", "rules", "close_time", "outcomes",
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

    market_stats_info = ""
    if market_stats:
        market_stats_info = f"""
CURRENT ONLINE TRADING DATA:
You have access to the predicted outcome probability (last trading price of each outcome treated as YES probability) from Kalshi at the moment of your prediction:
{json.dumps(market_stats, indent=2)}

Note: Market data reflects the current consensus of traders with diverse beliefs and private information. It is a strong but not definitive signal — combine it with the evidence you gather via web_search to produce a well-calibrated prediction. Do not rely on market data alone.
"""

    parts.append(
        "\nUse the web_search tool to gather recent, reliable sources bearing "
        "on the question. Cite specific sources by URL in your rationale."
    )
    if market_stats_info:
        parts.append(market_stats_info)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# JSON parsing + probability normalization
# ---------------------------------------------------------------------------

def _extract_final_text(response: anthropic.types.Message) -> str:
    texts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(texts).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"No JSON object in response: {text[:300]!r}")
    return json.loads(text[start:end + 1])


def _coerce_probabilities(data: dict[str, Any]) -> list[dict[str, Any]]:
    if "probabilities" not in data:
        raise KeyError("response missing 'probabilities' key")
    raw = data["probabilities"]
    if isinstance(raw, dict):
        return [{"market": k, "probability": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        raise TypeError("'probabilities' must be a list or object")
    return raw


def _normalize_probabilities(
    raw: list[dict[str, Any]],
    expected_markets: list[str],
) -> list[MarketProbability]:
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
        raise ValueError("no usable probabilities after normalization")

    total = sum(p for _m, p in aligned)
    if total <= 0:
        n = len(aligned)
        aligned = [(m, 1.0 / n) for m, _ in aligned]
        total = 1.0

    return [MarketProbability(market=m, probability=p / total) for m, p in aligned]


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
        event.market_ticker,
        len(markets),
        len(market_stats),
    )

    task_prompt = _build_task_prompt(event.title, markets)
    user_prompt = _build_user_prompt(event, market_stats)
    combined = f"{user_prompt}\n\n{task_prompt}"

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=DEFAULT_MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": combined}],
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
        data = _parse_json_object(text)
        raw = _coerce_probabilities(data)
        probabilities = _normalize_probabilities(raw, markets)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        logger.warning(
            "%s: parse failure (%s); raw=%r", event.market_ticker, exc, text[:500]
        )
        return _uniform_fallback(markets, f"parse failure: {exc}")

    rationale = data.get("rationale") if isinstance(data, dict) else None
    if isinstance(rationale, str):
        rationale = rationale.strip() or None
    else:
        rationale = None

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
    logger.info("predict %s: %s", event.market_ticker, event.title)
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
