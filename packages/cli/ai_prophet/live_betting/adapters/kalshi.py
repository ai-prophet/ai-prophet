"""Kalshi exchange adapter — routes orders to Kalshi's v2 REST API.

Implements the ExchangeAdapter interface for real-market execution on Kalshi.
Uses RSA-PSS authentication matching the indexer's KalshiConnector pattern.

Kalshi API notes:
- Prices are in cents (1-99), framework uses [0, 1] floats
- Orders use: POST /trade-api/v2/portfolio/orders
- Side is "yes" or "no" (lowercase)
- Action is "buy" or "sell" (lowercase)
- count is number of contracts (integer)
- type is "limit" or "market"
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import requests  # type: ignore[import-untyped]

from .base import (
    ExchangeAdapter,
    ExecutionMode,
    OrderRequest,
    OrderResult,
    OrderStatus,
)

logger = logging.getLogger(__name__)


class KalshiAdapter(ExchangeAdapter):
    """Routes orders to Kalshi's v2 API for real-market execution.

    Authentication uses RSA-PSS signatures (same as the indexer connector).
    Orders are submitted as limit orders at the computed execution price.

    Supports a dry_run mode that validates everything but doesn't hit the API.

    Args:
        api_key_id: Kalshi API key ID (or KALSHI_API_KEY_ID env var).
        private_key_b64: Base64-encoded RSA private key (or KALSHI_API_KEY env var).
        base_url: Kalshi API base URL.
        dry_run: If True, simulate order submission without hitting the API.
        timeout_sec: HTTP request timeout.
    """

    def __init__(
        self,
        api_key_id: str = "",
        private_key_b64: str = "",
        base_url: str = "https://api.elections.kalshi.com",
        dry_run: bool = False,
        timeout_sec: int = 30,
    ):
        self._api_key_id = api_key_id or os.getenv("KALSHI_API_KEY_ID", "")
        self._private_key_b64 = private_key_b64 or os.getenv("KALSHI_API_KEY", "")
        self._base_url = base_url
        self._dry_run = dry_run
        self._timeout = timeout_sec
        self._private_key = None
        self._session = requests.Session()

        if not self._api_key_id:
            logger.warning("KalshiAdapter: No API key ID configured")
        if not self._private_key_b64:
            logger.warning("KalshiAdapter: No private key configured")

    @property
    def name(self) -> str:
        return "kalshi"

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.REAL

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    # ------------------------------------------------------------------
    # Authentication (RSA-PSS, mirrors indexer/kalshi_connector.py)
    # ------------------------------------------------------------------

    def _load_private_key(self):
        """Lazy-load RSA private key from base64-encoded string."""
        if self._private_key is not None:
            return self._private_key

        if not self._private_key_b64:
            raise RuntimeError(
                "Kalshi private key not configured. "
                "Set KALSHI_API_KEY env var or pass private_key_b64."
            )

        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives import serialization

        key_bytes = base64.b64decode(self._private_key_b64)
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

    # ------------------------------------------------------------------
    # Core adapter methods
    # ------------------------------------------------------------------

    def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit a limit order to Kalshi.

        Translates framework OrderRequest to Kalshi's API format:
        - limit_price (0-1 float) → yes_price/no_price (1-99 cents)
        - side YES/NO → "yes"/"no"
        - action BUY/SELL → "buy"/"sell"
        - shares → count (integer contracts)
        """
        # Pre-validate
        validation_error = self.validate_order(request)
        if validation_error:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=validation_error,
            )

        # Dry-run mode: simulate success without API call
        if self._dry_run:
            return self._dry_run_result(request)

        # Build Kalshi order payload
        ticker = request.exchange_ticker
        side = request.side.lower()  # "yes" or "no"
        action = request.action.lower()  # "buy" or "sell"
        count = int(request.shares)  # Kalshi uses integer contracts

        if count <= 0:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Count must be positive integer, got {request.shares}",
            )

        # Convert price from [0,1] float to cents (1-99)
        price_cents = int(round(float(request.limit_price) * 100))
        price_cents = max(1, min(99, price_cents))

        # Build price field based on side
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

        # Attach client_order_id for idempotency
        order_body["client_order_id"] = request.order_id

        logger.info(
            f"KalshiAdapter: submitting order — "
            f"{action} {count}x {ticker} {side} @ {price_cents}¢ "
            f"(intent={request.intent_id})"
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
                f"KalshiAdapter: order rejected by API — "
                f"status={e.response.status_code}, detail={error_detail}"
            )
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Kalshi API error {e.response.status_code}: {error_detail}",
                raw_response={"error": error_detail},
            )
        except requests.exceptions.RequestException as e:
            logger.error(f"KalshiAdapter: network error — {e}")
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Network error: {e}",
            )

        # Parse response
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
            # Kalshi returns balance in cents
            balance_cents = data.get("balance", 0)
            return Decimal(str(balance_cents)) / Decimal("100")
        except requests.exceptions.RequestException as e:
            logger.error(f"KalshiAdapter: failed to fetch balance — {e}")
            return Decimal("0")

    def get_positions(self) -> list[dict[str, Any]]:
        """Fetch current positions from Kalshi.

        Returns:
            List of position dicts with ticker, side, count, avg_price info.
        """
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
            logger.error(f"KalshiAdapter: failed to fetch positions — {e}")
            return []

    def get_market(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a single market by ticker for live quotes.

        Returns:
            Market dict with yes_bid, yes_ask, etc. or None.
        """
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
            logger.error(f"KalshiAdapter: failed to fetch market {ticker} — {e}")
            return None

    def close(self) -> None:
        """Close HTTP session."""
        self._session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dry_run_result(self, request: OrderRequest) -> OrderResult:
        """Simulate a successful fill without hitting the API."""
        now = datetime.now(UTC)
        notional = request.shares * request.limit_price
        logger.info(
            f"KalshiAdapter [DRY RUN]: {request.action} {request.shares}x "
            f"{request.exchange_ticker} {request.side} @ {request.limit_price}"
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

        # Map Kalshi status to our OrderStatus
        if kalshi_status in ("executed", "filled"):
            status = OrderStatus.FILLED
        elif kalshi_status == "resting":
            # Resting = limit order on book, treat as filled at limit
            # (conservative: in production you'd poll for fill confirmation)
            status = OrderStatus.FILLED
        elif kalshi_status == "canceled":
            status = OrderStatus.CANCELLED
        elif kalshi_status == "pending":
            # Treat pending as filled for now (Kalshi fills are usually instant)
            status = OrderStatus.FILLED
        else:
            status = OrderStatus.REJECTED

        if status == OrderStatus.FILLED:
            # Extract fill info
            filled_count = order_data.get("place_count", int(request.shares))
            # Kalshi returns avg price in cents
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
                fee=Decimal("0"),  # Kalshi fee handling TBD
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
