"""JSON schema loader and validator."""

import json
from importlib import resources
from pathlib import Path
from typing import Any

import jsonschema  # type: ignore[import-untyped]


class SchemaLoader:
    """Load and cache JSON schemas for validation.

    By default, loads schemas bundled with the ai_prophet_core package.
    Can be overridden with a custom schema directory.
    """

    def __init__(self, schema_dir: Path | None = None):
        self.schema_dir = schema_dir
        self._cache: dict[str, dict] = {}
        self._validators: dict[str, jsonschema.protocols.Validator] = {}

    def load(self, schema_name: str) -> dict:
        """Load a schema by name.

        Args:
            schema_name: Schema name without extension (e.g. 'review', 'trade_intent')

        Returns:
            Schema dict

        Raises:
            FileNotFoundError: If schema file doesn't exist
            json.JSONDecodeError: If schema is invalid JSON
        """
        if schema_name not in self._cache:
            filename = f"{schema_name}.schema.json"

            if self.schema_dir:
                # Load from explicit directory
                schema_path = self.schema_dir / filename
                with open(schema_path) as f:
                    schema = json.load(f)
            else:
                # Load from package data
                schema_files = resources.files("ai_prophet_core.schemas")
                schema_text = (schema_files / filename).read_text(encoding="utf-8")
                schema = json.loads(schema_text)

            # Validate schema itself once at load time (not per-validation).
            # Pick validator based on declared draft — our schemas use 2020-12.
            validator_cls = jsonschema.validators.validator_for(schema)
            validator_cls.check_schema(schema)

            self._cache[schema_name] = schema
            self._validators[schema_name] = validator_cls(schema)

        return self._cache[schema_name]

    def validate(self, schema_name: str, data: Any) -> None:
        """Validate data against a schema.

        Args:
            schema_name: Schema name
            data: Data to validate

        Raises:
            jsonschema.ValidationError: If validation fails
        """
        self.load(schema_name)  # ensure loaded
        self._validators[schema_name].validate(data)

    def is_valid(self, schema_name: str, data: Any) -> bool:
        """Check if data is valid against schema without raising.

        Args:
            schema_name: Schema name
            data: Data to validate

        Returns:
            True if valid, False otherwise
        """
        try:
            self.validate(schema_name, data)
            return True
        except jsonschema.ValidationError:
            return False


# Global instance
_loader: SchemaLoader | None = None


def get_loader() -> SchemaLoader:
    """Get global schema loader instance."""
    global _loader
    if _loader is None:
        _loader = SchemaLoader()
    return _loader


def validate_schema(schema_name: str, data: Any) -> None:
    """Validate data against a schema (convenience function).

    Args:
        schema_name: Schema name
        data: Data to validate

    Raises:
        jsonschema.ValidationError: If validation fails
    """
    get_loader().validate(schema_name, data)


def is_valid_schema(schema_name: str, data: Any) -> bool:
    """Check if data is valid against schema (convenience function).

    Args:
        schema_name: Schema name
        data: Data to validate

    Returns:
        True if valid, False otherwise
    """
    return get_loader().is_valid(schema_name, data)
