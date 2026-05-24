"""Forecasting strategies that produce independent probability estimates."""

from __future__ import annotations

from .base import Estimate, Strategy
from .base_rate import BaseRateStrategy
from .contrarian import ContrarianStrategy
from .evidence import EvidenceWeightedStrategy

__all__ = [
    "Estimate",
    "Strategy",
    "EvidenceWeightedStrategy",
    "BaseRateStrategy",
    "ContrarianStrategy",
]
