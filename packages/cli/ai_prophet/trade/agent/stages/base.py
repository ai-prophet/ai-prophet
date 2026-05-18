"""Base class for agent pipeline stages."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ai_prophet.trade.core import TickContext
from ai_prophet.trade.llm import LLMClient, LLMMessage


@dataclass
class StageResult:
    """Result from a pipeline stage."""
    stage_name: str
    success: bool
    data: dict[str, Any]
    error: str | None = None


class PipelineStage(ABC):
    """Abstract base for pipeline stages.

    Subclasses implement ``name`` and ``execute``. The helpers below
    (``_ok``, ``_fail``, ``_messages``, ``_require_llm``, ``_require_stage``)
    exist to eliminate repeated boilerplate at every stage entry point.
    """

    def __init__(self, llm_client: LLMClient | None = None):
        self.llm_client = llm_client

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def execute(
        self,
        tick_ctx: TickContext,
        previous_results: dict[str, StageResult],
    ) -> StageResult: ...

    # -- shared helpers -----------------------------------------------------

    def _ok(self, data: dict[str, Any]) -> StageResult:
        return StageResult(stage_name=self.name, success=True, data=data)

    def _fail(self, error: str, data: dict[str, Any] | None = None) -> StageResult:
        return StageResult(
            stage_name=self.name, success=False, data=data or {}, error=error,
        )

    @staticmethod
    def _messages(system: str, user: str) -> list[LLMMessage]:
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]

    def _require_llm(self) -> StageResult | None:
        if self.llm_client is None:
            return self._fail(f"LLM client required for {self.name} stage")
        return None

    def _require_stage(
        self,
        previous_results: dict[str, StageResult],
        stage_name: str,
    ) -> StageResult | None:
        if stage_name not in previous_results:
            return self._fail(f"{stage_name.capitalize()} stage result not found")
        return None
