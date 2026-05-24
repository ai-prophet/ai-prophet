"""Public-market consensus signal for the ensemble.

Two lookup paths, tried in order by :func:`market_signal_estimate`:

1. **Kalshi by ticker** — the competition's events come from Kalshi, so the
   ``market_ticker`` on each event is a direct key into Kalshi's public API
   at ``/trade-api/v2/markets/{ticker}``. No auth required for reads. When
   we get a hit we know the prices we see are for the exact same question,
   so we trust this signal with ``strategy="market_price"`` and
   ``confidence=0.65``.

2. **Polymarket by fuzzy title match** — fallback when the Kalshi ticker
   lookup fails (404, rate-limit, etc.) OR the event isn't a Kalshi market
   at all. We pull a page of Polymarket markets and rank by Jaccard overlap
   of normalized title words. Returns ``strategy="market_consensus"`` so
   logs distinguish exact-ticker matches from fuzzy-title matches.

Both return :class:`Estimate` or ``None``. Why this exists: the official
scoring formula is ``(our_brier - market_brier) * completion_rate``.
Matching the market on events where we have no edge guarantees a near-zero
contribution from those events; beating the market on events where our
research finds alpha is then pure upside.

All operations are best-effort. Missing endpoint, network error, parse
error, unexpected JSON shape, missing price field, or no good title match
all return ``None`` — never an exception.
"""

from __future__ import annotations

import base64
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from .strategies.base import Estimate, clamp_probability

logger = logging.getLogger(__name__)

KALSHI_TRADING_URL = "https://trading-api.kalshi.com/trade-api/v2/markets"
"""Main Kalshi trading host. All read endpoints require RSA-PSS-signed
auth (despite being "market data" they're not anonymous). When the auth
env vars are set we use this host; otherwise we fall back to the public
elections endpoint."""

KALSHI_ELECTIONS_URL = "https://api.elections.kalshi.com/trade-api/v2/markets"
"""Public no-auth fallback. Limited to US-political markets but works
without credentials. Used when ``KALSHI_API_KEY_ID``+``KALSHI_PRIVATE_KEY``
are missing OR when trading-api returns 401/403/5xx."""

# Auth env vars (both required for signing):
KALSHI_KEY_ID_ENV = "KALSHI_API_KEY_ID"
KALSHI_PRIVATE_KEY_ENV = "KALSHI_PRIVATE_KEY"

POLYMARKET_URL = os.environ.get(
    "POLYMARKET_API_URL",
    "https://clob.polymarket.com/markets",
)
"""Public Polymarket CLOB markets list. Override via ``POLYMARKET_API_URL``."""

REQUEST_TIMEOUT = 6.0
"""Short timeout — we'd rather skip the signal than block the pipeline."""

MIN_JACCARD = 0.30
"""Minimum word-overlap score (after stopword removal) to accept a Polymarket match."""

MIN_OVERLAP = 3
"""Minimum raw word overlap before scoring — guards against tiny coincidences."""

MAX_MARKETS = 200
"""How many Polymarket markets to pull and scan. Endpoint paginates; this is one page."""

MARKET_CONSENSUS_CONFIDENCE = 0.65
"""Used by both Kalshi exact-ticker hits and Polymarket fuzzy-title hits."""

_STOPWORDS = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for",
        "will", "is", "are", "was", "were", "be", "by", "with", "at",
        "this", "that", "these", "those", "have", "has", "had", "do",
        "does", "did", "as", "from", "it", "its",
    }
)


# ---------------------------------------------------------------------------
# Title matching
# ---------------------------------------------------------------------------


