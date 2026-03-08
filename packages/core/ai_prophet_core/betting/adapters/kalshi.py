"""Kalshi exchange adapter."""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests  # type: ignore[import-untyped]

from ..config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_PRIVATE_KEY_B64_ENV,
    KALSHI_PRIVATE_KEY_LEGACY_ENV,
)
from .base import (
    ExchangeAdapter,
    ExecutionMode,
    OrderRequest,
    OrderResult,
    OrderStatus,
)

logger = logging.getLogger(__name__)


class KalshiAdapter(ExchangeAdapter):
    """Routes orders to Kalshi's v2 API."""

    def __init__(
        self,
        api_key_id: str = "",
        private_key_base64: str = "",
        *,
        private_key_b64: str | None = None,
        base_url: str = DEFAULT_KALSHI_BASE_URL,
        dry_run: bool = False,
        timeout_sec: int = 30,
    ):
        # Support the legacy parameter and env name while we transition the public API.
        env_private_key = os.getenv(KALSHI_PRIVATE_KEY_B64_ENV, "") or os.getenv(
            KALSHI_PRIVATE_KEY_LEGACY_ENV, ""
        )
        self._api_key_id = api_key_id or os.getenv(KALSHI_API_KEY_ID_ENV, "")
        self._private_key_base64 = private_key_base64 or private_key_b64 or env_private_key
        self._base_url = base_url
        self._dry_run = dry_run
        self._timeout = timeout_sec
        self._private_key = None
        self._session = requests.Session()

        if not self._api_key_id:
            logger.warning("KalshiAdapter: No API key ID configured")
        if not self._private_key_base64:
            logger.warning(
                "KalshiAdapter: No private key configured "
                "(set KALSHI_PRIVATE_KEY_B64 or pass private_key_base64)"
            )

    @property
    def name(self) -> str:
        return "kalshi"

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.REAL

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def _load_private_key(self):
        """Lazy-load RSA private key from base64-encoded string."""
        if self._private_key is not None:
            return self._private_key

        if not self._private_key_base64:
            raise RuntimeError(
                "Kalshi private key not configured. "
                "Set KALSHI_PRIVATE_KEY_B64 (preferred) or KALSHI_API_KEY "
                "(legacy alias), or pass private_key_base64."
            )

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        key_bytes = base64.b64decode(self._private_key_base64)
        self._private_key = serialization.load_pem_private_key(
            key_bytes, password=None, backend=default_backend()
        )
        return self._private_key

    def _sign_request(self, method: str, path: str) -> dict[str, str]:
        """Generate authenticated headers for Kalshi API."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        private_key = self._load_private_key()
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

    def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit a limit order to Kalshi."""
        validation_error = self.validate_order(request)
        if validation_error:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=validation_error,
            )

        if self._dry_run:
            return self._dry_run_result(request)

        ticker = request.exchange_ticker
        side = request.side.lower()
        action = request.action.lower()
        count = int(request.shares)

        if count <= 0:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Count must be positive integer, got {request.shares}",
            )

        price_cents = int(round(float(request.limit_price) * 100))
        price_cents = max(1, min(99, price_cents))

        order_body: dict[str, Any] = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": "limit",
        }

        if side == "yes":
            order_body["yes_price"] = price_cents
        else:
            order_body["no_price"] = price_cents

        order_body["client_order_id"] = request.order_id

        logger.info(
            "KalshiAdapter: submitting order - %s %sx %s %s @ %s¢ (intent=%s)",
            action,
            count,
            ticker,
            side,
            price_cents,
            request.intent_id,
        )

        path = "/trade-api/v2/portfolio/orders"
        headers = self._sign_request("POST", path)

        try:
            response = self._session.post(
                self._base_url + path,
                headers=headers,
                json=order_body,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.exceptions.HTTPError as e:
            error_detail = ""
            try:
                error_detail = e.response.text[:500]
            except Exception:
                pass
            logger.error(
                "KalshiAdapter: order rejected by API - status=%s, detail=%s",
                e.response.status_code,
                error_detail,
            )
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Kalshi API error {e.response.status_code}: {error_detail}",
                raw_response={"error": error_detail},
            )
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: network error - %s", e)
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Network error: {e}",
            )

        return self._parse_order_response(request, data)

    def get_balance(self) -> Decimal:
        """Fetch available balance from Kalshi."""
        if self._dry_run:
            return Decimal("10000")

        path = "/trade-api/v2/portfolio/balance"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            balance_cents = data.get("balance", 0)
            return Decimal(str(balance_cents)) / Decimal("100")
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed to fetch balance - %s", e)
            return Decimal("0")

    def get_positions(self) -> list[dict[str, Any]]:
        """Fetch current positions from Kalshi."""
        if self._dry_run:
            return []

        path = "/trade-api/v2/portfolio/positions"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("market_positions", [])
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed to fetch positions - %s", e)
            return []

    def get_market(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a single market by ticker for live quotes."""
        path = f"/trade-api/v2/markets/{ticker}"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            return data.get("market")
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed to fetch market %s - %s", ticker, e)
            return None

    def close(self) -> None:
        self._session.close()

    def _dry_run_result(self, request: OrderRequest) -> OrderResult:
        """Simulate a successful fill without hitting the API."""
        now = datetime.now(UTC)
        notional = request.shares * request.limit_price
        logger.info(
            "KalshiAdapter [DRY RUN]: %sx %s %s @ %s",
            request.shares,
            request.exchange_ticker,
            request.side,
            request.limit_price,
        )
        return OrderResult(
            order_id=request.order_id,
            intent_id=request.intent_id,
            status=OrderStatus.DRY_RUN,
            filled_shares=request.shares,
            fill_price=request.limit_price,
            notional=notional,
            fee=Decimal("0"),
            filled_at=now,
            exchange_order_id=f"dry-run-{request.order_id}",
        )

    def _parse_order_response(
        self, request: OrderRequest, data: dict[str, Any]
    ) -> OrderResult:
        """Parse Kalshi API order response into OrderResult."""
        order_data = data.get("order", data)
        now = datetime.now(UTC)

        kalshi_status = order_data.get("status", "").lower()
        exchange_order_id = order_data.get("order_id", "")

        if kalshi_status in ("executed", "filled"):
            status = OrderStatus.FILLED
        elif kalshi_status == "resting":
            status = OrderStatus.FILLED
        elif kalshi_status == "canceled":
            status = OrderStatus.CANCELLED
        elif kalshi_status == "pending":
            status = OrderStatus.FILLED
        else:
            status = OrderStatus.REJECTED

        if status == OrderStatus.FILLED:
            filled_count = order_data.get("place_count", int(request.shares))
            avg_price_cents = order_data.get(
                "avg_price", int(round(float(request.limit_price) * 100))
            )
            fill_price = Decimal(str(avg_price_cents)) / Decimal("100")
            filled_shares = Decimal(str(filled_count))
            notional = filled_shares * fill_price

            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=status,
                filled_shares=filled_shares,
                fill_price=fill_price,
                notional=notional,
                fee=Decimal("0"),
                filled_at=now,
                exchange_order_id=exchange_order_id,
                raw_response=data,
            )

        return OrderResult(
            order_id=request.order_id,
            intent_id=request.intent_id,
            status=status,
            rejection_reason=order_data.get("reason", f"Status: {kalshi_status}"),
            exchange_order_id=exchange_order_id,
            raw_response=data,
        )
