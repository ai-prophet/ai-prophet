"""Multi-provider LLM client with automatic fallback.

Supports three providers, configured via environment variables:

* ``GROQ_API_KEY``        — Groq (free, fast Llama 3.3 70B)
* ``OPENROUTER_API_KEY``  — OpenRouter (gives access to many premium models)
* ``ANTHROPIC_API_KEY``   — Anthropic direct (Claude Sonnet)

The :func:`call_llm` entry point picks an ordered chain of providers based
on the requested ``tier``:

* ``tier="research"``  — Groq → OpenRouter → Anthropic (cheap, fast queries)
* ``tier="reasoning"`` — OpenRouter → Anthropic → Groq (higher quality)

Failures (missing key, network error, bad status, malformed body) are caught
and the next provider in the chain is tried. If every provider fails the
final exception is re-raised.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Provider configuration -----------------------------------------------------

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "anthropic/claude-sonnet-4")

ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

DEFAULT_TIMEOUT = float(os.environ.get("LLM_TIMEOUT_SECONDS", "20"))
RATE_LIMIT_RETRY_DELAY = float(os.environ.get("LLM_RATE_LIMIT_DELAY_SECONDS", "10"))

_PROVIDER_CHAINS: dict[str, list[str]] = {
    # Both tiers now lead with OpenRouter (Claude Sonnet 4) so every
    # analyst call benefits from Claude's calibration. Groq remains as
    # the last-resort fallback in case OpenRouter (and Anthropic, when
    # an ``ANTHROPIC_API_KEY`` is set) both fail — the endpoint never
    # goes silent. Originally the ``research`` tier led with Groq for
    # cost, but the leaderboard analysis showed Llama-driven analysts
    # (base_rate, contrarian) were dragging the ensemble Brier.
    "research": ["openrouter", "anthropic", "groq"],
    "reasoning": ["openrouter", "anthropic", "groq"],
}


_dotenv_loaded = False


def _ensure_env_loaded() -> None:
    """Load variables from ``.env`` once per process, if dotenv is available."""
    global _dotenv_loaded
    if _dotenv_loaded:
        return
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 — best effort
        pass
    _dotenv_loaded = True


# Provider implementations ---------------------------------------------------


def _call_openai_compatible(
    *,
    base_url: str,
    api_key: str,
    model: str,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
    timeout: float,
    extra_headers: dict[str, str] | None = None,
) -> str:
    """Hit any OpenAI-compatible ``/chat/completions`` endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(f"{base_url}/chat/completions", headers=headers, json=payload)
    resp.raise_for_status()
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError(f"Empty choices from {base_url}")
    content = choices[0].get("message", {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"Empty content from {base_url}")
    return content


def _call_groq(system: str, user: str, temperature: float, max_tokens: int) -> str:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set")
    return _call_openai_compatible(
        base_url=GROQ_BASE_URL,
        api_key=api_key,
        model=GROQ_MODEL,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=DEFAULT_TIMEOUT,
    )


def _call_openrouter(system: str, user: str, temperature: float, max_tokens: int) -> str:
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    extra_headers = {
        "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://github.com/ai-prophet"),
        "X-Title": os.environ.get("OPENROUTER_TITLE", "ai-prophet ensemble agent"),
    }
    return _call_openai_compatible(
        base_url=OPENROUTER_BASE_URL,
        api_key=api_key,
        model=OPENROUTER_MODEL,
        system=system,
        user=user,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=DEFAULT_TIMEOUT,
        extra_headers=extra_headers,
    )


def _call_anthropic(system: str, user: str, temperature: float, max_tokens: int) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover — anthropic is in deps
        raise RuntimeError("anthropic package not installed") from exc

    client = anthropic.Anthropic(api_key=api_key, timeout=DEFAULT_TIMEOUT)
    response = client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if not response.content:
        raise RuntimeError("Empty content from Anthropic")
    text = response.content[0].text
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("Empty text from Anthropic")
    return text


