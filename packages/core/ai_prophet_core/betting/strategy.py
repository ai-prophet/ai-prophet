"""
Betting strategy for the live betting system.

Implements the core per-market betting decision.
"""

from typing import Any

from .config import MAX_SPREAD


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
      If diff > 0 -> buy YES, shares = diff, cost = shares * yes_ask
      If diff < 0 -> buy NO,  shares = (1-p) - no_ask, cost = shares * no_ask
      If prediction within spread -> skip
    """
    spread = yes_ask + no_ask
    if spread > max_spread:
        return None

    lower_bound = 1.0 - no_ask
    upper_bound = yes_ask
    if lower_bound <= prediction_prob <= upper_bound:
        return None

    diff = prediction_prob - yes_ask

    if diff > 0:
        shares = prediction_prob - yes_ask
        price = yes_ask
        side = "yes"
    elif diff < 0:
        shares = (1.0 - prediction_prob) - no_ask
        price = no_ask
        side = "no"
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
