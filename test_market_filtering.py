#!/usr/bin/env python3
"""Test that extreme-priced markets are properly filtered."""

import sys
sys.path.insert(0, 'packages/core')

from ai_prophet_core.betting.strategy import (
    RebalancingStrategy, DefaultBettingStrategy, PortfolioSnapshot
)
from decimal import Decimal

def test_strategies():
    """Test that both strategies properly filter extreme markets."""

    # Create strategies
    rebalancing = RebalancingStrategy()
    default = DefaultBettingStrategy()

    # Set a portfolio (not needed for this test but good to have)
    portfolio = PortfolioSnapshot(cash=Decimal("1000"))
    rebalancing._portfolio = portfolio
    default._portfolio = portfolio

    # Test cases: (yes_ask, no_ask, expected_result_description)
    test_cases = [
        # Extreme YES prices (should be filtered)
        (0.00, 1.00, "HOLD_NOPROFIT - YES at 0%"),
        (0.01, 0.99, "HOLD_NOPROFIT - YES at 1%"),
        (0.03, 0.97, "HOLD_NOPROFIT - YES at 3% boundary"),
        (0.97, 0.03, "HOLD_NOPROFIT - YES at 97% boundary"),
        (0.99, 0.01, "HOLD_NOPROFIT - YES at 99%"),

        # Extreme NO prices (should be filtered)
        (0.50, 0.99, "HOLD_NOPROFIT - NO at 99%"),
        (0.50, 0.97, "HOLD_NOPROFIT - NO at 97% boundary"),
        (0.50, 0.03, "HOLD_NOPROFIT - NO at 3% boundary"),
        (0.50, 0.01, "HOLD_NOPROFIT - NO at 1%"),

        # Normal prices (should pass through to evaluation)
        (0.45, 0.55, "Normal - should evaluate"),
        (0.30, 0.70, "Normal - should evaluate"),
        (0.70, 0.30, "Normal - should evaluate"),
    ]

    print("Testing RebalancingStrategy:")
    print("-" * 60)
    for yes_ask, no_ask, description in test_cases:
        signal = rebalancing.evaluate("test_market", 0.5, yes_ask, no_ask)

        if signal and signal.metadata and signal.metadata.get("reason") == "HOLD_NOPROFIT":
            result = "✓ HOLD_NOPROFIT"
        elif signal is None:
            result = "None (no edge)"
        else:
            result = f"Signal: {signal.side.upper()} {signal.shares:.3f}"

        print(f"  YES={yes_ask:.2f}, NO={no_ask:.2f}: {result} | {description}")

    print("\nTesting DefaultBettingStrategy:")
    print("-" * 60)
    for yes_ask, no_ask, description in test_cases:
        signal = default.evaluate("test_market", 0.5, yes_ask, no_ask)

        if signal and signal.metadata and signal.metadata.get("reason") == "HOLD_NOPROFIT":
            result = "✓ HOLD_NOPROFIT"
        elif signal is None:
            result = "None (no edge)"
        else:
            result = f"Signal: {signal.side.upper()} {signal.shares:.3f}"

        print(f"  YES={yes_ask:.2f}, NO={no_ask:.2f}: {result} | {description}")

    print("\n✅ Test complete!")

if __name__ == "__main__":
    test_strategies()