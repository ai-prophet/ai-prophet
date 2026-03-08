"""Tests for schema validation.

Note: Tests use the current schema format (v1):
- forecast: probability-only (p_yes + rationale)
- trade_decision: separate action stage output
- review: batched format with schema_version
"""

from ai_prophet_core.schemas import validate_schema, is_valid_schema, SchemaLoader


def test_validate_review_schema():
    """Test review schema validation (v1 batched format)."""
    # Valid review output
    valid = {
        "schema_version": "v1",
        "review": [
            {
                "market_id": "market_123",
                "priority": 85,
                "queries": ["query 1", "query 2"],
                "rationale": "High volume spike"
            }
        ]
    }
    validate_schema("review", valid)  # Should not raise
    assert is_valid_schema("review", valid) is True
    
    # schema_version is optional (default in Pydantic, not required in JSON schema)
    without_version = {
        "review": [
            {
                "market_id": "market_123",
                "priority": 85,
                "queries": ["query 1"],
                "rationale": "reason"
            }
        ]
    }
    assert is_valid_schema("review", without_version) is True

    # Invalid (extra field on review item)
    invalid = {
        "review": [
            {
                "market_id": "market_123",
                "priority": 85,
                "queries": ["query 1"],
                "rationale": "reason",
                "unknown_field": True
            }
        ]
    }
    assert is_valid_schema("review", invalid) is False


def test_validate_forecast_schema():
    """Test forecast schema validation (v1 probability-only)."""
    # Valid forecast
    valid = {
        "schema_version": "v1",
        "p_yes": 0.73,
        "rationale": "Based on analysis..."
    }
    validate_schema("forecast", valid)
    assert is_valid_schema("forecast", valid) is True
    
    # Invalid p_yes (out of range)
    invalid = {
        "schema_version": "v1",
        "p_yes": 1.5,  # > 1.0
        "rationale": "..."
    }
    assert is_valid_schema("forecast", invalid) is False


def test_validate_trade_decision_schema():
    """Test trade decision schema validation (v1)."""
    # Valid trade decision
    valid = {
        "schema_version": "v1",
        "recommendation": "BUY_YES",
        "size_usd": 100.0,
        "rationale": "Good edge on this market"
    }
    validate_schema("trade_decision", valid)
    assert is_valid_schema("trade_decision", valid) is True
    
    # Invalid (bad recommendation enum)
    invalid = {
        "schema_version": "v1",
        "recommendation": "INVALID",
        "size_usd": 100.0,
        "rationale": "..."
    }
    assert is_valid_schema("trade_decision", invalid) is False


def test_schema_loader_caching():
    """Test that schemas are cached."""
    loader = SchemaLoader()
    schema1 = loader.load("review")
    schema2 = loader.load("review")
    assert schema1 is schema2  # Same object (cached)
