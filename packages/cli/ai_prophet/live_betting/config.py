"""
Configuration for the live betting system.

Defines model configurations, strategy constants, and Kalshi API settings.
"""

import os

# Optional .env loading for local development.
# Production environments should provide env vars directly.
try:
    from dotenv import load_dotenv
    dotenv_path = os.getenv("LIVE_BETTING_DOTENV_PATH")
    if dotenv_path:
        load_dotenv(dotenv_path)
    elif os.getenv("LIVE_BETTING_LOAD_DOTENV", "false").lower() == "true":
        load_dotenv()
except ImportError:
    pass  # dotenv not installed, use system env vars (production)

# ─── Strategy Constants ───────────────────────────────────────────────
MAX_SPREAD = 1.03          # Liquidity filter: skip markets with yes_ask + no_ask > this

# ─── Kalshi API ───────────────────────────────────────────────────────
KALSHI_BASE_URL = "https://api.elections.kalshi.com"
KALSHI_API_KEY_ID = os.getenv("KALSHI_API_KEY_ID", "")

# ─── Model Configurations ────────────────────────────────────────────
# Each model config specifies:
#   provider: "google" or "anthropic"
#   api_model: the actual model ID to call
#   avoid_market_search: whether to instruct the model not to search for market data
#   include_market_stats: whether to include current market prices in the prompt
MODEL_CONFIGS = {
    "gemini-3": {
        "provider": "google",
        "api_model": "gemini-3-pro-preview",
        "avoid_market_search": False,
        "include_market_stats": True,
    },
    "gemini-3-no-search": {
        "provider": "google",
        "api_model": "gemini-3-pro-preview",
        "avoid_market_search": True,
        "include_market_stats": False,
    },
    "anthropic/claude-opus-4.6": {
        "provider": "anthropic",
        "api_model": "claude-opus-4-6",
        "avoid_market_search": False,
        "include_market_stats": True,
    },
    "anthropic/claude-opus-4.6-no-search": {
        "provider": "anthropic",
        "api_model": "claude-opus-4-6",
        "avoid_market_search": True,
        "include_market_stats": False,
    },
}

# ─── Pipeline Model Specs ────────────────────────────────────────────
# Maps ExperimentRunner model specs ("provider:label") to the MODEL_CONFIGS
# entry and the actual api_model to call.
# The -no-search suffix indicates variants that skip market search.
PIPELINE_MODEL_SPECS = {
    "google:gemini-3-pro-preview": "gemini-3",
    "google:gemini-3-pro-preview-no-search": "gemini-3-no-search",
    "anthropic:claude-opus-4-6": "anthropic/claude-opus-4.6",
    "anthropic:claude-opus-4-6-no-search": "anthropic/claude-opus-4.6-no-search",
}

# All model specs that participate in live betting (for the hook)
BETTING_MODEL_SPECS = list(PIPELINE_MODEL_SPECS.keys())


def get_pipeline_config(model_spec: str) -> dict | None:
    """Get the MODEL_CONFIGS entry for a pipeline model spec.

    Args:
        model_spec: ExperimentRunner model spec (e.g. "google:gemini-3-pro-preview")

    Returns:
        Config dict with provider, api_model, avoid_market_search, include_market_stats
        or None if not a betting model.
    """
    config_name = PIPELINE_MODEL_SPECS.get(model_spec)
    if config_name is None:
        return None
    return MODEL_CONFIGS.get(config_name)


# ─── API Keys (loaded from .env) ─────────────────────────────────────
KALSHI_PRIVATE_KEY_B64 = os.getenv("KALSHI_API_KEY")
