"""Shared Kalshi API configuration constants.

Used by both betting and forecast modules. Lives at the package root
to avoid cross-module dependencies.
"""

DEFAULT_KALSHI_BASE_URL = "https://api.elections.kalshi.com"

KALSHI_API_KEY_ID_ENV = "KALSHI_API_KEY_ID"
KALSHI_BASE_URL_ENV = "KALSHI_BASE_URL"
KALSHI_PRIVATE_KEY_B64_ENV = "KALSHI_PRIVATE_KEY_B64"
