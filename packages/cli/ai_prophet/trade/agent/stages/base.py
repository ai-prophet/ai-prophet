"""Base class for agent pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient


@dataclass
class StageResult:
    """Result from a pipeline stage."""
    stage_name: str
    success: bool
    data: dict[str, Any]
    error: str | None = None


class PipelineStage(ABC):
    """Abstract base class for pipeline stages.

    Each stage:
    - Takes TickContext and previous stage results
    - Calls LLM (optional)
    - Returns StageResult
    - Logs to EventStore (handled by pipeline)
    """

    def __init__(self, llm_client: LLMClient | None = None):
        """Initialize stage.

        Args:
            llm_client: LLM client (if stage needs LLM)
        """
        self.llm_client = llm_client

    @property
    @abstractmethod
    def name(self) -> str:
        """Stage name for logging."""
        pass

    @abstractmethod
    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult:
        """Execute the stage.

        Args:
            tick_ctx: Current tick context
            previous_results: Results from previous stages (keyed by stage name)

        Returns:
            Stage result with data and metadata
        """
        pass

