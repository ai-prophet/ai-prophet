"""Prophet Arena forecast agent — Opus 4.7 + native web search.

Exposes ``POST /predict`` matching the contract that ``prophet forecast predict``
sends. The request body mirrors ``ai_prophet_core.forecast.schemas.Event`` so any
field the dataset / server attaches (including future market-data extensions)
flows through to the LLM prompt without code changes.

Submission flow:
    prophet forecast register --team-name <name> --endpoint-url \
        https://<this-render-service>.onrender.com/predict

Local run:
    ANTHROPIC_API_KEY=sk-ant-... uvicorn main:app --host 0.0.0.0 --port 8000
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

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

app = FastAPI(title="Prophet Arena Forecast Agent (Opus 4.7 + Web Search)")


# ---------------------------------------------------------------------------
# Wire contract — mirrors ai_prophet_core.forecast.schemas
# Keep field names identical so any payload extensions (e.g. market_data) flow
# through automatically.
# ---------------------------------------------------------------------------

class EventRequest(BaseModel):
    """Incoming event payload from `prophet forecast predict`.

    Extra fields are allowed and forwarded to the prompt so the bot can use
    market data, orderbook snapshots, or other context added later upstream
    without redeploying.
    """

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
# Claude (Opus 4.7) with native web_search tool
# ---------------------------------------------------------------------------

DEFAULT_MODEL = os.environ.get("FORECAST_MODEL", "claude-opus-4-7")
DEFAULT_MAX_TOKENS = int(os.environ.get("FORECAST_MAX_TOKENS", "3000"))
DEFAULT_WEB_SEARCH_MAX_USES = int(os.environ.get("FORECAST_WEB_SEARCH_MAX_USES", "5"))
DEFAULT_TEMPERATURE = float(os.environ.get("FORECAST_TEMPERATURE", "0.2"))

SYSTEM_PROMPT = """\
You are an expert forecaster competing on Prophet Arena. Your sole job is to
produce well-calibrated probability distributions over event outcomes.

PROCESS
1. Read the event carefully — identify what is being asked, the resolution
   criteria, and the set of valid outcomes.
2. Use the `web_search` tool to gather the most recent and reliable evidence
   (news, official sources, market data) bearing on the question. Prefer
   primary sources. Search multiple angles when uncertain.
3. Reason explicitly about base rates, recency, source quality, and what could
   make you wrong.
4. Produce calibrated probabilities.

CALIBRATION RULES
- Probabilities must be decimals in [0, 1] and sum to 1.0 across the listed
  outcomes.
- Use the EXACT outcome labels provided in the event. Do not invent labels,
  collapse, or split them.
- Extremes (p < 0.05 or p > 0.95) require very strong evidence.
- When evidence is weak or conflicting, regress toward the base rate / uniform.
- For binary markets where only one outcome is listed, return that outcome's
  probability (the YES probability).

OUTPUT FORMAT
Your FINAL message must be ONLY a single JSON object — no prose around it —
in exactly this shape:

{"probabilities": [{"market": "<exact outcome label>", "probability": <float>}, ...],
 "rationale": "<one short paragraph summarizing the key evidence and your reasoning>"}

Do not wrap the JSON in markdown fences. Do not emit any text after the JSON.
"""


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def _event_markets(event: EventRequest) -> list[str]:
    if event.outcomes:
        return list(event.outcomes)
    return [event.market_ticker]


def _build_user_prompt(event: EventRequest) -> str:
    parts: list[str] = []
    parts.append(f"Event ticker: {event.event_ticker}")
    parts.append(f"Market ticker: {event.market_ticker}")
    parts.append(f"Title: {event.title}")
    if event.subtitle:
        parts.append(f"Subtitle: {event.subtitle}")
    parts.append(f"Category: {event.category}")
    parts.append(f"Close time (UTC): {event.close_time}")
    if event.description:
        parts.append(f"\nDescription:\n{event.description}")
    if event.rules:
        parts.append(f"\nResolution rules:\n{event.rules}")

    markets = _event_markets(event)
    parts.append("\nValid outcome labels (use EXACTLY these strings):")
    for m in markets:
        parts.append(f"  - {m}")

    # Forward any extra fields (e.g. market_data, orderbook) attached upstream.
    known = {
        "event_ticker", "market_ticker", "title", "subtitle", "description",
        "category", "rules", "close_time", "outcomes",
    }
    extras = {
        k: v for k, v in event.model_dump().items()
        if k not in known and v is not None
    }
    if extras:
        parts.append("\nAdditional context provided by the platform:")
        parts.append(json.dumps(extras, indent=2, default=str))

    parts.append(
        "\nResearch the question using web_search as needed, then output the "
        "final JSON object as specified."
    )
    return "\n".join(parts)


def _extract_final_text(response: anthropic.types.Message) -> str:
    """Concatenate all text blocks; Claude's final answer is in the last one(s)."""
    texts = [
        block.text
        for block in response.content
        if getattr(block, "type", None) == "text"
    ]
    return "\n".join(texts).strip()


def _parse_json_object(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        # Strip ```json ... ``` fence if Claude added one despite instructions
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
    """Coerce, clamp, normalize-to-1, and align to expected outcome set."""
    expected = list(expected_markets)
    by_market: dict[str, float] = {}
    for item in raw:
        market = str(item["market"])
        probability = float(item["probability"])
        if probability > 1.0:  # tolerate model emitting 0-100
            probability /= 100.0
        by_market[market] = max(0.0, min(1.0, probability))

    # If the model used outcome labels that match (case-insensitive), realign
    if expected and not any(m in by_market for m in expected):
        lower = {k.lower(): v for k, v in by_market.items()}
        by_market = {
            m: lower[m.lower()] for m in expected if m.lower() in lower
        }

    aligned = (
        [(m, by_market.get(m, 0.0)) for m in expected]
        if expected
        else list(by_market.items())
    )
    aligned = [pair for pair in aligned if pair[0]]
    if not aligned:
        raise ValueError("no usable probabilities after normalization")

    total = sum(p for _m, p in aligned)
    if total <= 0:
        # uniform fallback rather than failing
        n = len(aligned)
        aligned = [(m, 1.0 / n) for m, _ in aligned]
        total = 1.0

    return [
        MarketProbability(market=m, probability=p / total) for m, p in aligned
    ]


def _uniform_fallback(markets: list[str], reason: str) -> PredictionResponse:
    n = max(len(markets), 1)
    return PredictionResponse(
        probabilities=[
            MarketProbability(market=m, probability=1.0 / n) for m in markets
        ],
        rationale=f"Uniform fallback: {reason}",
    )


def forecast(event: EventRequest) -> PredictionResponse:
    client = _get_client()
    markets = _event_markets(event)

    try:
        response = client.messages.create(
            model=DEFAULT_MODEL,
            max_tokens=DEFAULT_MAX_TOKENS,
            temperature=DEFAULT_TEMPERATURE,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(event)}],
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
        logger.warning("%s: empty response, falling back to uniform", event.market_ticker)
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
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "model": DEFAULT_MODEL}


@app.post("/predict", response_model=PredictionResponse)
def predict_endpoint(event: EventRequest) -> PredictionResponse:
    logger.info("predict %s: %s", event.market_ticker, event.title)
    return forecast(event)


def main() -> None:
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=False,
    )


if __name__ == "__main__":
    main()
