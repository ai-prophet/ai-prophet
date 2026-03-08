"""Abstract exchange adapter interface.

Defines the contract that all exchange adapters must implement.
The execution engine delegates order routing through this interface,
allowing transparent switching between paper trading and real execution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ExecutionMode(StrEnum):
    """Execution mode selector."""
    PAPER = "PAPER"   # Simulated fills at quote prices (current behavior)
    REAL = "REAL"     # Route orders to a real exchange


class OrderStatus(StrEnum):
    """Order lifecycle status."""
    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    DRY_RUN = "DRY_RUN"


@dataclass
class OrderRequest:
    """Exchange-bound order request.

    This is the adapter's input — translated from an internal TradeIntent
    by the execution engine before routing to the exchange.
    """
    order_id: str               # Internal UUID (maps to fill_id)
    intent_id: str              # Original TradeIntent ID
    market_id: str              # Internal market ID
    exchange_ticker: str        # Exchange-specific ticker (e.g. Kalshi market_ticker)
    action: str                 # "BUY" or "SELL"
    side: str                   # "YES" or "NO"
    shares: Decimal             # Number of contracts
    limit_price: Decimal        # Price limit (cents/100 for Kalshi)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderResult:
    """Result of submitting an order to the exchange."""
    order_id: str
    intent_id: str
    status: OrderStatus
    filled_shares: Decimal = Decimal("0")
    fill_price: Decimal = Decimal("0")
    notional: Decimal = Decimal("0")
    fee: Decimal = Decimal("0")
    filled_at: datetime | None = None
    exchange_order_id: str | None = None
    rejection_reason: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


class ExchangeAdapter(ABC):
    """Abstract interface for exchange execution.

    Implementations:
    - KalshiAdapter: Routes orders to Kalshi's v2 API
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable adapter name (e.g. 'paper', 'kalshi')."""
        ...

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        """Execution mode this adapter implements."""
        ...

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> OrderResult:
        """Submit a single order to the exchange.

        Args:
            request: The order to submit.

        Returns:
            OrderResult with fill information or rejection details.
        """
        ...

    @abstractmethod
    def get_balance(self) -> Decimal:
        """Get available balance from the exchange.

        Returns:
            Available balance in dollars.
        """
        ...

    def validate_order(self, request: OrderRequest) -> str | None:
        """Optional pre-submission validation.

        Returns:
            Error message if invalid, None if valid.
        """
        if request.shares <= 0:
            return "Shares must be positive"
        if request.limit_price <= 0 or request.limit_price >= 1:
            return f"Price must be in (0, 1), got {request.limit_price}"
        return None

    @abstractmethod
    def close(self) -> None:
        """Clean up any resources (HTTP clients, etc)."""
        ...
