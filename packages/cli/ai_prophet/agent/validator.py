"""Schema validation for LLM outputs."""

from __future__ import annotations

from typing import Any

from ai_prophet_core.schemas import get_loader


class SchemaValidator:
    """Validate LLM outputs against JSON schemas.

    Uses ai_prophet_core's bundled schema files via SchemaLoader.
    """

    def __init__(self) -> None:
        self._loader = get_loader()

    def validate_review(self, data: dict[str, Any]) -> None:
        """Validate review stage output."""
        self._loader.validate("review", data)

    def validate_search(self, data: dict[str, Any]) -> None:
        """Validate search stage output."""
        self._loader.validate("search", data)

    def validate_forecast(self, data: dict[str, Any]) -> None:
        """Validate forecast stage output."""
        self._loader.validate("forecast", data)

    def validate_trade_decision(self, data: dict[str, Any]) -> None:
        """Validate trade decision output from action stage LLM."""
        self._loader.validate("trade_decision", data)

