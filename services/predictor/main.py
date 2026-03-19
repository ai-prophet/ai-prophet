"""Prediction microservice for LLM-based market analysis.

Stateless FastAPI service that accepts a model spec and market info,
calls the appropriate LLM provider, and returns a parsed prediction.
Designed for deployment on Google Cloud Run.

Usage (local):
    uvicorn main:app --port 8080
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    from instance_config import env_suffix, normalize_instance_name
except ModuleNotFoundError:
    # Keep Cloud Run source deploys self-contained when sibling modules are
    # outside the uploaded build context.
    def normalize_instance_name(instance_name: str | None) -> str:
        value = (instance_name or "").strip()
        return value or "Haifeng"


    def env_suffix(instance_name: str | None) -> str:
        normalized = normalize_instance_name(instance_name)
        return "".join(ch if ch.isalnum() else "_" for ch in normalized.upper())

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="AI Prophet Predictor", version="1.0.0")

PREDICTOR_API_KEY = os.getenv("PREDICTOR_API_KEY", "")
PREDICTOR_TIMEOUT_SEC = float(os.getenv("PREDICTOR_TIMEOUT_SEC", "180"))


# ── Request / Response schemas ────────────────────────────────────

class PredictRequest(BaseModel):
    model_spec: str  # e.g. "gemini:gemini-3.1-pro-preview:market"
    market_info: dict  # {title, subtitle?, category?, yes_ask, no_ask}
    instance_name: str | None = None


class PredictResponse(BaseModel):
    p_yes: float
    confidence: float
    reasoning: str
    analysis: dict = {}
    sources: list[dict] = []


# ── Endpoints ─────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/predict", response_model=PredictResponse)
def predict(req: PredictRequest, x_api_key: str = Header(default="")):
    if PREDICTOR_API_KEY and x_api_key != PREDICTOR_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        result = run_prediction(req.model_spec, req.market_info, req.instance_name)
        return PredictResponse(**result)
    except Exception as e:
        logger.error("Prediction failed for %s: %s", req.model_spec, e)
        raise HTTPException(status_code=500, detail=str(e))


# ── Prompt building ───────────────────────────────────────────────

def _build_prompts(market_info: dict, include_market_prices: bool = False) -> tuple[str, str]:
    """Build system and user prompts for market prediction."""
    title = market_info.get("title", "")

    system = f"""You are an AI assistant specialized in analyzing and predicting real-world events.
You have deep expertise in predicting the outcome of the event: "{title}"

Note that this event occurs in the future. Your goal is to provide well-reasoned predictions.
You will be predicting the probability (as a float value from 0 to 1) of ONLY the following possible outcome:
- {title}

IMPORTANT CONSTRAINTS:
1. You MUST ONLY provide a probability for the exact outcome listed above
2. Ensure your probability is between 0 and 1

Your response MUST be in JSON format with the following structure:
```json
{{
    "rationale": "<short_concise_3_sentence_rationale>",
    "probabilities": {{
        "{title}": <probability_value_from_0_to_1>
    }}
}}
```

In the rationale, provide a short, concise, 3 sentence rationale that explains:
- How you weighed different pieces of information
- Your reasoning for the probability you assigned
- Any key factors or uncertainties you considered"""

    if include_market_prices:
        yes_ask = market_info.get("yes_ask", 0.5)
        no_ask = market_info.get("no_ask", 0.5)
        market_stats = json.dumps({"YES": yes_ask, "NO": no_ask}, indent=2)
        user = f"""CURRENT ONLINE TRADING DATA:
You also have access to the predicted outcome probability from a prediction market:
{market_stats}

Note: Market data can provide insights into the current consensus influenced by traders of various beliefs and private information. However, you should not rely on market data alone.

Please analyze the event and provide your prediction following the specified format."""
    else:
        user = f"""Please analyze the event "{title}" and provide your prediction following the specified format.

