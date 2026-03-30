"""Kalshi exchange adapter."""

from __future__ import annotations

import base64
import logging
import os
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import requests  # type: ignore[import-untyped]

from ..config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_API_KEY_ID_ENV,
    KALSHI_PRIVATE_KEY_B64_ENV,
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
        base_url: str = DEFAULT_KALSHI_BASE_URL,
        dry_run: bool = False,
        timeout_sec: int = 30,
    ):
        self._api_key_id = api_key_id or os.getenv(KALSHI_API_KEY_ID_ENV, "")
        self._private_key_base64 = private_key_base64 or os.getenv(KALSHI_PRIVATE_KEY_B64_ENV, "")
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
                "(set KALSHI_PRIVATE_KEY_B64 env var or pass private_key_base64)"
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
                "Set KALSHI_PRIVATE_KEY_B64 or pass private_key_base64."
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

    def validate_order(self, request: OrderRequest) -> str | None:
        """Validate order before submission, including 36-hour pre-resolution check."""
        # Call parent validation first
        base_validation = super().validate_order(request)
        if base_validation:
            return base_validation

        # Check 36-hour pre-resolution constraint
        from datetime import timedelta

        # Get market expiration from metadata if available
        market_expiration = request.metadata.get("market_expiration")
        if market_expiration:
            if isinstance(market_expiration, str):
                try:
                    market_expiration = datetime.fromisoformat(market_expiration.replace("Z", "+00:00"))
                except (ValueError, TypeError):
                    market_expiration = None

            if market_expiration and isinstance(market_expiration, datetime):
                now = datetime.now(UTC)
                time_to_expiration = market_expiration - now

                # Block trading within 36 hours of expiration (even if price deviates)
                if time_to_expiration <= timedelta(hours=36):
                    hours_remaining = time_to_expiration.total_seconds() / 3600
                    if hours_remaining > 0:
                        return (
                            f"Trading blocked: Market expires in {hours_remaining:.1f} hours. "
                            f"Trading is not allowed within 36 hours of market resolution."
                        )
                    else:
                        return "Trading blocked: Market has already expired or resolved."

        return None

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
        count = round(float(request.shares))

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
        """Fetch available balance from Kalshi (always real, even in dry-run)."""
        data = self.get_balance_details()
        balance_cents = data.get("balance", 0)
        return Decimal(str(balance_cents)) / Decimal("100")

    def get_balance_details(self) -> dict[str, Any]:
        """Fetch raw balance details from Kalshi."""
        path = "/trade-api/v2/portfolio/balance"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed to fetch balance - %s", e)
            return {"balance": 0, "portfolio_value": 0}

    def get_positions(self) -> list[dict[str, Any]]:
        """Fetch current positions from Kalshi (always real, even in dry-run)."""
        return self._get_paginated_items(
            "/trade-api/v2/portfolio/positions",
            items_key="market_positions",
        )

    def get_orders(self, *, status: str | None = None, ticker: str | None = None) -> list[dict[str, Any]]:
        """Fetch current order states from Kalshi."""
        params: dict[str, Any] = {}
        if status:
            params["status"] = status
        if ticker:
            params["ticker"] = ticker
        return self._get_paginated_items(
            "/trade-api/v2/portfolio/orders",
            items_key="orders",
            params=params,
        )

    def get_historical_orders(self, *, ticker: str | None = None) -> list[dict[str, Any]]:
        """Fetch archived order states from Kalshi's historical store."""
        params: dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        return self._get_paginated_items(
            "/trade-api/v2/historical/orders",
            items_key="orders",
            params=params,
        )

    def get_settlements(
        self,
        *,
        ticker: str | None = None,
        event_ticker: str | None = None,
        min_ts: datetime | None = None,
        max_ts: datetime | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch settlement history from Kalshi for resolved markets.

        Args:
            ticker: Optional ticker to filter by specific market
            event_ticker: Optional event ticker to filter by
            min_ts: Optional minimum timestamp to filter settlements
            max_ts: Optional maximum timestamp to filter settlements
            limit: Maximum number of settlements to fetch (default: 200)

        Returns:
            List of settlement records containing:
                - ticker: Market ticker
                - market_result: Market result ("yes" or "no")
                - yes_count: Number of yes contracts held
                - no_count: Number of no contracts held
                - revenue: Revenue from the settlement
                - fee_cost: Fees paid
                - settled_time: When the market was settled
        """
        params: dict[str, Any] = {}
        if ticker:
            params["ticker"] = ticker
        if event_ticker:
            params["event_ticker"] = event_ticker
        if min_ts:
            params["min_ts"] = int(min_ts.timestamp() * 1000)
        if max_ts:
            params["max_ts"] = int(max_ts.timestamp() * 1000)
        if limit:
            params["limit"] = min(limit, 200)  # API max is 200

        return self._get_paginated_items(
            "/trade-api/v2/portfolio/settlements",
            items_key="settlements",
            params=params,
        )

    def get_fills(
        self,
        *,
        ticker: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch fill history from Kalshi.

        Args:
            ticker: Optional ticker to filter by specific market
            limit: Maximum number of fills to fetch (default: 200)

        Returns:
            List of fill records containing trade execution details
        """
        params: dict[str, Any] = {"limit": limit}
        if ticker:
            params["ticker"] = ticker

        return self._get_paginated_items(
            "/trade-api/v2/portfolio/fills",
            items_key="fills",
            params=params,
        )

    def _get_paginated_items(
        self,
        path: str,
        *,
        items_key: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        base_params = {"limit": 200}
        if params:
            base_params.update({k: v for k, v in params.items() if v not in (None, "")})
        cursor: str | None = None
        items: list[dict[str, Any]] = []

        try:
            while True:
                query_params = dict(base_params)
                if cursor:
                    query_params["cursor"] = cursor
                headers = self._sign_request("GET", path)
                response = self._session.get(
                    self._base_url + path,
                    headers=headers,
                    params=query_params,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                data = response.json()
                page_items = data.get(items_key, [])
                if isinstance(page_items, list):
                    items.extend(page_items)
                cursor = data.get("cursor")
                if not cursor or not page_items:
                    break
            return items
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed GET %s - %s", path, e)
            return []

    def get_market(self, ticker: str) -> dict[str, Any] | None:
        """Fetch a single market's live pricing from Kalshi."""
        path = f"/trade-api/v2/markets/{ticker}"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            market = response.json().get("market", {})
            return {
                "ticker": ticker,
                "yes_bid": market.get("yes_bid_dollars"),
                "yes_ask": market.get("yes_ask_dollars"),
                "no_bid": market.get("no_bid_dollars"),
                "no_ask": market.get("no_ask_dollars"),
                "last_price": market.get("last_price_dollars"),
                "volume": market.get("volume_24h_fp", 0),
                "status": market.get("status"),
            }
        except requests.exceptions.RequestException as e:
            logger.error("KalshiAdapter: failed to fetch market %s - %s", ticker, e)
            return None

    def get_order(
        self,
        exchange_order_id: str,
        *,
        fallback_request: OrderRequest | None = None,
    ) -> OrderResult | None:
        """Poll Kalshi for the current status of an order."""
        if self._dry_run:
            return None

        path = f"/trade-api/v2/portfolio/orders/{exchange_order_id}"
        headers = self._sign_request("GET", path)

        try:
            response = self._session.get(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            data = response.json()
            request_for_parse = fallback_request or OrderRequest(
                order_id="poll",
                intent_id="poll",
                market_id="",
                exchange_ticker="",
                action="BUY",
                side="YES",
                shares=Decimal("1"),
                limit_price=Decimal("0.50"),
            )
            return self._parse_order_response(request_for_parse, data)
        except requests.exceptions.RequestException as e:
            logger.error(
                "KalshiAdapter: failed to poll order %s - %s",
                exchange_order_id, e,
            )
            return None

    def cancel_order(self, exchange_order_id: str) -> bool:
        """Cancel a live order on Kalshi."""
        if self._dry_run:
            return True

        path = f"/trade-api/v2/portfolio/orders/{exchange_order_id}"
        headers = self._sign_request("DELETE", path)

        try:
            response = self._session.delete(
                self._base_url + path,
                headers=headers,
                timeout=self._timeout,
            )
            response.raise_for_status()
            logger.info("KalshiAdapter: cancelled order %s", exchange_order_id)
            return True
        except requests.exceptions.RequestException as e:
            logger.warning(
                "KalshiAdapter: failed to cancel order %s - %s",
                exchange_order_id,
                e,
            )
            return False

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
        fee = self._extract_fee(order_data)

        if kalshi_status in ("executed", "filled"):
            status = OrderStatus.FILLED
        elif kalshi_status in ("resting", "pending"):
            status = OrderStatus.PENDING
        elif kalshi_status == "canceled":
            status = OrderStatus.CANCELLED
        else:
            status = OrderStatus.REJECTED

        filled_count = order_data.get("fill_count")
        fill_count_fp = order_data.get("fill_count_fp")
        if fill_count_fp is not None:
            filled_count = fill_count_fp
        if filled_count is None:
            initial_count = order_data.get("initial_count")
            remaining_count = order_data.get("remaining_count")
            initial_count_fp = order_data.get("initial_count_fp")
            remaining_count_fp = order_data.get("remaining_count_fp")
            if initial_count_fp is not None and remaining_count_fp is not None:
                filled_count = Decimal(str(initial_count_fp)) - Decimal(str(remaining_count_fp))
            elif initial_count is not None and remaining_count is not None:
                filled_count = Decimal(str(initial_count)) - Decimal(str(remaining_count))
            elif status == OrderStatus.FILLED:
                filled_count = order_data.get("place_count", int(request.shares))
            else:
                filled_count = 0

        filled_shares = Decimal(str(filled_count))

        avg_price_cents = order_data.get("avg_price")
        avg_price_dollars = order_data.get("avg_price_dollars")
        if avg_price_dollars is not None:
            fill_price = Decimal(str(avg_price_dollars))
        else:
            if avg_price_cents is None and filled_shares > 0:
                taker_fill_cost = Decimal(str(order_data.get("taker_fill_cost_dollars") or order_data.get("taker_fill_cost") or 0))
                maker_fill_cost = Decimal(str(order_data.get("maker_fill_cost_dollars") or order_data.get("maker_fill_cost") or 0))
                total_fill_cost = taker_fill_cost + maker_fill_cost
                # Fix: Check for zero division
                if total_fill_cost > 0 and filled_shares > 0:
                    fill_price = total_fill_cost / filled_shares
                else:
                    fill_price = Decimal("0")
            else:
                fill_price = Decimal("0")

            if fill_price <= 0:
                if avg_price_cents is None:
                    avg_price_cents = int(round(float(request.limit_price) * 100))
                fill_price = Decimal(str(avg_price_cents)) / Decimal("100")

        if avg_price_cents is None and filled_shares > 0 and avg_price_dollars is None:
            taker_fill_cost = Decimal(str(order_data.get("taker_fill_cost_dollars") or order_data.get("taker_fill_cost") or 0))
            maker_fill_cost = Decimal(str(order_data.get("maker_fill_cost_dollars") or order_data.get("maker_fill_cost") or 0))
            total_fill_cost = taker_fill_cost + maker_fill_cost
            # Fix: Check for zero division
            if total_fill_cost > 0 and filled_shares > 0:
                avg_price_cents = total_fill_cost / filled_shares
        notional = filled_shares * fill_price

        if status == OrderStatus.FILLED:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=status,
                filled_shares=filled_shares,
                fill_price=fill_price,
                notional=notional,
                fee=fee,
                filled_at=now,
                exchange_order_id=exchange_order_id,
                raw_response=data,
            )

        if status == OrderStatus.PENDING:
            return OrderResult(
                order_id=request.order_id,
                intent_id=request.intent_id,
                status=status,
                filled_shares=filled_shares,
                fill_price=fill_price,
                notional=notional,
                fee=fee,
                exchange_order_id=exchange_order_id,
                raw_response=data,
            )

        return OrderResult(
            order_id=request.order_id,
            intent_id=request.intent_id,
            status=status,
            filled_shares=filled_shares,
            fill_price=fill_price,
            notional=notional,
            fee=fee,
            rejection_reason=order_data.get("reason", f"Status: {kalshi_status}"),
            exchange_order_id=exchange_order_id,
            raw_response=data,
        )

    @staticmethod
    def _coerce_money_amount(key: str, value: Any) -> Decimal:
        # Fix: Handle non-numeric values safely
        try:
            # Handle None, empty string, or "N/A" type values
            if value in (None, "", "N/A", "null"):
                return Decimal("0")
            amount = Decimal(str(value))
            if key.endswith("_cents") or key.endswith("_cent"):
                return amount / Decimal("100")
            return amount
        except (ValueError, TypeError, InvalidOperation):
            logger.debug("Failed to parse fee value %s=%s, treating as 0", key, value)
            return Decimal("0")

    def _extract_fee(self, order_data: dict[str, Any]) -> Decimal:
        total = Decimal("0")
        found_any = False
        candidate_keys = (
            "fee",
            "fees",
            "fee_dollars",
            "fees_dollars",
            "fee_cents",
            "fees_cents",
            "maker_fees",
            "maker_fees_dollars",
            "maker_fees_cents",
            "taker_fees",
            "taker_fees_dollars",
            "taker_fees_cents",
            "filled_fees",
            "filled_fees_dollars",
            "filled_fees_cents",
            "fees_paid",
            "fees_paid_dollars",
            "fees_paid_cents",
        )

        for key in candidate_keys:
            value = order_data.get(key)
            if value in (None, ""):
                continue
            total += self._coerce_money_amount(key, value)
            found_any = True

        fills = order_data.get("fills")
        if not found_any and isinstance(fills, list):
            for fill in fills:
                if not isinstance(fill, dict):
                    continue
                for key in candidate_keys:
                    value = fill.get(key)
                    if value in (None, ""):
                        continue
                    total += self._coerce_money_amount(key, value)
                    found_any = True

        return total if found_any else Decimal("0")
