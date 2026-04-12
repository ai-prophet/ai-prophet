# ai-prophet-core Tests

This directory contains the package test suite for `ai_prophet_core`.

## Running Tests

```bash
# From the repo root
pytest packages/core/tests -q

# From packages/core
pytest tests -q

# Run one file
pytest tests/test_client.py -q
```

## Coverage Areas

- API client behavior and wire models (`test_client.py`, `test_client_models.py`)
- SDK primitives and helpers (`test_arena.py`, `test_models.py`, `test_time.py`, `test_ids.py`, `test_decimal_utils.py`, `test_schemas.py`)
- Betting engine behavior and safety (`test_live_betting.py`, `test_dry_run_safety.py`, `test_cash_safety.py`, `test_live_position_and_contamination.py`)
- MCP tool surface (`test_mcp_server.py`)
- Public package exports (`test_package_api.py`)

## Writing Tests

Use focused pytest files with descriptive names and behavior-oriented
assertions. Prefer fixtures or local helpers when setup repeats, and cover
validation, safety, and failure paths in addition to happy paths.

```python
from datetime import UTC, datetime

from ai_prophet_core.time import normalize_tick


def test_normalize_tick():
    dt = datetime(2024, 1, 15, 14, 32, 45, tzinfo=UTC)
    assert normalize_tick(dt) == datetime(2024, 1, 15, 14, 30, 0, tzinfo=UTC)
```

