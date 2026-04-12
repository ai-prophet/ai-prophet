"""Tests for decimal quantization utilities."""

from decimal import Decimal

import pytest
from ai_prophet_core.decimal_utils import (
    CASH_QUANTIZE,
    PRICE_QUANTIZE,
    SHARES_QUANTIZE,
    is_non_negative,
    is_positive,
    is_valid_price,
    q_cash,
    q_price,
    q_shares,
    safe_divide,
)


class TestQuantizePrice:
    """Tests for q_price function."""

    def test_quantizes_to_6_decimals(self):
        """Should quantize to exactly 6 decimal places."""
        result = q_price(0.123456789)
        assert result == Decimal('0.123457')
        assert str(result) == '0.123457'

    def test_rounds_half_up(self):
        """Should use ROUND_HALF_UP strategy."""
        assert q_price(0.1234565) == Decimal('0.123457')  # 5 rounds up
        assert q_price(0.1234564) == Decimal('0.123456')  # 4 rounds down

    def test_accepts_string(self):
        """Should accept string input."""
        assert q_price("0.5") == Decimal('0.500000')

    def test_accepts_decimal(self):
        """Should accept Decimal input."""
        assert q_price(Decimal("0.999999")) == Decimal('0.999999')

    def test_accepts_int(self):
        """Should accept integer input."""
        assert q_price(1) == Decimal('1.000000')

    def test_accepts_float(self):
        """Should accept float input (via string conversion)."""
        result = q_price(0.5)
        assert result == Decimal('0.500000')

    def test_zero(self):
        """Should handle zero correctly."""
        assert q_price(0) == Decimal('0.000000')

    def test_one(self):
        """Should handle one correctly."""
        assert q_price(1) == Decimal('1.000000')

    def test_very_small_number(self):
        """Should handle very small numbers."""
        assert q_price(0.0000001) == Decimal('0.000000')
        assert q_price(0.0000005) == Decimal('0.000001')

    def test_invalid_input_raises(self):
        """Should raise ValueError for invalid input."""
        with pytest.raises(ValueError):
            q_price("not a number")

    def test_determinism(self):
        """Same input should always produce same output."""
        value = 0.123456789
        result1 = q_price(value)
        result2 = q_price(value)
        result3 = q_price(value)

        assert result1 == result2 == result3


class TestQuantizeShares:
    """Tests for q_shares function."""

    def test_quantizes_to_6_decimals(self):
        """Should quantize to exactly 6 decimal places."""
        result = q_shares(100.123456789)
        assert result == Decimal('100.123457')

    def test_rounds_half_up(self):
        """Should use ROUND_HALF_UP strategy."""
        assert q_shares(50.5000005) == Decimal('50.500001')
        assert q_shares(50.5000004) == Decimal('50.500000')

    def test_large_numbers(self):
        """Should handle large share quantities."""
        assert q_shares(1000000.123456) == Decimal('1000000.123456')

    def test_accepts_various_types(self):
        """Should accept string, Decimal, int, float."""
        assert q_shares("100.5") == Decimal('100.500000')
        assert q_shares(Decimal("100.5")) == Decimal('100.500000')
        assert q_shares(100) == Decimal('100.000000')
        assert q_shares(100.5) == Decimal('100.500000')

    def test_zero_shares(self):
        """Should handle zero shares."""
        assert q_shares(0) == Decimal('0.000000')

    def test_fractional_shares(self):
        """Should handle fractional shares correctly."""
        assert q_shares(0.5) == Decimal('0.500000')
        assert q_shares(0.1) == Decimal('0.100000')

    def test_invalid_input_raises(self):
        """Should raise ValueError for invalid input."""
        with pytest.raises(ValueError):
            q_shares("invalid")


class TestQuantizeCash:
    """Tests for q_cash function."""

    def test_quantizes_to_2_decimals(self):
        """Should quantize to exactly 2 decimal places (cents)."""
        result = q_cash(10000.12345)
        assert result == Decimal('10000.12')
        assert str(result) == '10000.12'

    def test_rounds_half_up(self):
        """Should use ROUND_HALF_UP strategy."""
        assert q_cash(99.995) == Decimal('100.00')  # 5 rounds up
        assert q_cash(99.994) == Decimal('99.99')   # 4 rounds down

    def test_accepts_various_types(self):
        """Should accept string, Decimal, int, float."""
        assert q_cash("1000.50") == Decimal('1000.50')
        assert q_cash(Decimal("1000.50")) == Decimal('1000.50')
        assert q_cash(1000) == Decimal('1000.00')
        assert q_cash(1000.5) == Decimal('1000.50')

    def test_large_amounts(self):
        """Should handle large cash amounts."""
        assert q_cash(1000000.99) == Decimal('1000000.99')

    def test_zero_cash(self):
        """Should handle zero."""
        assert q_cash(0) == Decimal('0.00')

    def test_small_fractions_round(self):
        """Should round small fractions to nearest cent."""
        assert q_cash(0.001) == Decimal('0.00')
        assert q_cash(0.005) == Decimal('0.01')
        assert q_cash(0.999) == Decimal('1.00')

    def test_invalid_input_raises(self):
        """Should raise ValueError for invalid input."""
        with pytest.raises(ValueError):
            q_cash("not valid")


