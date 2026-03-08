"""Abstract exchange adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class ExecutionMode(StrEnum):
    """Execution mode selector."""

    PAPER = "PAPER"
    REAL = "REAL"


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
    """Exchange-bound order request."""

    order_id: str
    intent_id: str
    market_id: str
    exchange_ticker: str
    action: str
    side: str
    shares: Decimal
    limit_price: Decimal
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
    """Abstract interface for exchange execution."""

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def mode(self) -> ExecutionMode:
        ...

    @abstractmethod
    def submit_order(self, request: OrderRequest) -> OrderResult:
        ...

    @abstractmethod
    def get_balance(self) -> Decimal:
        ...

    def validate_order(self, request: OrderRequest) -> str | None:
        if request.shares <= 0:
            return "Shares must be positive"
        if request.limit_price <= 0 or request.limit_price >= 1:
            return f"Price must be in (0, 1), got {request.limit_price}"
        return None

    @abstractmethod
    def close(self) -> None:
        ...