Use your knowledge and any available information to form an independent probability estimate."""

    return system, user


# ── Response parsing ──────────────────────────────────────────────

def _parse_prediction(content: str) -> dict:
    """Extract prediction JSON from LLM response."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    start = text.find("{")
    end = text.rfind("}") + 1
    if start >= 0 and end > start:
        result = json.loads(text[start:end])
    else:
        result = json.loads(text)

    p_yes = 0.5
    probs = result.get("probabilities", {})
    if probs:
        p_yes = float(next(iter(probs.values())))
    elif "p_yes" in result:
        p_yes = float(result["p_yes"])

    return {
        "p_yes": p_yes,
        "confidence": float(result.get("confidence", 0.5)),
        "reasoning": result.get("rationale", result.get("reasoning", "")),
        "analysis": result.get("analysis", {}),
        "sources": result.get("sources", []),
    }


# ── Provider clients (lazy-init, reused within container) ─────────

_openai_client = None
_anthropic_client = None
_gemini_http_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        import openai
        _openai_client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    return _openai_client


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    return _anthropic_client


def _get_gemini_http_client():
    global _gemini_http_client
    if _gemini_http_client is None:
        _gemini_http_client = httpx.Client(timeout=PREDICTOR_TIMEOUT_SEC)
    return _gemini_http_client


# ── Provider prediction functions ─────────────────────────────────

def _predict_openai(model_name: str, market_info: dict, include_market: bool) -> dict:
    client = _get_openai_client()
    system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.2,
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    return _parse_prediction(response.choices[0].message.content)


def _predict_anthropic(model_name: str, market_info: dict, include_market: bool) -> dict:
    client = _get_anthropic_client()
    system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)
    response = client.messages.create(
        model=model_name,
        max_tokens=800,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    return _parse_prediction(response.content[0].text)


def _instance_gemini_key(instance_name: str | None) -> str:
    normalized = normalize_instance_name(instance_name)
    suffix = env_suffix(normalized)
    return (
        os.getenv(f"GOOGLE_API_KEY_{suffix}", "")
        or os.getenv(f"GEMINI_API_KEY_{suffix}", "")
    )


def _predict_gemini(
    model_name: str,
    market_info: dict,
    include_market: bool,
    instance_name: str | None,
) -> dict:
    api_key = _instance_gemini_key(instance_name)
    if not api_key:
        normalized = normalize_instance_name(instance_name)
        suffix = env_suffix(normalized)
        raise ValueError(
            f"GOOGLE_API_KEY_{suffix} or GEMINI_API_KEY_{suffix} env var required"
        )

    http_client = _get_gemini_http_client()
    system_prompt, user_prompt = _build_prompts(market_info, include_market_prices=include_market)

    body: dict = {
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {},
        "tools": [{"googleSearch": {}}],
    }

    if "gemini-3" in model_name:
        body["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "high"}

    base_url = "https://generativelanguage.googleapis.com/v1beta"
    url = f"{base_url}/models/{model_name}:generateContent?key={api_key}"

    t0 = time.time()
    response = http_client.post(url, json=body)
    elapsed = time.time() - t0
    response.raise_for_status()
    data = response.json()

    candidates = data.get("candidates", [])
    if not candidates:
        raise ValueError(f"Gemini returned no candidates: {data}")

    parts = candidates[0].get("content", {}).get("parts", [])
    # Exclude thought parts (thinking-mode internal reasoning) — response is in non-thought parts
    text = "".join(p.get("text", "") for p in parts if not p.get("thought", False))
    if not text.strip():
        finish_reason = candidates[0].get("finishReason", "unknown")
        raise ValueError(f"Gemini returned empty response text (finishReason={finish_reason})")

    sources: list[dict] = []
    try:
        grounding_meta = candidates[0].get("groundingMetadata", {})
        for chunk in grounding_meta.get("groundingChunks", []):
            web = chunk.get("web", {})
            uri = web.get("uri", "")
            if uri:
                sources.append({"url": uri, "title": web.get("title", uri)})
    except Exception:
        pass

    logger.info("Gemini API call took %.1fs (%d sources)", elapsed, len(sources))
    result = _parse_prediction(text)
    result["sources"] = sources
    return result


# ── Main prediction router ────────────────────────────────────────

def run_prediction(model_spec: str, market_info: dict, instance_name: str | None = None) -> dict:
    """Parse model spec and dispatch to the right provider."""
    parts = model_spec.split(":")
    if len(parts) >= 3:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = parts[2].lower() in ("market", "mkt", "prices")
    elif len(parts) == 2:
        provider = parts[0].lower()
        model_name = parts[1]
        include_market = False
    else:
        provider, model_name = "openai", parts[0]
        include_market = False

    if provider == "openai":
        return _predict_openai(model_name, market_info, include_market)
    elif provider in ("anthropic", "claude"):
        return _predict_anthropic(model_name, market_info, include_market)
    elif provider in ("gemini", "google"):
        return _predict_gemini(model_name, market_info, include_market, instance_name)
    else:
        raise ValueError(f"Unsupported LLM provider: {provider}")