def _normalize(text: str) -> set[str]:
    """Lower-case, strip punctuation, drop stopwords and 1-char tokens."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) >= 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ---------------------------------------------------------------------------
# Polymarket payload parsing
# ---------------------------------------------------------------------------


def _yes_price_from_market(market: dict[str, Any]) -> float | None:
    """Extract the YES outcome price from a Polymarket market record.

    Polymarket response shapes vary between endpoints and time; we try the
    three forms we've observed and return None if none match.
    """
    tokens = market.get("tokens")
    if isinstance(tokens, list):
        for tok in tokens:
            if not isinstance(tok, dict):
                continue
            outcome = str(tok.get("outcome", "")).strip().lower()
            if outcome == "yes":
                try:
                    return float(tok.get("price"))
                except (TypeError, ValueError):
                    return None

    prices = market.get("outcome_prices") or market.get("outcomePrices")
    if isinstance(prices, list) and prices:
        try:
            return float(prices[0])
        except (TypeError, ValueError):
            return None

    last = market.get("lastTradePrice") or market.get("last_trade_price")
    if last is not None:
        try:
            return float(last)
        except (TypeError, ValueError):
            return None

    return None


# ---------------------------------------------------------------------------
# Kalshi: auth + HTTP helpers
# ---------------------------------------------------------------------------


_PRIVATE_KEY_CACHE: dict[str, Any] = {}


def _load_kalshi_private_key():
    """Load + cache the RSA private key from env. Returns ``None`` if missing
    or malformed.

    The PEM may be supplied either as the raw multiline PEM (preferred) or
    as a base64-encoded blob of the same. Falls back gracefully on parse
    errors so the rest of the pipeline isn't blocked.
    """
    raw = os.environ.get(KALSHI_PRIVATE_KEY_ENV, "").strip()
    if not raw:
        return None

    cached = _PRIVATE_KEY_CACHE.get(raw)
    if cached is not None:
        return cached

    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
    except Exception as exc:  # noqa: BLE001
        logger.warning("cryptography lib not available: %s", exc)
        return None

    pem_bytes: bytes
    if "-----BEGIN" in raw:
        pem_bytes = raw.encode()
    else:
        # Maybe base64-wrapped — try decoding once.
        try:
            pem_bytes = base64.b64decode(raw)
        except Exception:  # noqa: BLE001
            logger.warning("KALSHI_PRIVATE_KEY is neither PEM nor base64")
            return None

    try:
        key = load_pem_private_key(pem_bytes, password=None)
    except Exception as exc:  # noqa: BLE001
        logger.warning("KALSHI_PRIVATE_KEY parse failed: %s", exc)
        return None

    _PRIVATE_KEY_CACHE[raw] = key
    return key


def _kalshi_auth_headers(method: str, url: str) -> dict[str, str] | None:
    """Build the Kalshi RSA-PSS signed auth headers for one request.

    Per Kalshi docs, the signed string is ``f"{ts_ms}{METHOD}{PATH}"`` —
    PATH being the URL's path component only (no host, no query string).
    Returns ``None`` if either auth env var is missing or the private key
    can't be loaded.
    """
    key_id = os.environ.get(KALSHI_KEY_ID_ENV, "").strip()
    if not key_id:
        logger.info(
            "kalshi auth skipped: %s env var not set (will hit elections fallback)",
            KALSHI_KEY_ID_ENV,
        )
        return None

    private_key = _load_kalshi_private_key()
    if private_key is None:
        logger.info(
            "kalshi auth skipped: %s missing or unparseable (will hit elections fallback)",
            KALSHI_PRIVATE_KEY_ENV,
        )
        return None

    try:
        from cryptography.exceptions import UnsupportedAlgorithm
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
    except Exception as exc:  # noqa: BLE001
        logger.warning("cryptography lib import failed: %s", exc)
        return None

    parsed = urlparse(url)
    path = parsed.path or "/"
    timestamp_ms = str(int(time.time() * 1000))
    msg = (timestamp_ms + method.upper() + path).encode()

    try:
        signature = private_key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
    except (UnsupportedAlgorithm, Exception) as exc:  # noqa: BLE001
        logger.warning("kalshi signing failed: %s", exc)
        return None

    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
    }


def _kalshi_get(url: str, params: dict[str, Any] | None = None) -> dict | list | None:
    """GET a Kalshi URL with auth if configured, fall back to elections on 401/403/5xx.

    Returns the parsed JSON body on success, ``None`` on any failure.
    Logs the response body on 401/403 so we can diagnose auth issues.
    """
    try:
        headers = _kalshi_auth_headers("GET", url)
        # When we have auth headers, send Content-Type too — some
        # Kalshi gateways are strict about it even on GET.
        if headers:
            headers = {**headers, "Content-Type": "application/json"}
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(url, params=params)
        if resp.status_code in (401, 403):
            # Surface the actual rejection reason — critical for diagnosing
            # signature/key-permission failures.
            body_snippet = (resp.text or "")[:300].replace("\n", " ")
            logger.info(
                "kalshi %s on %s — body=%r (auth_attempted=%s)",
                resp.status_code,
                url,
                body_snippet,
                headers is not None and "KALSHI-ACCESS-KEY" in (headers or {}),
            )
        if resp.status_code in (401, 403) or resp.status_code >= 500:
            # Auth or server failure on trading-api → try elections.
            if KALSHI_TRADING_URL in url:
                fallback_url = url.replace(KALSHI_TRADING_URL, KALSHI_ELECTIONS_URL)
                logger.info(
                    "kalshi trading-api %s; falling back to elections %s",
                    resp.status_code,
                    fallback_url,
                )
                with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                    resp = client.get(fallback_url, params=params)
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.info("kalshi GET failed url=%s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Kalshi: direct ticker lookup (primary)
# ---------------------------------------------------------------------------


def _kalshi_yes_price_cents(market: dict[str, Any]) -> int | float | None:
    """Pick the most-current YES price (in cents) from a Kalshi market record.

    Order of preference:
      1. ``last_price`` — most recent trade, if it's a sensible 0 < p < 100.
      2. Midpoint of ``yes_bid`` / ``yes_ask`` — current order book.
      3. ``yes_ask`` alone — upper-bound when only one side is quoted.
      4. ``yes_bid`` alone — lower-bound.
    """
    last = market.get("last_price")
    if last is not None:
        try:
            v = float(last)
        except (TypeError, ValueError):
            v = None
        if v is not None and 0.0 < v < 100.0:
            return v

    bid = market.get("yes_bid")
    ask = market.get("yes_ask")
    try:
        bid_v = float(bid) if bid is not None else None
        ask_v = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        bid_v = ask_v = None

    if bid_v is not None and ask_v is not None and bid_v > 0 and ask_v > 0:
        return (bid_v + ask_v) / 2.0
    if ask_v is not None and ask_v > 0:
        return ask_v
    if bid_v is not None and bid_v > 0:
        return bid_v
    return None


def kalshi_price_estimate(
    market_ticker: str | None,
    title: str | None = None,
) -> Estimate | None:
    """Look up the current Kalshi market price by ticker and return an Estimate.

    Hits ``KALSHI_API_URL/{market_ticker}``. Returns ``None`` on any failure
    (empty ticker, network error, non-200, malformed payload, no usable
    price field). Prices are in cents on Kalshi; we divide by 100 to map to
    a probability.

    ``title`` is unused for the lookup itself (the ticker is exact) but is
    included in the rationale for traceability.
    """
    if not market_ticker:
        return None
    ticker = str(market_ticker).strip()
    if not ticker:
        return None

    # ``KALSHI_API_URL`` override beats auto-selection (used in tests).
    # Default: trading-api (signed auth when KALSHI_API_KEY_ID +
    # KALSHI_PRIVATE_KEY are set; auto-falls back to elections on 401/5xx).
    base = os.environ.get("KALSHI_API_URL") or KALSHI_TRADING_URL
    url = f"{base.rstrip('/')}/{ticker}"
    payload = _kalshi_get(url)
    if payload is None:
        logger.info("kalshi lookup failed ticker=%s", ticker)
        return None

    # Kalshi wraps the record as {"market": {...}}; tolerate a bare dict too.
    if isinstance(payload, dict) and "market" in payload:
        market = payload.get("market")
    else:
        market = payload
    if not isinstance(market, dict):
        logger.info("kalshi payload not a market dict ticker=%s", ticker)
        return None

    yes_cents = _kalshi_yes_price_cents(market)
    if yes_cents is None:
        logger.info("kalshi market has no usable price ticker=%s", ticker)
        return None

    p_yes = clamp_probability(yes_cents / 100.0)
    cents_str = f"{yes_cents:.0f}c" if yes_cents == int(yes_cents) else f"{yes_cents:.1f}c"
    rationale = f"Kalshi market price: {cents_str} (as of query time)"
    if title:
        rationale += f" for '{title[:80]}'"
    return Estimate(
        p_yes=p_yes,
        rationale=rationale,
        strategy="market_price",
        confidence=MARKET_CONSENSUS_CONFIDENCE,
    )


# ---------------------------------------------------------------------------
# Polymarket: fuzzy title match (fallback)
# ---------------------------------------------------------------------------


def query_polymarket(title: str, *, max_markets: int = MAX_MARKETS) -> dict | None:
    """Return the best-matching Polymarket market dict for ``title``, or None."""
    if not title:
        return None

    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(POLYMARKET_URL, params={"limit": max_markets})
        resp.raise_for_status()
        payload = resp.json()
    except Exception as exc:  # noqa: BLE001 — never block pipeline
        logger.info("polymarket request failed: %s", exc)
        return None

    # Some Polymarket endpoints wrap the list in {"data": [...]}; others
    # return a bare list.
    if isinstance(payload, dict):
        markets = payload.get("data") or payload.get("markets") or []
    elif isinstance(payload, list):
        markets = payload
    else:
        return None

    if not isinstance(markets, list) or not markets:
        return None

    event_words = _normalize(title)
    if not event_words:
        return None

    best: dict | None = None
    best_score = 0.0
    for m in markets:
        if not isinstance(m, dict):
            continue
        question = (
            m.get("question")
            or m.get("title")
            or m.get("market_slug")
            or ""
        )
        market_words = _normalize(str(question))
        if not market_words:
            continue
        if len(event_words & market_words) < MIN_OVERLAP:
            continue
        score = _jaccard(event_words, market_words)
        if score > best_score:
            best_score = score
            best = m

    if best is None or best_score < MIN_JACCARD:
        return None
    return best


def market_consensus_estimate(title: str) -> Estimate | None:
    """Look up the best-matching public market and return it as an Estimate.

    Returns ``None`` on every failure mode — network error, no match, missing
    price, unexpected payload shape.
    """
    market = query_polymarket(title)
    if market is None:
        return None

    yes_price = _yes_price_from_market(market)
    if yes_price is None:
        logger.info("polymarket match found but no usable price field")
        return None

    question = market.get("question") or market.get("title") or "(unknown)"
    return Estimate(
        p_yes=clamp_probability(yes_price),
        rationale=(
            f"Polymarket consensus: '{question}' trading at YES "
            f"{yes_price:.3f}"
        ),
        strategy="market_consensus",
        confidence=MARKET_CONSENSUS_CONFIDENCE,
    )


# ---------------------------------------------------------------------------
# Dispatcher: Kalshi first, then Polymarket fallback
# ---------------------------------------------------------------------------


def market_signal_estimate(
    market_ticker: str | None,
    title: str | None,
) -> Estimate | None:
    """Try Kalshi by ticker; fall back to Polymarket by fuzzy title.

    This is the single entrypoint the pipeline calls. Hierarchy:

    1. If ``market_ticker`` is set, try Kalshi's direct lookup. Exact match
       to the question we're scoring — most trustworthy.
    2. If Kalshi misses (404, network, malformed, no price) AND ``title`` is
       set, try Polymarket's fuzzy title match — useful when the event came
       from somewhere other than Kalshi, or when Kalshi is down.
    3. If both miss, return ``None``. The ensemble proceeds without a
       market-consensus signal.
    """
    if market_ticker:
        est = kalshi_price_estimate(market_ticker, title)
        if est is not None:
            return est
    if title:
        return market_consensus_estimate(title)
    return None


# ---------------------------------------------------------------------------
# Kalshi: multi-outcome (event-level) child-markets lookup
# ---------------------------------------------------------------------------


def kalshi_event_markets(event_ticker: str | None) -> list[dict[str, Any]] | None:
    """Fetch all child markets for an event from Kalshi.

    Multi-outcome events on Kalshi have one **event_ticker** with N
    **child markets**, each its own binary YES/NO question — e.g.
    ``KXAAAGASD-26MAY24`` (event) has children ``KXAAAGASD-26MAY24-T0.10``,
    ``KXAAAGASD-26MAY24-T0.15``, etc. The endpoint
    ``GET /markets?event_ticker=<ticker>`` returns them all in one call.

    Returns the list of market dicts (each containing ``ticker``,
    ``subtitle``/``yes_sub_title``/``title`` for matching, and price
    fields ``last_price``/``yes_bid``/``yes_ask``). Returns ``None`` on
    any failure — caller falls back to other paths.
    """
    if not event_ticker:
        return None
    ticker = str(event_ticker).strip()
    if not ticker:
        return None

    base = os.environ.get("KALSHI_API_URL") or KALSHI_TRADING_URL
    payload = _kalshi_get(
        base.rstrip("/"),
        params={"event_ticker": ticker, "limit": 1000},
    )
    if payload is None:
        logger.info("kalshi event lookup failed event=%s", ticker)
        return None

    # Kalshi wraps the list as {"markets": [...]} typically.
    if isinstance(payload, dict):
        markets = payload.get("markets")
    elif isinstance(payload, list):
        markets = payload
    else:
        return None

    if not isinstance(markets, list) or not markets:
        logger.info("kalshi event=%s returned no markets", ticker)
        return None

    return markets


def _market_label(market: dict[str, Any]) -> str:
    """Pull the most outcome-descriptive label from a Kalshi child market.

    Kalshi child markets carry the outcome description in several fields
    depending on event type. We try the most-specific first and fall
    back through generic ones.
    """
    for field in ("yes_sub_title", "subtitle", "sub_title", "title", "ticker"):
        v = market.get(field)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def kalshi_multi_outcome_probabilities(
    event_ticker: str | None, outcomes: list[str]
) -> list[dict[str, Any]] | None:
    """For a multi-outcome event, fetch child markets and align prices
    to the input ``outcomes`` list.

    Returns ``[{"market": str, "probability": float}, ...]`` aligned
    positionally to ``outcomes`` if at least one outcome was matched
    to a Kalshi child market with a usable price; otherwise ``None``.

    Matching strategy per outcome:
      1. Build a normalized token set for the outcome string.
      2. For each child market, build a token set from
         ``yes_sub_title``/``subtitle``/``title``.
      3. Pick the child whose tokens have the highest Jaccard overlap,
         provided the overlap is at least ``MIN_OVERLAP`` shared tokens.
      4. Use the child's ``yes_*`` price as that outcome's probability.

    Outcomes without a match get ``0.5`` (uninformative prior). The
    output is independent (no sum normalization) per the eval's
    independent-binary-YES scoring rule.
    """
    if not event_ticker or not outcomes:
        return None

    markets = kalshi_event_markets(event_ticker)
    if not markets:
        return None

    # Pre-compute label, ticker, and token set for each child market.
    market_meta: list[tuple[dict[str, Any], str, str, set[str]]] = []
    for m in markets:
        if not isinstance(m, dict):
            continue
        label = _market_label(m)
        ticker = str(m.get("ticker") or "")
        tokens = _normalize(label) if label else set()
        # Even labels with no usable tokens (e.g. just a number) are kept,
        # since substring/ticker matches can still find them.
        market_meta.append((m, label.lower(), ticker.lower(), tokens))

    if not market_meta:
        logger.info(
            "kalshi event=%s returned only malformed child markets",
            event_ticker,
        )
        return None

    def _match_outcome(outcome_str: str) -> dict[str, Any] | None:
        """Find the best child market for ``outcome_str``.

        Three strategies, in order of strictness:
          1. **Jaccard token overlap** — highest score wins if any tokens
             overlap. Good for "Boston Celtics" → "Boston Celtics".
          2. **Substring match** — case-insensitive, in either direction.
             Catches "Increase" → "Approval rating increase".
          3. **Ticker suffix match** — outcome string appears in the
             ticker (case-insensitive, stripped of non-alphanumerics).
             Catches "0.10" → "EVT-T0.10".
        """
        outcome_lower = outcome_str.lower().strip()
        outcome_tokens = _normalize(outcome_str)

        # Strategy 1: Jaccard
        best: dict[str, Any] | None = None
        best_score = 0.0
        if outcome_tokens:
            for m, _label, _ticker, m_tokens in market_meta:
                if not m_tokens:
                    continue
                if len(outcome_tokens & m_tokens) < 1:
                    continue
                score = _jaccard(outcome_tokens, m_tokens)
                if score > best_score:
                    best_score = score
                    best = m
        if best is not None and best_score >= 0.25:
            return best

        # Strategy 2: substring (case-insensitive, either direction)
        if outcome_lower:
            for m, label, _ticker, _tokens in market_meta:
                if not label:
                    continue
                if outcome_lower in label or label in outcome_lower:
                    return m

        # Strategy 3: ticker-suffix match (loose)
        # E.g. outcome "0.10" matches ticker "EVT-T0.10"; outcome "Yes"
        # matches ticker "EVT-YES".
        normalized_outcome = re.sub(r"[^a-z0-9]", "", outcome_lower)
        if normalized_outcome:
            for m, _label, ticker, _tokens in market_meta:
                normalized_ticker = re.sub(r"[^a-z0-9]", "", ticker)
                if normalized_outcome and normalized_outcome in normalized_ticker:
                    return m

        # Strategy 1 backup: even a weak Jaccard match is better than nothing.
        return best

    matched_count = 0
    out: list[dict[str, Any]] = []
    for outcome in outcomes:
        outcome_str = str(outcome)
        best_market = _match_outcome(outcome_str)

        prob = 0.5
        if best_market is not None:
            yes_cents = _kalshi_yes_price_cents(best_market)
            if yes_cents is not None:
                prob = clamp_probability(yes_cents / 100.0)
                matched_count += 1

        out.append({"market": outcome_str, "probability": round(prob, 4)})

    if matched_count == 0:
        # Diagnostic: show what we were trying to match against, so the
        # next iteration can tune the matcher properly.
        sample_meta = [
            f"ticker={t!r} label={lab!r}"
            for _m, lab, t, _tok in market_meta[:5]
        ]
        logger.info(
            "kalshi event=%s matched 0/%d outcomes to child markets "
            "(outcomes=%r ; first_children=%s)",
            event_ticker,
            len(outcomes),
            outcomes[:5],
            sample_meta,
        )
        return None

    logger.info(
        "kalshi event=%s matched %d/%d outcomes (children=%d)",
        event_ticker,
        matched_count,
        len(outcomes),
        len(market_meta),
    )
    return out


__all__ = [
    "MARKET_CONSENSUS_CONFIDENCE",
    "MIN_JACCARD",
    "MIN_OVERLAP",
    "kalshi_event_markets",
    "kalshi_multi_outcome_probabilities",
    "kalshi_price_estimate",
    "market_consensus_estimate",
    "market_signal_estimate",
    "query_polymarket",
]
