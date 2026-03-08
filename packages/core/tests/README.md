# ai-prophet-core Tests

This directory contains tests for the `ai_prophet_core` package.

## Running Tests

```bash
# From the ai-prophet packages/core directory
pytest

# Run specific test file
pytest tests/test_time.py -v

# Run with coverage
pytest --cov=ai_prophet_core --cov-report=html
```

## Test Structure

- `test_time.py` - Time utilities and tick normalization
- `test_schemas.py` - JSON schema validation
- `test_ids.py` - ID generation utilities
- `test_models.py` - Pydantic model validation

## Writing Tests

All tests use pytest. Follow these conventions:

1. One test file per module
2. Use descriptive test function names
3. Include docstrings explaining what is tested
4. Use fixtures for shared setup
5. Test edge cases and validation

Example:

```python
def test_normalize_tick():
    """Test tick boundary normalization."""
    dt = datetime(2024, 1, 15, 14, 32, 45, tzinfo=timezone.utc)
    result = normalize_tick(dt)
    assert result == datetime(2024, 1, 15, 14, 30, 0, tzinfo=timezone.utc)
```

