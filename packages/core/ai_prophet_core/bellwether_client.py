"""Bellwether API client for cross-platform market data.

Provides VWAP prices, market depth, and reportability scores
aggregated across Polymarket and Kalshi.
"""

from __future__ import annotations

import logging
import time

import httpx

from .bellwether_models import BellwetherEventMetrics, BellwetherSearchResponse

logger = logging.getLogger(__name__)

DEFAULT_BELLWETHER_URL = "https://bellwether.live"


class BellwetherAPIError(Exception):
    """Error communicating with the Bellwether API."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class BellwetherClient:
    """Sync HTTP client for the Bellwether public API.

    GET-only, with short timeouts and simple retry logic.

    Example::

        with BellwetherClient() as client:
            results = client.search_markets("senate 2026")
            metrics = client.get_event_metrics("SENATE_2026")
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BELLWETHER_URL,
        timeout: int = 10,
        max_retries: int = 2,
        retry_backoff: float = 0.5,
        api_key: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.api_key = api_key

        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        )

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        """Issue a GET with retry logic."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.get(path, params=params)
                if response.status_code >= 500:
                    last_error = BellwetherAPIError(
                        f"Server error {response.status_code}: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                    if attempt < self.max_retries - 1:
                        time.sleep(self.retry_backoff * (2 ** attempt))
                        continue
                    raise last_error
                if response.status_code >= 400:
                    raise BellwetherAPIError(
                        f"Client error {response.status_code}: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                return response
            except httpx.TimeoutException as e:
                last_error = BellwetherAPIError(f"Timeout: {e}")
            except httpx.TransportError as e:
                last_error = BellwetherAPIError(f"Transport error: {e}")
            except BellwetherAPIError:
                raise
            if attempt < self.max_retries - 1:
                time.sleep(self.retry_backoff * (2 ** attempt))
        raise last_error or BellwetherAPIError(
            f"Request failed after {self.max_retries} attempts"
        )

    def search_markets(
        self,
        query: str,
        category: str | None = None,
        limit: int = 5,
    ) -> BellwetherSearchResponse:
        """Search Bellwether for markets matching *query*.

        Args:
            query: Free-text search query (e.g. market question).
            category: Optional category filter.
            limit: Maximum results to return.

        Returns:
            Parsed search response with matching markets.
        """
        params: dict[str, str | int] = {"q": query, "limit": limit}
        if category:
            params["category"] = category

        response = self._get("/api/search", params=params)
        return BellwetherSearchResponse.model_validate(response.json())

    def get_event_metrics(self, ticker: str) -> BellwetherEventMetrics:
        """Fetch cross-platform metrics for a single event.

        The ticker is converted to a URL-safe slug (lower-case, hyphens).

        Args:
            ticker: Bellwether event ticker (e.g. ``"SENATE_2026"``).

        Returns:
            Parsed event metrics including VWAP, platform prices, depth.
        """
        slug = ticker.lower().replace("_", "-")
        response = self._get(f"/api/events/{slug}/metrics")
        return BellwetherEventMetrics.model_validate(response.json())

    def close(self) -> None:
        """Close the underlying HTTP connection pool."""
        self.client.close()

    def __enter__(self) -> BellwetherClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()
