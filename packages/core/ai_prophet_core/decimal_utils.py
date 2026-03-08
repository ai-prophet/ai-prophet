"""Decimal quantization utilities for execution and accounting boundaries.

These helpers normalize external numeric inputs into deterministic ``Decimal``
values when the caller needs accounting-safe math. Wire models may still carry
API payloads as strings, and some observation-layer domain models intentionally
use floats; conversion happens explicitly at execution or portfolio boundaries.

Quantization rules:
- Prices: 6 decimal places (0.000001)
- Shares: 6 decimal places (0.000001)
- Cash: 2 decimal places (0.01) - cents
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

# Quantization precisions
PRICE_QUANTIZE = Decimal('0.000001')  # 6 decimals for prices/probabilities
SHARES_QUANTIZE = Decimal('0.000001')  # 6 decimals for shares
CASH_QUANTIZE = Decimal('0.01')  # 2 decimals for cash (cents)


def q_price(value: Decimal | float | str | int) -> Decimal:
    """Quantize price/probability to 6 decimal places (ROUND_HALF_UP)."""
    try:
        decimal_value = Decimal(str(value))
        return decimal_value.quantize(PRICE_QUANTIZE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Cannot convert {value} to price Decimal: {e}") from e


def q_shares(value: Decimal | float | str | int) -> Decimal:
    """Quantize shares to 6 decimal places (ROUND_HALF_UP)."""
    try:
        decimal_value = Decimal(str(value))
        return decimal_value.quantize(SHARES_QUANTIZE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Cannot convert {value} to shares Decimal: {e}") from e


def q_cash(value: Decimal | float | str | int) -> Decimal:
    """Quantize cash to 2 decimal places / cents (ROUND_HALF_UP)."""
    try:
        decimal_value = Decimal(str(value))
        return decimal_value.quantize(CASH_QUANTIZE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as e:
        raise ValueError(f"Cannot convert {value} to cash Decimal: {e}") from e


def is_valid_price(value: Decimal) -> bool:
    """True if 0 <= value <= 1."""
    return Decimal('0') <= value <= Decimal('1')


def is_positive(value: Decimal) -> bool:
    """True if value > 0."""
    return value > Decimal('0')


def is_non_negative(value: Decimal) -> bool:
    """True if value >= 0."""
    return value >= Decimal('0')


def safe_divide(numerator: Decimal, denominator: Decimal, default: Decimal = Decimal('0')) -> Decimal:
    """Divide numerator/denominator, returning *default* when denominator is zero."""
    if denominator == Decimal('0'):
        return default
    return numerator / denominator

