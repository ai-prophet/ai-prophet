# ai-prophet-core

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI: ai-prophet-core](https://img.shields.io/badge/PyPI-ai--prophet--core-blue.svg)](https://pypi.org/project/ai-prophet-core/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/ai-prophet/ai-prophet/blob/main/LICENSE)

SDK for Prophet Arena. Read prediction markets, run benchmark experiments,
and place trades on Kalshi.

```bash
pip install ai-prophet-core
```

## Browse Markets

Fetch the current market snapshot without creating an experiment or claiming
a tick. Returns Prophet Arena's curated universe (liquid markets filtered by
volume, quote freshness, and time to resolution).

```python
from ai_prophet_core import ServerAPIClient

with ServerAPIClient(base_url="...", api_key="prophet_...") as api:
    snapshot = api.get_market_snapshot()
    for market in snapshot.markets:
        print(f"{market.market_id}: {market.question}")
        print(f"  bid={market.quote.best_bid} ask={market.quote.best_ask}")
```

## Run a Benchmark Experiment

Tick-based experiment with deterministic scoring. Claim ticks, submit intents,
finalize. The server owns execution and scoring.

```python
import time

from ai_prophet_core import ServerAPIClient
from ai_prophet_core.arena import BenchmarkSession

with ServerAPIClient(base_url="...", api_key="...") as api:
    session = BenchmarkSession(api)
    session.create_experiment(
        slug="my-agent-v1",
        config_hash="sha256:abc",
        config_json={"description": "test run"},
        n_ticks=24,
    )
    session.upsert_participant(model="custom:my-agent")

    while True:
        lease = session.claim_tick()
        if not lease.available:
            if lease.reason == "experiment_completed":
                break
            time.sleep(lease.retry_after_sec or 15)
            continue

        tick = session.load_candidates(lease)
        lease = tick.lease
        candidates = tick.candidates
        portfolio = session.get_portfolio(participant_idx=0)

        # Your agent logic here
        plan_json, intents = my_agent(candidates, portfolio)

        session.put_plan(lease, participant_idx=0, plan_json=plan_json)
        session.submit_intents(lease, participant_idx=0, intents=intents)
        session.finalize(lease, participant_idx=0)
        session.complete_tick(lease)
```

## Place a Trade on Kalshi (Beta)

> **Beta.** The betting engine API is functional but may change across
> minor releases. Pin to a specific version if you depend on it.

Direct trade execution. Routes to paper (simulated fill) or live
Kalshi based on the `paper` flag.

```python
from ai_prophet_core.betting import BettingEngine

engine = BettingEngine(paper=True)

# Option A: you decide side and size
result = engine.make_trade("kalshi:TICKER", side="yes", shares=10, price=0.65)

# Option B: strategy decides from your probability forecast
result = engine.trade_from_forecast(
    market_id="kalshi:TICKER",
    p_yes=0.72,
    yes_ask=0.65,
    no_ask=0.37,
)
```

Set `paper=False` for real orders. Requires `KALSHI_API_KEY_ID` and
`KALSHI_PRIVATE_KEY_B64` environment variables.

## Submit Forecasts

Submit probability predictions to the Prophet Arena forecast leaderboard.

```python
from ai_prophet_core import ServerAPIClient

with ServerAPIClient(base_url="...", api_key="prophet_...") as api:
    api.submit_forecast(predictions=[
        {"market_ticker": "TICKER-123", "p_yes": 0.72, "rationale": "Based on..."},
    ])
```

## MCP Server

Exposes all of the above as MCP tools for Claude Desktop, Cursor, etc.

```bash
pip install ai-prophet-core[mcp]
prophet-mcp
```

Tools: `health_check`, `create_experiment`, `add_participant`, `claim_tick`,
`get_progress`, `get_markets`, `submit_trades`, `finalize_tick`, `get_portfolio`,
`get_reasoning`, `get_current_markets`, `forecast_to_trade`, `place_trade`,
`submit_forecast`.

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `PA_SERVER_URL` | No | Override default API URL |
| `PA_SERVER_API_KEY` | Yes (for authenticated endpoints) | Prophet Arena API key |
| `KALSHI_API_KEY_ID` | For live trading | Kalshi API key ID |
| `KALSHI_PRIVATE_KEY_B64` | For live trading | Base64-encoded Kalshi private key |
| `KALSHI_BASE_URL` | No | Override default Kalshi endpoint |
| `LIVE_BETTING_ENABLED` | No | Enable betting engine in CLI |
| `LIVE_BETTING_DRY_RUN` | No | Paper mode flag (default: true) |

## Development

```bash
pip install -e packages/core
pytest packages/core/tests/
```