class TestIsValidPrice:
    """Tests for is_valid_price function."""

    def test_valid_prices(self):
        """Should return True for prices between 0 and 1."""
        assert is_valid_price(Decimal('0')) is True
        assert is_valid_price(Decimal('0.5')) is True
        assert is_valid_price(Decimal('1')) is True
        assert is_valid_price(Decimal('0.000001')) is True
        assert is_valid_price(Decimal('0.999999')) is True

    def test_invalid_prices(self):
        """Should return False for prices outside [0, 1]."""
        assert is_valid_price(Decimal('-0.1')) is False
        assert is_valid_price(Decimal('1.1')) is False
        assert is_valid_price(Decimal('2')) is False
        assert is_valid_price(Decimal('-1')) is False

    def test_edge_cases(self):
        """Should handle boundary values correctly."""
        assert is_valid_price(Decimal('0.0')) is True
        assert is_valid_price(Decimal('1.0')) is True
        assert is_valid_price(Decimal('0.000000')) is True
        assert is_valid_price(Decimal('1.000000')) is True


class TestIsPositive:
    """Tests for is_positive function."""

    def test_positive_values(self):
        """Should return True for values > 0."""
        assert is_positive(Decimal('0.01')) is True
        assert is_positive(Decimal('1')) is True
        assert is_positive(Decimal('1000')) is True
        assert is_positive(Decimal('0.000001')) is True

    def test_non_positive_values(self):
        """Should return False for values <= 0."""
        assert is_positive(Decimal('0')) is False
        assert is_positive(Decimal('-0.01')) is False
        assert is_positive(Decimal('-1')) is False

    def test_edge_case_zero(self):
        """Zero should not be positive."""
        assert is_positive(Decimal('0.0')) is False
        assert is_positive(Decimal('0.00')) is False


class TestIsNonNegative:
    """Tests for is_non_negative function."""

    def test_non_negative_values(self):
        """Should return True for values >= 0."""
        assert is_non_negative(Decimal('0')) is True
        assert is_non_negative(Decimal('0.01')) is True
        assert is_non_negative(Decimal('1')) is True
        assert is_non_negative(Decimal('1000')) is True

    def test_negative_values(self):
        """Should return False for values < 0."""
        assert is_non_negative(Decimal('-0.01')) is False
        assert is_non_negative(Decimal('-1')) is False

    def test_edge_case_zero(self):
        """Zero should be non-negative."""
        assert is_non_negative(Decimal('0.0')) is True


class TestSafeDivide:
    """Tests for safe_divide function."""

    def test_normal_division(self):
        """Should divide normally when denominator is non-zero."""
        result = safe_divide(Decimal('10'), Decimal('2'))
        assert result == Decimal('5')

        result = safe_divide(Decimal('1'), Decimal('3'))
        assert result == Decimal('1') / Decimal('3')

    def test_division_by_zero_returns_default(self):
        """Should return default when denominator is zero."""
        result = safe_divide(Decimal('10'), Decimal('0'))
        assert result == Decimal('0')

    def test_custom_default(self):
        """Should return custom default when provided."""
        result = safe_divide(Decimal('10'), Decimal('0'), Decimal('-1'))
        assert result == Decimal('-1')

        result = safe_divide(Decimal('10'), Decimal('0'), Decimal('999'))
        assert result == Decimal('999')

    def test_zero_numerator(self):
        """Should handle zero numerator correctly."""
        result = safe_divide(Decimal('0'), Decimal('5'))
        assert result == Decimal('0')


class TestQuantizationInvariants:
    """Tests for important invariants across quantization functions."""

    def test_quantize_is_idempotent(self):
        """Quantizing a quantized value should return same value."""
        # Price
        price = q_price(0.123456789)
        assert q_price(price) == price

        # Shares
        shares = q_shares(100.123456789)
        assert q_shares(shares) == shares

        # Cash
        cash = q_cash(1000.12345)
        assert q_cash(cash) == cash

    def test_string_roundtrip(self):
        """Converting to string and back should be stable."""
        original = Decimal('0.123456')
        quantized = q_price(original)
        from_string = q_price(str(quantized))

        assert quantized == from_string

    def test_cash_arithmetic_stays_quantized(self):
        """Cash arithmetic should maintain quantization."""
        a = q_cash(100.50)
        b = q_cash(50.25)

        # Addition
        sum_result = q_cash(a + b)
        assert sum_result == Decimal('150.75')

        # Subtraction
        diff_result = q_cash(a - b)
        assert diff_result == Decimal('50.25')

    def test_price_multiplication_with_shares(self):
        """Price * shares should be quantized to cash."""
        price = q_price(0.5)
        shares = q_shares(100)

        notional = q_cash(price * shares)
        assert notional == Decimal('50.00')

    def test_precision_levels_correct(self):
        """Verify precision levels are as specified."""
        assert PRICE_QUANTIZE == Decimal('0.000001')   # 6 decimals
        assert SHARES_QUANTIZE == Decimal('0.000001')  # 6 decimals
        assert CASH_QUANTIZE == Decimal('0.01')        # 2 decimals

    def test_no_float_precision_errors(self):
        """Using Decimal should avoid float precision issues."""
        # Classic float precision problem: 0.1 + 0.2 != 0.3
        # With Decimal and quantization, this should work

        a = q_cash(0.1)
        b = q_cash(0.2)
        result = q_cash(a + b)

        assert result == Decimal('0.30')
        assert result == q_cash(0.3)

