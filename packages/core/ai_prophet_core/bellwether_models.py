"""Pydantic response models for the Bellwether API."""

from __future__ import annotations

from pydantic import BaseModel


class BellwetherSearchResult(BaseModel):
    """A single market from Bellwether search results."""
    ticker: str
    title: str
    category: str | None = None
    volume_usd: float | None = None
    is_matched: bool = False
    platforms: list[str] = []


class BellwetherSearchResponse(BaseModel):
    """Response from /api/search."""
    results: list[BellwetherSearchResult] = []
    total: int = 0
    query: str = ""
    category: str | None = None


class BellwetherPlatformPrices(BaseModel):
    """Per-platform YES prices."""
    polymarket: float | None = None
    kalshi: float | None = None


class BellwetherRobustness(BaseModel):
    """Market robustness / depth metrics."""
    cost_to_move_5c: float | None = None
    reportability: str | None = None


class BellwetherVWAPDetails(BaseModel):
    """Volume-weighted average price breakdown."""
    trade_count: int = 0
    total_volume: float = 0.0


class BellwetherEventMetrics(BaseModel):
    """Response from /api/events/{slug}/metrics."""
    ticker: str = ""
    title: str = ""
    bellwether_price: float | None = None
    price_tier: str | None = None
    price_label: str | None = None
    platform_prices: BellwetherPlatformPrices = BellwetherPlatformPrices()
    robustness: BellwetherRobustness = BellwetherRobustness()
    vwap_details: BellwetherVWAPDetails = BellwetherVWAPDetails()
    fetched_at: str | None = None
