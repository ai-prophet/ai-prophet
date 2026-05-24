"""Web research module for the ensemble forecasting agent.

Given an event, this module:

1. Generates 5 diverse search queries via an LLM.
2. Hits DuckDuckGo's HTML endpoint for each query.
3. Extracts clean text from each result URL with trafilatura.
4. Compiles a single research brief (~8K chars) for downstream strategies.

Every step is wrapped in best-effort error handling — if search or extraction
fails for one URL we move on and return whatever we managed to gather. The
function never raises during normal operation; callers receive a (possibly
empty) string.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import date
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .llm_utils import call_llm_json

logger = logging.getLogger(__name__)

DDG_HTML_URL = "https://html.duckduckgo.com/html/"

# DDG fingerprints aggressively. Empirically Chrome-on-Windows and Safari-on-Mac
# UAs reliably return search results; Firefox-on-Windows and curl get the 202
# "anomaly detected" page. Rotate per request to spread the fingerprint.
DDG_USER_AGENTS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
)
DEFAULT_USER_AGENT = DDG_USER_AGENTS[0]

SEARCH_TIMEOUT = 8.0
FETCH_TIMEOUT = 5.0
RESEARCH_BRIEF_MAX_CHARS = 8000

# Throughput knobs. Empirically the 4th and 5th query rarely add new evidence —
# the first three (current state, recent news, contrarian angle) dominate. And
# the 3rd result per query is usually a forum repost. Keeping the brief lean
# also keeps the strategies' input tokens down.
MAX_SEARCH_QUERIES = 3
MAX_SEARCH_RESULTS = 2

# Per-event burst control. DDG starts throttling after ~5-10 rapid queries from
# the same IP. A jittered inter-query pause is much cheaper than burning every
# query after the first burst.
DDG_INTERQUERY_DELAY = (0.4, 1.1)  # uniform seconds
DDG_THROTTLE_BACKOFF = 5.0  # seconds before retrying a 202/403/429
DDG_THROTTLE_STATUSES = {202, 403, 429}


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

_QUERY_SYSTEM_PROMPT = """You generate web search queries for a forecasting analyst.

Given a binary prediction-market question, output 3 diverse search queries that
together would give a well-informed forecaster a strong view on the likely
outcome. Each query should target a distinct angle: current status / recent
news, historical base rate or expert commentary, and contradictory or risk-
oriented information.

If the user prompt provides a CATEGORY HINT, treat it as authoritative
guidance about which kinds of sources to look for. Generate queries that
will surface those source types specifically.

Respond with ONLY a JSON object of the form:
{"queries": ["query 1", "query 2", "query 3"]}

