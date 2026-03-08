"""Live betting module — bridges agent predictions to real Kalshi orders.

Modules:
- config: Model configs, strategy constants, Kalshi credentials, pipeline mapping
- strategy: compute_bet() — the core per-market betting decision
- hook: LiveBettingHook — aggregates forecasts from the ExperimentRunner pipeline
        and routes bets to Kalshi
"""

from .config import (
    BETTING_MODEL_SPECS,
    KALSHI_API_KEY_ID,
    KALSHI_BASE_URL,
    MAX_SPREAD,
    MODEL_CONFIGS,
    PIPELINE_MODEL_SPECS,
    get_pipeline_config,
)
from .hook import LiveBettingHook
from .strategy import compute_bet

__all__ = [
    "MODEL_CONFIGS",
    "MAX_SPREAD",
    "KALSHI_BASE_URL",
    "KALSHI_API_KEY_ID",
    "PIPELINE_MODEL_SPECS",
    "BETTING_MODEL_SPECS",
    "get_pipeline_config",
    "compute_bet",
    "LiveBettingHook",
]
