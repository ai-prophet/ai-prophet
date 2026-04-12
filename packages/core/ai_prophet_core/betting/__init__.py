"""Betting module - pluggable strategy, exchange execution, and DB logging.

Quick-start::

    from ai_prophet_core.betting import BettingEngine

    # Paper trading (default)
    engine = BettingEngine(paper=True)

    # Place a trade directly (paper or live based on engine config):
    result = engine.make_trade("kalshi:NASDAQ-100-GT5K", side="yes", shares=10, price=0.65)

    # Or let the strategy decide from a forecast:
    result = engine.trade_from_forecast(
        "kalshi:NASDAQ-100-GT5K", p_yes=0.72, yes_ask=0.65, no_ask=0.37
    )
"""

from .config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_BASE_URL,
    MAX_SPREAD,
    KalshiConfig,
    LiveBettingSettings,
    load_live_betting_dotenv,
)
from .engine import BetResult, BettingEngine
from .position_replay import (
    InventoryPosition,
    normalize_order,
    replay_orders_by_ticker,
    summarize_replayed_positions,
)
from .strategy import (
    BetSignal,
    BettingStrategy,
    DefaultBettingStrategy,
    PortfolioSnapshot,
    RebalancingStrategy,
)

__all__ = [
    # Engine - main entry points
    "BettingEngine",   # make_trade() and trade_from_forecast() live here
    "BetResult",
    # Strategy
    "BettingStrategy",
    "DefaultBettingStrategy",
    "RebalancingStrategy",
    "BetSignal",
    "PortfolioSnapshot",
    # Config
    "MAX_SPREAD",
    "DEFAULT_KALSHI_BASE_URL",
    "KALSHI_BASE_URL",
    "KalshiConfig",
    "LiveBettingSettings",
    "load_live_betting_dotenv",
    # Position replay
    "InventoryPosition",
    "normalize_order",
    "replay_orders_by_ticker",
    "summarize_replayed_positions",
]
