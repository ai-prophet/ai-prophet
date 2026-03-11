"""Betting module — pluggable strategy, exchange execution, and DB logging."""

from .config import (
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_BASE_URL,
    KalshiConfig,
    LiveBettingSettings,
    MAX_SPREAD,
    load_live_betting_dotenv,
)
from .engine import BetResult, BettingEngine
from .strategy import BetSignal, BettingStrategy, DefaultBettingStrategy, PortfolioSnapshot

__all__ = [
    # Engine
    "BettingEngine",
    "BetResult",
    # Strategy
    "BettingStrategy",
    "DefaultBettingStrategy",
    "BetSignal",
    "PortfolioSnapshot",
    # Config
    "MAX_SPREAD",
    "DEFAULT_KALSHI_BASE_URL",
    "KALSHI_BASE_URL",
    "KalshiConfig",
    "LiveBettingSettings",
    "load_live_betting_dotenv",
]
