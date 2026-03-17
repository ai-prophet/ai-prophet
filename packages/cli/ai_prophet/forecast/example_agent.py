"""Example forecast agent server.

A minimal FastAPI agent that receives events from ``prophet forecast predict``
and returns calibrated probability estimates using Claude.

Usage:
    # Install deps (if not already):  pip install fastapi uvicorn anthropic
    # Start the server:
    python -m ai_prophet.forecast.example_agent

    # In another terminal:
    prophet forecast predict --events events.json --agent-url http://localhost:8000/predict --team-name my-team
"""

from __future__ import annotations

import logging
import os

import anthropic
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="Example Forecast Agent")

# ---------------------------------------------------------------------------
# Request / Response models (match the CLI's predict contract)
# ---------------------------------------------------------------------------

class EventRequest(BaseModel):
    event_ticker: str
    market_ticker: str
    title: str
    subtitle: str | None = None
    description: str | None = None
    category: str
    rules: str | None = None
    close_time: str


class PredictionResponse(BaseModel):
    p_yes: float
    rationale: str


# ---------------------------------------------------------------------------
# Claude-based forecasting
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an expert forecaster specialized in calibrated probability estimation.

Your task is to estimate the probability that the given event resolves YES.

CALIBRATION GUIDELINES:
- Consider base rates for similar events.
- Weight evidence by reliability and recency.
- Account for uncertainty — don't be overconfident.
- Extremes (p < 0.10 or p > 0.90) require very strong evidence.

Respond with ONLY valid JSON: {"p_yes": <float 0.01-0.99>, "rationale": "<2-3 sentences>"}
Do not include any other text outside the JSON object."""


def _build_user_prompt(event: EventRequest) -> str:
    parts = [f"Event: {event.title}"]
    if event.subtitle:
        parts.append(f"Subtitle: {event.subtitle}")
    if event.description:
        parts.append(f"Description: {event.description}")
    if event.rules:
        parts.append(f"Rules: {event.rules}")
    parts.append(f"Category: {event.category}")
    parts.append(f"Close time: {event.close_time}")
    parts.append(
        "\nBased on your knowledge, what is the probability this resolves YES?"
    )
    return "\n".join(parts)


_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to .env and source it, "
                "or export it directly: export ANTHROPIC_API_KEY=sk-ant-..."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def forecast_with_claude(event: EventRequest) -> PredictionResponse:
    """Call Claude to produce a probability forecast for the event."""
    import json

    client = _get_client()
    response = client.messages.create(
        model=os.environ.get("FORECAST_MODEL", "claude-sonnet-4-20250514"),
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _build_user_prompt(event)}],
    )

    text = response.content[0].text.strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]

    data = json.loads(text)
    p = max(0.01, min(0.99, float(data["p_yes"])))
    return PredictionResponse(p_yes=p, rationale=data.get("rationale", ""))


# ---------------------------------------------------------------------------
# Local predict function (used by: prophet forecast predict --local)
# ---------------------------------------------------------------------------

def predict(event: dict) -> dict:
    """Predict function for --local mode.

    Args:
        event: Event dict with keys like market_ticker, title, category, etc.

    Returns:
        Dict with p_yes (float) and rationale (str).
    """
    event_req = EventRequest(**event)
    resp = forecast_with_claude(event_req)
    return {"p_yes": resp.p_yes, "rationale": resp.rationale}


# ---------------------------------------------------------------------------
# Server endpoint (used by: prophet forecast predict --agent-url)
# ---------------------------------------------------------------------------

@app.post("/predict", response_model=PredictionResponse)
async def predict_endpoint(event: EventRequest) -> PredictionResponse:
    """Receive an event and return a probability forecast."""
    logger.info("Forecasting %s: %s", event.market_ticker, event.title)
    return forecast_with_claude(event)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    uvicorn.run(
        "ai_prophet.forecast.example_agent:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )


if __name__ == "__main__":
    main()
