"""Standalone Kalshi API client for fetching events and markets."""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime
from typing import Any

import requests

from ..betting.config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_BASE_URL_ENV,
    KALSHI_PRIVATE_KEY_B64_ENV,
)

logger = logging.getLogger(__name__)


class KalshiForecastClient:
    """Read-only Kalshi API client for discovering events and markets."""

    def __init__(
        self,
        api_key_id: str = "",
        private_key_base64: str = "",
        *,
        base_url: str = "",
        timeout_sec: int = 30,
    ):
        self._api_key_id = api_key_id or os.getenv(KALSHI_API_KEY_ID_ENV, "")
        self._private_key_base64 = private_key_base64 or os.getenv(
            KALSHI_PRIVATE_KEY_B64_ENV, ""
        )
        self._base_url = (
            base_url
            or os.getenv(KALSHI_BASE_URL_ENV, "")
            or DEFAULT_KALSHI_BASE_URL
        )
        self._timeout = timeout_sec
        self._private_key = None
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Auth (reuses the RSA-PSS signing pattern from betting adapter)
    # ------------------------------------------------------------------

    def _load_private_key(self):
        if self._private_key is not None:
            return self._private_key

        if not self._private_key_base64:
            return None

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        key_bytes = base64.b64decode(self._private_key_base64)
        self._private_key = serialization.load_pem_private_key(
            key_bytes, password=None, backend=default_backend()
        )
        return self._private_key

    def _sign_request(self, method: str, path: str) -> dict[str, str]:
        """Return auth headers, or just Content-Type if no credentials are configured."""
        private_key = self._load_private_key()
        if private_key is None:
            return {"Content-Type": "application/json"}

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp_str = str(int(datetime.now().timestamp() * 1000))
        msg_string = timestamp_str + method.upper() + path

        signature = private_key.sign(
            msg_string.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": self._api_key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_str,
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------

    def get_events(
        self,
        *,
        category: str | None = None,
        status: str = "open",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch events from Kalshi. GET /trade-api/v2/events"""
        path = "/trade-api/v2/events"
        params: dict[str, Any] = {"limit": limit, "status": status}
        if category:
            params["category"] = category

        headers = self._sign_request("GET", path)
        all_events: list[dict[str, Any]] = []
        try:
            while True:
                resp = self._session.get(
                    self._base_url + path,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                all_events.extend(data.get("events", []))
                cursor = data.get("cursor")
                if not cursor:
                    break
                params["cursor"] = cursor
            return all_events
        except requests.exceptions.RequestException as e:
            logger.error("KalshiForecastClient: failed to fetch events - %s", e)
            return all_events

    def get_markets(
        self,
        *,
        event_ticker: str | None = None,
        status: str = "open",
        limit: int = 200,
        min_close_ts: int | None = None,
        max_close_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch markets from Kalshi. GET /trade-api/v2/markets"""
        path = "/trade-api/v2/markets"
        params: dict[str, Any] = {"limit": limit, "status": status}
        if event_ticker:
            params["event_ticker"] = event_ticker
        if min_close_ts is not None:
            params["min_close_ts"] = min_close_ts
        if max_close_ts is not None:
            params["max_close_ts"] = max_close_ts

        headers = self._sign_request("GET", path)
        all_markets: list[dict[str, Any]] = []
        try:
            while True:
                resp = self._session.get(
                    self._base_url + path,
                    headers=headers,
                    params=params,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                all_markets.extend(data.get("markets", []))
                cursor = data.get("cursor")
                if not cursor:
                    break
                params["cursor"] = cursor
            return all_markets
        except requests.exceptions.RequestException as e:
            logger.error("KalshiForecastClient: failed to fetch markets - %s", e)
            return all_markets

    def get_market(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a single market by ticker. GET /trade-api/v2/markets/{ticker}"""
        path = f"/trade-api/v2/markets/{ticker}"
        headers = self._sign_request("GET", path)
        try:
            resp = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json().get("market", resp.json())
        except requests.exceptions.RequestException as e:
            logger.error("KalshiForecastClient: failed to fetch market %s - %s", ticker, e)
            return None

    def close(self) -> None:
        self._session.close()
