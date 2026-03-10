# ai-prophet-core

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI: ai-prophet-core](https://img.shields.io/badge/PyPI-ai--prophet--core-blue.svg)](https://pypi.org/project/ai-prophet-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/ai-prophet/ai-prophet/blob/main/LICENSE)

Typed SDK for interacting with the Prophet Arena API.

## Install

```bash
python -m pip install ai-prophet-core
```

For local development from this repository:

```bash
python -m pip install -e packages/core
```

## Live Betting

`ai-prophet-core` also ships `ai_prophet_core.betting` for forecast aggregation,
order routing, and local persistence. The public API is explicit: importing the module
does not enable trading or load dotenv files. Callers opt in by constructing a
`BettingEngine` directly or by loading `LiveBettingSettings` from the environment.

Common environment variables:

- `LIVE_BETTING_ENABLED` for CLI-side enablement
- `LIVE_BETTING_DRY_RUN` with `true` as the safe default
- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY_B64` for the base64-encoded Kalshi private key
- `KALSHI_BASE_URL` to override the default Kalshi endpoint
- `DATABASE_URL` to override the default local SQLite database

If you want dotenv-backed local development, call `LiveBettingSettings.from_env()`.
That helper will honor `LIVE_BETTING_DOTENV_PATH` or `LIVE_BETTING_LOAD_DOTENV=true`
when it reads process configuration.

## Quickstart

```python
from ai_prophet_core import ServerAPIClient

client = ServerAPIClient(base_url="https://api.prophetarena.co")
health = client.health_check()
print(health.status)
```
