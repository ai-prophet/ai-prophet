"""
Betting strategy interface and default implementation.

Users can subclass ``BettingStrategy`` to implement custom logic.
The ``DefaultBettingStrategy`` (average-return-neutral) ships as the
built-in default and is used when no custom strategy is provided.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .config import MAX_SPREAD


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Lightweight portfolio state available to strategies during evaluation.

    The engine sets this on the strategy before calling :meth:`evaluate`,
    so custom strategies can access it via ``self.portfolio``.
    """

    cash: Decimal = Decimal("0")
    equity: Decimal = Decimal("0")
    total_pnl: Decimal = Decimal("0")
    position_count: int = 0
    market_position_shares: Decimal = Decimal("0")
    market_position_side: str | None = None


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
    _portfolio: PortfolioSnapshot | None = None

    @property
    def portfolio(self) -> PortfolioSnapshot | None:
        """Current portfolio state, set by the engine before each evaluation."""
        return self._portfolio

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
        # Skip crossed/invalid markets (spread < 1 means prices are unreliable)
        if spread < 0.90:
            return None

        lower_bound = 1.0 - no_ask
        upper_bound = yes_ask
        if lower_bound <= p_yes <= upper_bound:
            return None

        diff = p_yes - yes_ask

        if diff > 0:
            desired_shares = p_yes - yes_ask
            price = yes_ask
            side = "yes"
        elif diff < 0:
            desired_shares = abs(diff)
            price = no_ask
            side = "no"
        else:
            return None

        # Subtract same-side holdings so we only buy the delta needed to reach
        # the target position.  Opposite-side holdings are handled by the
        # engine's NET flip logic, so we don't adjust for those here.
        port = self.portfolio
        if port and port.market_position_side and float(port.market_position_shares) > 0:
            if port.market_position_side.lower() == side:
                current_contracts = float(port.market_position_shares)
                desired_contracts = round(desired_shares * 100)
                delta = max(0, desired_contracts - current_contracts) / 100.0
                if delta < 0.005:  # less than 1 contract needed -- already at target
                    return None
                desired_shares = delta

        cost = desired_shares * price

        return BetSignal(side=side, shares=desired_shares, price=price, cost=cost)


class RebalancingStrategy(BettingStrategy):
    """Rebalancing strategy that maintains (p - q) units of contract.

    At each time step the desired position is ``p - q`` where ``p`` is the
    model's probability and ``q`` is the market YES ask.  The strategy reads
    the *actual* portfolio position (set by the engine) and computes the
    delta needed to reach the target:

        delta = target - current_position

    Positive delta -> buy YES (or sell NO via engine's NET logic).
    Negative delta -> buy NO (or sell YES via engine's NET logic).

    Using the real portfolio position instead of in-memory state means the
    strategy survives process restarts and handles partial fills correctly.
    """

    name = "rebalancing"

    def __init__(self, max_spread: float = MAX_SPREAD, min_trade: float = 0.005) -> None:
        self.max_spread = max_spread
        self.min_trade = min_trade

    def _current_position_yes_equiv(self) -> float:
        """Return the current position as a YES-equivalent signed quantity.

        Positive = holding YES contracts, negative = holding NO contracts.
        Uses fractional shares (0-1 scale) matching target units.
        """
        port = self.portfolio
        if not port or not port.market_position_side or port.market_position_shares <= 0:
            return 0.0
        shares = float(port.market_position_shares) / 100.0  # contracts -> fractional
        if port.market_position_side.lower() == "yes":
            return shares
        else:
            return -shares

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
        if spread < 0.90:
            return None

        # Within-spread filter: if prediction sits inside the bid-ask band,
        # there is no edge -- skip without updating state.
        lower_bound = 1.0 - no_ask
        upper_bound = yes_ask
        if lower_bound <= p_yes <= upper_bound:
            return None

        # Target position in YES-equivalent fractional units: p - q
        target = p_yes - yes_ask

        # Actual current position from portfolio (set by engine)
        current_pos = self._current_position_yes_equiv()

        # Delta to reach target
        delta = target - current_pos

        if abs(delta) < self.min_trade:
            return None

        if delta > 0:
            # Increase YES exposure
            side = "yes"
            shares = delta
            price = yes_ask
            # If we hold NO, engine will sell those first (NET flip) -- no cash needed for that portion
            sell_portion = min(shares, abs(current_pos)) if current_pos < 0 else 0.0
        else:
            # Decrease YES exposure -> buy NO (engine handles sell-first)
            side = "no"
            shares = abs(delta)
            price = no_ask
            # If we hold YES, engine will sell those first (NET flip) -- no cash needed for that portion
            sell_portion = min(shares, current_pos) if current_pos > 0 else 0.0

        buy_portion = shares - sell_portion

        # Only cap the BUY portion by available cash -- sells return cash, they cost nothing.
        # Include expected sell proceeds so the buy isn't under-sized after a NET flip.
        port = self.portfolio
        if buy_portion > 0 and port is not None:
            sell_price = no_ask if side == "yes" else yes_ask
            sell_proceeds = sell_portion * sell_price
            available = float(port.cash) + sell_proceeds
            if available <= 0:
                # No cash for the buy portion; only do the sell-down
                buy_portion = 0.0
            else:
                buy_cost = buy_portion * price
                if buy_cost > available:
                    buy_portion = available / price if price > 0 else 0.0

        shares = sell_portion + buy_portion
        if shares < self.min_trade:
            return None

        cost = shares * price

        return BetSignal(
            side=side,
            shares=shares,
            price=price,
            cost=cost,
            metadata={
                "target": round(target, 6),
                "current_pos": round(current_pos, 6),
                "delta": round(delta, 6),
                "sell_portion": round(sell_portion, 6),
                "buy_portion": round(buy_portion, 6),
            },
        )