No prose, no markdown fences, no commentary."""


# Category-specific search guidance. The matching hint is injected into the
# query-generation prompt so the LLM produces category-appropriate queries
# (scores/standings for Sports, Fed statements for Economics, polls for
# Politics, etc.). Keys match by exact case AND case-insensitive title-case
# normalization, so "sports", "Sports", and "SPORTS" all resolve.
CATEGORY_HINTS: dict[str, str] = {
    "Sports": (
        "Search for recent game scores, standings, player injuries, "
        "head-to-head records, and betting odds."
    ),
    "Economics": (
        "Search for central bank statements, economic indicators, market "
        "data, analyst forecasts, and policy announcements."
    ),
    "Politics": (
        "Search for recent polls, legislative votes, political analysis, "
        "official statements, and election data."
    ),
    "Technology": (
        "Search for product announcements, company earnings, industry "
        "reports, and expert analysis."
    ),
    "Science": (
        "Search for research publications, expert commentary, "
        "institutional announcements, and peer review status."
    ),
    "Crypto": (
        "Search for current prices, trading volume, regulatory news, "
        "on-chain metrics, and market sentiment."
    ),
    "Weather": (
        "Search for official weather forecasts, historical climate data, "
        "and meteorological service predictions."
    ),
    "Mentions": (
        "Search for recent public statements, speeches, press conferences, "
        "social media posts, and media appearances by the person mentioned."
    ),
    "Other": (
        "Search for recent news, official announcements, regulatory filings, "
        "and expert analysis related to the topic."
    ),
}

DEFAULT_CATEGORY_HINT = (
    "Search for authoritative recent sources on the topic, expert "
    "commentary, historical precedents for similar questions, and any "
    "evidence that contradicts the consensus view."
)


def hint_for_category(category: str | None) -> str:
    """Return the category-specific search hint, or the generic fallback.

    Lookup is case-insensitive against :data:`CATEGORY_HINTS` keys, so
    ``"sports"``, ``"Sports"``, and ``"SPORTS"`` all resolve to the same hint.
    Unknown or missing categories receive :data:`DEFAULT_CATEGORY_HINT`.
    """
    if not category:
        return DEFAULT_CATEGORY_HINT
    normalized = category.strip().title()
    return CATEGORY_HINTS.get(normalized, DEFAULT_CATEGORY_HINT)


def _build_query_user_prompt(
    title: str, description: str | None, category: str | None
) -> str:
    """Compose the user prompt fed to the query-generation LLM call."""
    parts = [f"Event title: {title}"]
    if category:
        parts.append(f"Category: {category}")
    if description:
        parts.append(f"Description: {description}")
    parts.append(f"Today: {date.today().isoformat()}")
    parts.append(f"\nCATEGORY HINT: {hint_for_category(category)}")
    parts.append(
        f"\nReturn {MAX_SEARCH_QUERIES} distinct, well-formed search queries "
        "that, together, would ground a forecast on this event. Honor the "
        "CATEGORY HINT — pick query phrasings that will surface those "
        "specific source types."
    )
    return "\n".join(parts)


def generate_search_queries(
    title: str,
    description: str | None = None,
    category: str | None = None,
) -> list[str]:
    """Use an LLM to produce a small set of diverse search queries for the event."""
    user_prompt = _build_query_user_prompt(title, description, category)

    try:
        data = call_llm_json(
            _QUERY_SYSTEM_PROMPT,
            user_prompt,
            tier="research",
            temperature=0.4,
            max_tokens=400,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("query generation failed: %s — falling back to defaults", exc)
        return _fallback_queries(title, category)

    queries = data.get("queries") if isinstance(data, dict) else None
    if not isinstance(queries, list):
        return _fallback_queries(title, category)

    cleaned = []
    for q in queries:
        if isinstance(q, str) and q.strip():
            cleaned.append(q.strip())
        if len(cleaned) == MAX_SEARCH_QUERIES:
            break

    if not cleaned:
        return _fallback_queries(title, category)
    return cleaned


def _fallback_queries(title: str, category: str | None) -> list[str]:
    base = title.strip()
    suffix = f" {category}" if category else ""
    candidates = [
        base,
        f"{base} latest news",
        f"{base} forecast {date.today().year}",
        f"{base} odds prediction",
        f"{base}{suffix} analysis",
    ]
    return candidates[:MAX_SEARCH_QUERIES]


# ---------------------------------------------------------------------------
# DuckDuckGo HTML search
# ---------------------------------------------------------------------------

_RESULT_BLOCK_RE = re.compile(
    r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?(?:<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?',
    re.DOTALL | re.IGNORECASE,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    return unescape(_TAG_RE.sub("", html)).strip()


def _normalize_ddg_url(url: str) -> str:
    """DDG wraps outgoing links in /l/?uddg=...; unwrap them."""
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if parsed.path.endswith("/l/") or "/l/?" in url:
        qs = parse_qs(parsed.query)
        target = qs.get("uddg") or qs.get("u")
        if target:
            return unquote(target[0])
    return url


def _ddg_request(query: str, *, user_agent: str, method: str) -> httpx.Response | None:
    """Issue a single DDG request; return the response or None on transport error."""
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.7",
    }
    try:
        with httpx.Client(
            timeout=SEARCH_TIMEOUT,
            headers=headers,
            follow_redirects=True,
        ) as client:
            if method == "POST":
                return client.post(DDG_HTML_URL, data={"q": query})
            return client.get(DDG_HTML_URL, params={"q": query})
    except Exception as exc:  # noqa: BLE001
        logger.info("ddg transport error q=%r method=%s: %s", query, method, exc)
        return None


def search_duckduckgo(
    query: str, max_results: int = MAX_SEARCH_RESULTS
) -> list[dict[str, str]]:
    """Search DDG's HTML endpoint and return up to ``max_results`` results.

    Each result is a dict with ``url``, ``title``, ``snippet``. On any failure
    an empty list is returned.

    Resilience strategy:
        1. Pick a fresh UA per call (rotates the fingerprint).
        2. Try POST first; if the response is a throttle code, sleep
           ``DDG_THROTTLE_BACKOFF`` seconds and fall back to GET.
        3. Return empty on transport errors or final non-OK status — callers
           treat that as "no research for this query" and move on.
    """
    user_agent = random.choice(DDG_USER_AGENTS)
    resp = _ddg_request(query, user_agent=user_agent, method="POST")

    # On throttle, back off and try the other verb with a fresh UA. DDG appears
    # to apply different limits to POST vs GET, so verb-switching often clears
    # us through.
    if resp is not None and resp.status_code in DDG_THROTTLE_STATUSES:
        logger.info(
            "ddg throttle q=%r status=%d; sleeping %.1fs then retrying via GET",
            query,
            resp.status_code,
            DDG_THROTTLE_BACKOFF,
        )
        time.sleep(DDG_THROTTLE_BACKOFF)
        resp = _ddg_request(
            query, user_agent=random.choice(DDG_USER_AGENTS), method="GET"
        )

    if resp is None or resp.status_code != 200:
        status = resp.status_code if resp is not None else "no-response"
        logger.info("ddg search failed q=%r status=%s", query, status)
        return []

    html = resp.text
    out: list[dict[str, str]] = []
    for match in _RESULT_BLOCK_RE.finditer(html):
        raw_url = match.group(1)
        url = _normalize_ddg_url(raw_url)
        if not url.startswith(("http://", "https://")):
            continue
        title = _strip_tags(match.group(2) or "")
        snippet = _strip_tags(match.group(3) or "")
        if not title:
            continue
        out.append({"url": url, "title": title, "snippet": snippet})
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------------------
# Page content extraction
# ---------------------------------------------------------------------------


def fetch_page_content(url: str, max_chars: int = 2000) -> str:
    """Fetch ``url`` and return clean text, truncated to ``max_chars``.

    Returns an empty string on any failure.
    """
    try:
        import trafilatura
    except ImportError:  # pragma: no cover — trafilatura is in deps
        logger.warning("trafilatura not available; skipping page fetch")
        return ""

    try:
        with httpx.Client(
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": DEFAULT_USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.info("fetch failed url=%s: %s", url, exc)
        return ""

    try:
        text = trafilatura.extract(
            resp.text,
            include_comments=False,
            include_tables=False,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("trafilatura extract failed url=%s: %s", url, exc)
        return ""

    if not text:
        return ""
    cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rsplit(" ", 1)[0] + "…"
    return cleaned


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


def research_event(
    title: str,
    description: str | None = None,
    category: str | None = None,
    rules: str | None = None,
    *,
    max_results_per_query: int = MAX_SEARCH_RESULTS,
    max_chars: int = RESEARCH_BRIEF_MAX_CHARS,
) -> str:
    """Run the full research pipeline and return a compiled brief.

    Never raises. Returns whatever was gathered, even if every step failed.
    """
    queries = generate_search_queries(title, description, category)
    logger.info("research.queries n=%d", len(queries))

    sections: list[str] = []
    header_parts = [
        f"# Research brief for: {title}",
        f"Today's date: {date.today().isoformat()}",
    ]
    if category:
        header_parts.append(f"Category: {category}")
    if rules:
        header_parts.append(f"Rules: {rules.strip()}")
    sections.append("\n".join(header_parts))
    sections.append("Search queries used:\n" + "\n".join(f"  - {q}" for q in queries))

    seen_urls: set[str] = set()
    snippet_buf: list[str] = []
    body_buf: list[str] = []

    for i, query in enumerate(queries):
        # Small jittered pause between queries within a single event so DDG
        # doesn't see 5 back-to-back requests and trip its anomaly detector.
        if i > 0:
            time.sleep(random.uniform(*DDG_INTERQUERY_DELAY))
        results = search_duckduckgo(query, max_results=max_results_per_query)
        if not results:
            continue
        snippet_buf.append(f"\n## Query: {query}")
        for r in results:
            url = r["url"]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            snippet_buf.append(f"- [{r['title']}]({url})\n  {r['snippet']}")
            content = fetch_page_content(url, max_chars=1800)
            if content:
                body_buf.append(
                    f"\n### Source: {r['title']}\nURL: {url}\n\n{content}"
                )

    if snippet_buf:
        sections.append("# Search results" + "\n".join(snippet_buf))
    if body_buf:
        sections.append("# Extracted source content" + "\n".join(body_buf))

    brief = "\n\n".join(sections).strip()
    if len(brief) > max_chars:
        brief = brief[:max_chars].rsplit("\n", 1)[0] + "\n…[truncated]"
    return brief


__all__ = [
    "generate_search_queries",
    "search_duckduckgo",
    "fetch_page_content",
    "research_event",
]
