"""Live betting utilities for forecast aggregation and exchange execution."""

from .config import (
    BETTING_MODEL_SPECS,
    DEFAULT_KALSHI_BASE_URL,
    KALSHI_BASE_URL,
    KalshiConfig,
    LiveBettingSettings,
    MAX_SPREAD,
    MODEL_CONFIGS,
    PIPELINE_MODEL_SPECS,
    get_pipeline_config,
    load_live_betting_dotenv,
)
from .hook import LiveBettingHook
from .strategy import compute_bet

__all__ = [
    "MODEL_CONFIGS",
    "MAX_SPREAD",
    "DEFAULT_KALSHI_BASE_URL",
    "KALSHI_BASE_URL",
    "PIPELINE_MODEL_SPECS",
    "BETTING_MODEL_SPECS",
    "KalshiConfig",
    "LiveBettingSettings",
    "load_live_betting_dotenv",
    "get_pipeline_config",
    "compute_bet",
    "LiveBettingHook",
]
