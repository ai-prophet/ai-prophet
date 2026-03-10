"""
Betting strategy interface and default implementation.

Users can subclass ``BettingStrategy`` to implement custom logic.
The ``DefaultBettingStrategy`` (average-return-neutral) ships as the
built-in default and is used when no custom strategy is provided.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from .config import MAX_SPREAD


@dataclass(frozen=True)
class BetSignal:
    """Output of a strategy evaluation for a single market.

    Attributes:
        side: ``"yes"`` or ``"no"``.
        shares: Fractional share quantity the strategy wants to buy.
        price: Limit price per share (0-1 range).
        cost: ``shares * price``.
        metadata: Arbitrary extra info the strategy wants to attach
            (e.g. edge size, confidence band).  Logged to the DB as JSON.
    """

    side: str
    shares: float
    price: float
    cost: float
    metadata: dict[str, Any] | None = None


class BettingStrategy(ABC):
    """Base class for betting strategies.

    Subclass this and override :meth:`evaluate` to plug your own
    decision logic into the betting engine.

    Example::

        class KellyStrategy(BettingStrategy):
            name = "kelly"

            def evaluate(self, market_id, p_yes, yes_ask, no_ask):
                edge = p_yes - yes_ask
                if edge <= 0:
                    return None
                fraction = edge / yes_ask
                return BetSignal(
                    side="yes",
                    shares=fraction,
                    price=yes_ask,
                    cost=fraction * yes_ask,
                )
    """

    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        market_id: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
    ) -> BetSignal | None:
        """Decide whether to bet on *market_id*.

        Args:
            market_id: Canonical market identifier (e.g. ``"kalshi:TICKER"``).
            p_yes: Model's predicted probability of YES.
            yes_ask: Current ask price for a YES contract (0-1).
            no_ask: Current ask price for a NO contract (0-1).

        Returns:
            A :class:`BetSignal` describing the desired bet, or ``None``
            to skip this market.
        """
        ...


class DefaultBettingStrategy(BettingStrategy):
    """Average-return-neutral strategy (the built-in default).

    Logic:
      * If the spread (``yes_ask + no_ask``) exceeds *max_spread*, skip.
      * If the prediction falls inside the bid-ask band, skip.
      * Otherwise buy the side where the model disagrees with the market,
        sizing by the magnitude of the disagreement.
    """

    name = "default"

    def __init__(self, max_spread: float = MAX_SPREAD) -> None:
        self.max_spread = max_spread

    def evaluate(
        self,
        market_id: str,
        p_yes: float,
        yes_ask: float,
        no_ask: float,
    ) -> BetSignal | None:
        spread = yes_ask + no_ask
        if spread > self.max_spread:
            return None

        lower_bound = 1.0 - no_ask
        upper_bound = yes_ask
        if lower_bound <= p_yes <= upper_bound:
            return None

        diff = p_yes - yes_ask

        if diff > 0:
            shares = p_yes - yes_ask
            price = yes_ask
            side = "yes"
        elif diff < 0:
            shares = (1.0 - p_yes) - no_ask
            price = no_ask
            side = "no"
            if shares <= 0:
                return None
        else:
            return None

        cost = shares * price

        return BetSignal(side=side, shares=shares, price=price, cost=cost)
