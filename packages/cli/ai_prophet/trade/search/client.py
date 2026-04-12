"""Search client using Brave Search API."""

from __future__ import annotations

import asyncio
import atexit
import gc
import logging
import random
import threading
from collections.abc import Coroutine
from typing import Any

import aiohttp
import trafilatura
from trafilatura.meta import reset_caches

from ai_prophet._version import __version__ as PACKAGE_VERSION
from ai_prophet.trade.core.config import SearchConfig

logger = logging.getLogger(__name__)

BRAVE_API_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"

DEFAULT_HEADERS = {
    "User-Agent": f"ai-prophet/{PACKAGE_VERSION} (+https://prophetarena.co)",
    "Accept-Encoding": "gzip",
    "Accept": "application/json",
}


class SearchClient:
    """Client for searching the web and extracting article content.

    Uses Brave Search API for web search and Trafilatura for content extraction.
    """

    def __init__(
        self,
        api_key: str,
        config: SearchConfig | None = None,
    ) -> None:
        """Initialize search client.

        Args:
            api_key: Brave Search API key
            config: Optional explicit SearchConfig
        """
        if config is None:
            config = SearchConfig()

        self.api_key = api_key
        self.connect_timeout = config.connect_timeout
        self.total_timeout = config.total_timeout
        self.fetch_timeout = config.fetch_timeout
        self.max_retries = 3  # Keep retries hardcoded for robustness
        self.max_concurrent = config.max_concurrent
        self.max_html_bytes = config.max_html_bytes
        self.max_extract_chars = config.max_extract_chars

        # ---------------------------------------------------------------------
        # aiohttp session management
        #
        # IMPORTANT: aiohttp.ClientSession is bound to the event loop it was
        # created on. The benchmark code calls SearchClient.search() from sync
        # code, sometimes creating/destroying loops. To safely reuse a single
        # session (and its connector pool) across calls, we run all async work
        # on a dedicated background event loop thread owned by this SearchClient.
        # ---------------------------------------------------------------------
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_ready = threading.Event()
        self._thread = threading.Thread(target=self._loop_main, name="SearchClientLoop", daemon=True)
        self._thread.start()
        self._loop_ready.wait(timeout=10)

        self._session: aiohttp.ClientSession | None = None
        self._connector: aiohttp.TCPConnector | None = None

        # Best-effort cleanup on interpreter exit
        atexit.register(self.close)

        logger.info(f"SearchClient initialized (timeout={self.total_timeout}s, concurrent={self.max_concurrent})")

    def _loop_main(self) -> None:
        """Background thread entrypoint: run a dedicated asyncio loop forever."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            # If we ever stop, ensure session/connector are cleaned up
            try:
                if self._session and not self._session.closed:
                    loop.run_until_complete(self._session.close())
            except Exception:
                pass
            try:
                if self._connector and not self._connector.closed:
                    maybe_coro = self._connector.close()
                    if asyncio.iscoroutine(maybe_coro):
                        loop.run_until_complete(maybe_coro)
            except Exception:
                pass
            loop.close()

    def _run(self, coro: Coroutine[Any, Any, Any]) -> Any:
        """Run a coroutine on the dedicated loop and return its result (sync)."""
        if not self._loop:
            raise RuntimeError("SearchClient event loop not initialized")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result()

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the long-lived aiohttp session (loop-bound)."""
        if self._session and not self._session.closed:
            return self._session

        # Keep connector pool small -- this is a memory-constrained benchmark box.
        # Size it to the configured concurrency with a small headroom.
        per_host = max(2, int(self.max_concurrent) + 1)
        total = max(4, per_host * 2)

        self._connector = aiohttp.TCPConnector(
            limit=total,
            limit_per_host=per_host,
            ttl_dns_cache=300,
            use_dns_cache=True,
            keepalive_timeout=30,
            force_close=False,
            enable_cleanup_closed=True,
        )

        headers = {
            **DEFAULT_HEADERS,
            "X-Subscription-Token": self.api_key,
        }

        timeout = aiohttp.ClientTimeout(
            total=self.total_timeout,
            connect=self.connect_timeout,
        )

        self._session = aiohttp.ClientSession(
            headers=headers,
            timeout=timeout,
            connector=self._connector,
        )
        return self._session

    def close(self) -> None:
        """Best-effort cleanup of session and background loop."""
        # Idempotent
        loop = self._loop
        if not loop:
            return
        thread = self._thread
        self._loop = None

        try:
            if self._session and not self._session.closed:
                asyncio.run_coroutine_threadsafe(self._session.close(), loop).result(timeout=5)
        except Exception:
            pass

        try:
            if self._connector and not self._connector.closed:
                maybe_coro = self._connector.close()
                if asyncio.iscoroutine(maybe_coro):
                    asyncio.run_coroutine_threadsafe(maybe_coro, loop).result(timeout=5)
        except Exception:
            pass

        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass

        if thread and thread.is_alive() and threading.current_thread() is not thread:
            thread.join(timeout=5)

        self._session = None
        self._connector = None

    async def _get_brave_links(self, query: str, limit: int) -> list[dict[str, Any]]:
        """Fetch search results from Brave API.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of search results with url, title, and snippet
        """
        params: dict[str, str | int] = {
            "q": query,
            "count": limit,
            "country": "US",
            "search_lang": "en",
            "result_filter": "web",
        }

        try:
            session = await self._get_session()
            async with session.get(BRAVE_API_ENDPOINT, params=params) as response:
                response.raise_for_status()
                data = await response.json()

            # Extract results with metadata
            results: list[dict[str, Any]] = []
            for item in data.get("web", {}).get("results", []):
                url = item.get("url")
                if url:
                    results.append({
                        "url": url,
                        "title": item.get("title", ""),
                        "snippet": item.get("description", ""),
                        "score": 1.0 - (len(results) * 0.1)  # Simple relevance score
                    })

            logger.info(f"Found {len(results)} links for query: '{query}'")
            return results[:limit]

        except Exception as e:
            logger.error(f"Brave search failed for query '{query}': {e}")
            return []

    async def _fetch_article_text(self, url: str) -> str | None:
        """Fetch and extract article text from URL.

        Args:
            url: Article URL

        Returns:
            Extracted article text or None if failed
        """
        for attempt in range(self.max_retries):
            try:
                session = await self._get_session()
                async with session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=float(self.fetch_timeout)),
                    allow_redirects=True,
                ) as response:
                    response.raise_for_status()

                    # Read at most max_html_bytes to avoid ballooning RSS on
                    # huge pages. Decode leniently -- we only need "good enough"
                    # HTML for trafilatura.
                    raw = await response.content.read(self.max_html_bytes)
                    html = raw.decode("utf-8", errors="replace")
                    del raw

                    # If we didn't consume the full body, close the connection
                    # immediately. Otherwise the context-manager exit tries to
                    # drain the remainder, which hangs on large pages.
                    if not response.content.at_eof():
                        response.close()

                if not html or len(html) < 10:
                    raise ValueError("Empty response")

                text = trafilatura.extract(
                    html,
                    include_comments=False,
                    no_fallback=True,
                    url=url,
                )
                del html  # free the HTML buffer immediately

                if text:
                    # Truncate early -- the prompt only uses ~1 000 chars.
                    text = text[:self.max_extract_chars]
                    logger.debug(f"Extracted {len(text)} chars from {url[:60]}...")
                    return text
                else:
                    logger.warning(f"No text extracted from {url[:60]}...")
                    return None

            except asyncio.CancelledError:
                raise

            except Exception as e:
                if attempt == self.max_retries - 1:
                    logger.warning(f"Failed to fetch {url[:60]}... after {self.max_retries} attempts: {e}")
                    return None

                backoff = 1.5 * (attempt + 1) + random.random()
                logger.debug(f"Retry {attempt + 1}/{self.max_retries} for {url[:60]}... (sleeping {backoff:.1f}s)")
                await asyncio.sleep(backoff)

        return None

    async def _fetch_articles_parallel(self, urls: list[str]) -> list[str | None]:
        """Fetch multiple articles in parallel with concurrency control.

        Args:
            urls: List of article URLs

        Returns:
            List of extracted texts (None for failed fetches)
        """
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_with_semaphore(url: str) -> str | None:
            async with semaphore:
                return await self._fetch_article_text(url)

        try:
            results = await asyncio.gather(
                *[fetch_with_semaphore(url) for url in urls],
                return_exceptions=True
            )

            texts: list[str | None] = []
            for item in results:
                if isinstance(item, BaseException):
                    texts.append(None)
                else:
                    texts.append(item)
            return texts

        except asyncio.CancelledError:
            raise

    async def search_async(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Search for articles and return extracted content.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of dicts with url, title, snippet, and text
        """
        logger.info(f"Searching for: '{query}' (limit={limit})")

        # Get search results
        results = await self._get_brave_links(query, limit)
        if not results:
            logger.warning(f"No URLs found for query: '{query}'")
            return []

        # Extract URLs
        urls = [r["url"] for r in results]

        # Fetch article texts
        texts = await self._fetch_articles_parallel(urls)
        reset_caches()

        # Combine results with texts
        enriched_results: list[dict[str, Any]] = []
        for result, text in zip(results, texts, strict=False):
            if text:
                result["text"] = text
                enriched_results.append(result)

        # Aggressively reclaim memory: trafilatura/lxml + large HTML strings can
        # create fragmentation in long-running benchmarks. A single GC here is
        # cheaper than OOM-killing the whole box.
        gc.collect()

        logger.info(f"Successfully extracted {len(enriched_results)}/{len(urls)} articles for query: '{query}'")
        return enriched_results

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """Synchronous wrapper for search_async.

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of dicts with url, title, snippet, and text
        """
        return self._run(self.search_async(query, limit))

