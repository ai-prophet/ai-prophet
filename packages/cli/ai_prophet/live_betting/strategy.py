"""
Betting strategy for the live betting system.

Adapted from compute_average_return_neutral in scoring.py.
Implements the core betting decision logic per-market.
"""

from typing import Any

from ai_prophet.live_betting.config import MAX_SPREAD


def compute_bet(
    prediction_prob: float,
    yes_ask: float,
    no_ask: float,
    max_spread: float = MAX_SPREAD,
) -> dict[str, Any] | None:
    """
    Compute whether to bet on a single binary market, and how much.

    Implements the compute_average_return_neutral strategy:
      diff = prediction_prob - yes_ask
      If diff > 0 → buy YES, shares = diff, cost = shares * yes_ask
      If diff < 0 → buy NO,  shares = (1-p) - no_ask, cost = shares * no_ask
      If prediction within spread → skip

    Args:
        prediction_prob: Model's predicted probability of YES (0-1).
        yes_ask: Current YES ask price (0-1 scale).
        no_ask: Current NO ask price (0-1 scale).
        max_spread: Maximum spread (yes_ask + no_ask) to allow. Markets with
                    wider spreads are skipped (liquidity filter).

    Returns:
        Dict with {side, shares, price, cost} or None if no bet.
    """
    # Liquidity filter: skip if spread is too wide
    spread = yes_ask + no_ask
    if spread > max_spread:
        return None

    # Spread skip: if prediction falls within the bid-ask spread, don't bet
    # The spread region is [1 - no_ask, yes_ask]
    lower_bound = 1.0 - no_ask
    upper_bound = yes_ask
    if lower_bound <= prediction_prob <= upper_bound:
        return None

    diff = prediction_prob - yes_ask

    if diff > 0:
        # Buy YES
        shares = prediction_prob - yes_ask
        price = yes_ask
        side = "yes"
    elif diff < 0:
        # Buy NO
        shares = (1.0 - prediction_prob) - no_ask
        price = no_ask
        side = "no"

        # Edge case: if shares <= 0 (shouldn't happen given spread check, but be safe)
        if shares <= 0:
            return None
    else:
        return None

    cost = shares * price

    return {
        "side": side,
        "shares": shares,
        "price": price,
        "cost": cost,
    }
