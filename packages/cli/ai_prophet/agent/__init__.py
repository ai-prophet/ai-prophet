"""Agent module: LLM-powered prediction market trading agent."""

from .pipeline import AgentPipeline, PipelineError
from .stages import (
    ActionStage,
    ForecastStage,
    PipelineStage,
    ReviewStage,
    SearchStage,
    StageResult,
)
from .tool_schemas import (
    FORECAST_TOOL,
    REVIEW_TOOL,
    SEARCH_SUMMARY_TOOL,
    TRADE_DECISION_TOOL,
)
from .validator import SchemaValidator

__all__ = [
    # Pipeline
    "AgentPipeline",
    "PipelineError",
    # Stages
    "PipelineStage",
    "StageResult",
    "ReviewStage",
    "SearchStage",
    "ForecastStage",
    "ActionStage",
    # Tool schemas
    "REVIEW_TOOL",
    "SEARCH_SUMMARY_TOOL",
    "FORECAST_TOOL",
    "TRADE_DECISION_TOOL",
    # Validation
    "SchemaValidator",
]