_DISPATCH = {
    "groq": _call_groq,
    "openrouter": _call_openrouter,
    "anthropic": _call_anthropic,
}


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Return True if ``exc`` represents a 429 from any supported provider."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code == 429
    try:
        import anthropic

        if isinstance(exc, anthropic.RateLimitError):
            return True
    except ImportError:
        pass
    return False


def _call_with_rate_limit_retry(
    provider: str,
    fn: Any,
    system: str,
    user: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """Invoke a provider, retrying once after a delay on HTTP 429.

    If the retry also fails (rate-limited or otherwise) the exception is
    propagated so the outer dispatch loop falls through to the next provider.
    """
    try:
        return fn(system, user, temperature, max_tokens)
    except Exception as exc:  # noqa: BLE001
        if not _is_rate_limit_error(exc):
            raise
        logger.warning(
            "llm.rate_limit provider=%s sleeping=%.1fs before retry",
            provider,
            RATE_LIMIT_RETRY_DELAY,
        )
        time.sleep(RATE_LIMIT_RETRY_DELAY)
        return fn(system, user, temperature, max_tokens)


# Public API -----------------------------------------------------------------


class LLMError(RuntimeError):
    """Raised when every provider in the chain has failed."""


def call_llm(
    system: str,
    user: str,
    *,
    tier: str = "research",
    temperature: float = 0.3,
    max_tokens: int = 800,
) -> str:
    """Call an LLM, automatically falling back across providers.

    Args:
        system: System prompt.
        user: User prompt.
        tier: ``"research"`` (Groq-first) or ``"reasoning"`` (OpenRouter-first).
        temperature: Sampling temperature.
        max_tokens: Maximum tokens to generate.

    Returns:
        The raw text completion.

    Raises:
        LLMError: If every provider in the chosen chain fails.
    """
    _ensure_env_loaded()

    chain = _PROVIDER_CHAINS.get(tier)
    if chain is None:
        raise ValueError(f"Unknown tier: {tier!r}")

    last_error: Exception | None = None
    for provider in chain:
        fn = _DISPATCH[provider]
        try:
            logger.debug("llm.call provider=%s tier=%s", provider, tier)
            return _call_with_rate_limit_retry(
                provider, fn, system, user, temperature, max_tokens
            )
        except Exception as exc:  # noqa: BLE001
            logger.info("llm.call provider=%s failed: %s", provider, exc)
            last_error = exc
            continue

    raise LLMError(
        f"All providers failed for tier={tier!r}; last error: {last_error}"
    ) from last_error


_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```$", re.DOTALL | re.IGNORECASE)


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def _extract_json_object(text: str) -> str:
    """Best-effort: pull the first balanced JSON object out of ``text``."""
    start = text.find("{")
    if start < 0:
        return text
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return text


def call_llm_json(
    system: str,
    user: str,
    *,
    tier: str = "research",
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """Call an LLM and parse the response as JSON.

    Strips markdown code fences and tolerates extra prose around a single JSON
    object before parsing.
    """
    raw = call_llm(
        system,
        user,
        tier=tier,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    candidate = _strip_fences(raw)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        salvaged = _extract_json_object(candidate)
        return json.loads(salvaged)


# ---------------------------------------------------------------------------
# Smart routing: fast-resolve for already-settled Sports events
# ---------------------------------------------------------------------------

# Phrases that suggest a sports result has been reported in the research.
_RESULT_KEYWORDS = (
    " won ", " wins ", " winner ", " victory ", " defeat", " defeated",
    " beat ", " beats ", " loses to ", " lost to ", "final score",
    "final result", "knockout", "advances to", "eliminated",
)

# Score patterns like "115-94", "3 - 1", "115–94" (em-dash also tolerated).
_SCORE_PATTERN = re.compile(r"\b\d{1,3}\s*[-–]\s*\d{1,3}\b")

_FAST_RESOLVE_CONFIDENCE = 0.95
"""Confidence for fast-resolved events. Near-max because we're literally
quoting a reported result, but not 1.0 so the ensemble math is still safe
if the LLM mis-identifies which match the research is about."""

_FAST_RESOLVE_SYSTEM_PROMPT = """You decide whether a Sports prediction-market
question has already been settled by a result quoted in the research brief.

The user prompt provides:
* The question (e.g. "Will Cleveland beat Detroit in NBA Game 6?").
* OUTCOMES naming which side maps to YES and which to NO.
* A research brief.

Your procedure:
1. Look in the research for a definitive result of the SPECIFIC match the
   question asks about — same teams, same competition, same date if given.
2. If you find it, set ``settled: true`` and ``p_yes`` near 0.97 if the
   YES side won, or near 0.03 if the NO side won.
3. If the research mentions a result for the wrong match/date, or the
   result is ambiguous, or you'd be guessing, set ``settled: false`` and
   ``p_yes: 0.5``. The caller will then run the full forecasting pipeline.

Be conservative: false-positive fast-resolves are catastrophic for Brier.

Respond with ONLY a JSON object:
{"settled": true|false, "p_yes": <float 0.01-0.99>,
 "rationale": "<one short sentence quoting the specific match/score>"}

No prose, no markdown fences, no commentary."""


def _research_has_result_signal(research: str) -> bool:
    """Cheap pre-filter: does the research mention a result at all?"""
    if not research:
        return False
    lower = research.lower()
    if _SCORE_PATTERN.search(research):
        return True
    return any(kw in lower for kw in _RESULT_KEYWORDS)


def fast_resolve(event: Any, research: str) -> Any | None:
    """Skip the full pipeline for Sports events whose result is already public.

    For events where category == "Sports" AND the research brief mentions a
    result-like signal (score pattern, "won"/"beat"/etc.), make one cheap
    LLM call that decides whether the brief actually settles the question.
    Returns an :class:`Estimate` with confidence 0.95 if so, else ``None``.

    Returning ``None`` means "no fast-resolve; run the full pipeline" — the
    caller treats both "not applicable" and "ambiguous" the same way.
    """
    # Lazy import to avoid a circular dependency at module load.
    from .strategies.base import Estimate, clamp_probability

    category = (getattr(event, "category", None) or "").strip().lower()
    if category != "sports":
        return None

    if not _research_has_result_signal(research):
        return None

    title = getattr(event, "title", "") or ""
    outcomes = getattr(event, "outcomes", None) or []

    lines = [f"Question: {title}"]
    if isinstance(outcomes, list) and len(outcomes) >= 2:
        lines.append(f"OUTCOMES: YES = {outcomes[0]}, NO = {outcomes[1]}")
    lines.append("")
    lines.append("Research brief:")
    lines.append((research or "")[:3000])  # cap input — LLM doesn't need 8K
    lines.append("")
    lines.append(
        "Has THIS specific match been settled? If yes, who won? "
        "Return JSON only."
    )

    try:
        data = call_llm_json(
            _FAST_RESOLVE_SYSTEM_PROMPT,
            "\n".join(lines),
            tier="research",
            temperature=0.0,
            max_tokens=300,
        )
    except Exception as exc:  # noqa: BLE001 — never break the pipeline
        logger.info("fast_resolve LLM call failed: %s", exc)
        return None

    try:
        settled = bool(data.get("settled", False))
        p_raw = float(data["p_yes"])
        rationale = str(data.get("rationale", "")).strip() or "(no rationale)"
    except (KeyError, TypeError, ValueError) as exc:
        logger.info("fast_resolve bad payload: %s", exc)
        return None

    # Refuse to short-circuit if the LLM came back uncertain.
    if not settled or 0.4 <= p_raw <= 0.6:
        return None

    return Estimate(
        p_yes=clamp_probability(p_raw),
        rationale=f"Fast-resolve: {rationale}",
        strategy="fast_resolve",
        confidence=_FAST_RESOLVE_CONFIDENCE,
    )
