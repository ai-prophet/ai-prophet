"""Unit tests for SchemaValidator."""

import jsonschema
import pytest

from ai_prophet.trade.agent.validator import SchemaValidator


def test_validate_review_valid():
    """Test validation of valid review response."""
    validator = SchemaValidator()

    review = {
        "review": [
            {
                "market_id": "123",
                "priority": 80,
                "queries": ["query 1", "query 2"],
                "rationale": "Good opportunity"
            }
        ]
    }

    # Should not raise
    validator.validate_review(review)


def test_validate_review_empty():
    """Test validation of empty review list."""
    validator = SchemaValidator()

    review = {"review": []}

    # Should not raise (empty list is valid)
    validator.validate_review(review)


def test_validate_review_invalid_priority():
    """Test validation rejects invalid priority."""
    validator = SchemaValidator()

    review = {
        "review": [
            {
                "market_id": "123",
                "priority": 150,  # Invalid: > 100
                "queries": ["query"],
                "rationale": "Test"
            }
        ]
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate_review(review)


def test_validate_review_missing_field():
    """Test validation rejects missing required field."""
    validator = SchemaValidator()

    review = {
        "review": [
            {
                "market_id": "123",
                # Missing priority
                "queries": ["query"],
                "rationale": "Test"
            }
        ]
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate_review(review)


def test_validate_forecast_valid():
    """Test validation of valid forecast."""
    validator = SchemaValidator()

    forecast = {
        "p_yes": 0.65,
        "rationale": "Strong signals support YES outcome"
    }

    # Should not raise
    validator.validate_forecast(forecast)


def test_validate_forecast_invalid_probability():
    """Test validation rejects invalid probability."""
    validator = SchemaValidator()

    forecast = {
        "p_yes": 1.5,  # Invalid: > 1.0
        "rationale": "Test"
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate_forecast(forecast)


def test_validate_trade_decision_valid():
    """Test validation of valid trade decision."""
    validator = SchemaValidator()

    decision = {
        "recommendation": "BUY_YES",
        "size_usd": 50.0,
        "rationale": "Edge detected"
    }

    # Should not raise
    validator.validate_trade_decision(decision)


def test_validate_trade_decision_invalid_enum():
    """Test validation rejects invalid recommendation."""
    validator = SchemaValidator()

    decision = {
        "recommendation": "INVALID_ACTION",  # Invalid
        "size_usd": 50.0,
        "rationale": "Test"
    }

    with pytest.raises(jsonschema.ValidationError):
        validator.validate_trade_decision(decision)


def test_validate_search_valid():
    """Test validation of valid search result."""
    validator = SchemaValidator()

    search = {
        "summary": "Market analysis summary",
        "key_points": ["Point 1", "Point 2"],
        "open_questions": ["Question 1"]
    }

    # Should not raise
    validator.validate_search(search)


def test_validate_search_empty_key_points():
    """Test validation allows empty key_points."""
    validator = SchemaValidator()

    search = {
        "summary": "No detailed findings",
        "key_points": [],
        "open_questions": []
    }

    # Should not raise
    validator.validate_search(search)
