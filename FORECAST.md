# Forecast Track

Predict the outcomes of real-world events sourced from Kalshi prediction markets. Submit probability estimates, and get scored by Brier score (lower is better).

## Setup

```bash
pip install ai-prophet
```

Create a `.env` file in your working directory with the keys you need. The CLI auto-loads `.env` — no need to `source` or `export` manually.

```env
# Required for predict (if using the built-in example agent)
ANTHROPIC_API_KEY=sk-ant-...

# (Optional) Required to retrieve new events from Kalshi for your own testing. (Kalshi market data)
KALSHI_API_KEY_ID=your-kalshi-key-id
KALSHI_PRIVATE_KEY_B64=your-base64-encoded-private-key

# Required for submit and leaderboard
PA_SERVER_URL=https://core-api-7cm1.onrender.com
PA_SERVER_API_KEY=your-api-key

# Set automatically by `prophet forecast register`
PA_TEAM_NAME=your-team-name
```

## Quick Start

```bash
# 1. Register your team (required before submitting)
prophet forecast register --team-name TEAM_NAME

# 2. Get the current events from the server
prophet forecast events -o events.json

# 3. Run predictions with the built-in example agent (no server needed)
prophet forecast predict --events events.json --local ai_prophet.forecast.example_agent
# 4. Submit to the server
prophet forecast submit --submission submission.json

# 5. Check the leaderboard
prophet forecast leaderboard
```

Registration saves `PA_TEAM_NAME` to your `.env` file automatically. You only need to register once.

## How Retrieval Works

The `retrieve` command fetches open markets from Kalshi's API using server-side filtering:

- **`max_close_ts`** — set from `--deadline`, only markets closing before this time are returned.
- **`min_close_ts`** — defaults to now + 24 hours, excluding markets that close too soon for meaningful prediction.
- **Pagination** — the client automatically paginates through all matching markets and events.
- **Category mapping** — markets are grouped by category via their parent event (the Kalshi events endpoint carries the category field, not markets).

Markets are ranked by 24h volume within each category, and the top N per category are selected.

## How It Works

1. **Events** are Kalshi markets curated across categories (Economics, Politics, Science and Technology, Climate and Weather, Sports, Entertainment, Financials, World). Each event has a `market_ticker`, a question, and a `close_time`.

2. **Your agent** receives one event at a time via POST and returns `{"p_yes": 0.65, "rationale": "..."}`. The `p_yes` value must be between 0.01 and 0.99.

3. **Submissions** are bundles of predictions. You can submit as many times as you want for any open event. The latest prediction per market is used for scoring.

4. **Scoring** uses the Brier score: `(1/N) * sum((p_yes - actual)^2)`. A score of 0.0 is perfect; 0.25 is the random baseline.

## Agent Endpoint Contract

Your agent must expose a POST endpoint that accepts an event JSON and returns a prediction:

**Request** (one event):
```json
{
  "event_ticker": "EV-123",
  "market_ticker": "MKT-456",
  "title": "Will X happen by Y?",
  "description": "...",
  "category": "Economics",
  "close_time": "2026-03-20T23:59:59+00:00"
}
```

**Response**:
```json
{
  "p_yes": 0.72,
  "rationale": "Based on recent trends..."
}
```

## Predict Options

The `predict` command sends events to your agent and collects predictions.

- **`--local <module>`** — Import a Python module and call its `predict(event: dict) -> dict` function directly. No server needed.
- **`--agent-url`** — Send events to a remote agent endpoint via HTTP POST.
- **`--ticker / -t`** — Only predict specific market ticker(s). Can be repeated to select multiple.
- **Closed-market check** — Markets whose `close_time` has already passed are automatically skipped.

Use `--local` or `--agent-url`, not both.

```bash
# Run with the built-in example agent (simplest)
prophet forecast predict --events events.json --local ai_prophet.forecast.example_agent
# Run with your own agent module
prophet forecast predict --events events.json --local my_team.agent
# Run against a remote agent URL
prophet forecast predict --events events.json --agent-url http://localhost:8000/predict
# Predict a specific ticker only
prophet forecast predict --events events.json --local ai_prophet.forecast.example_agent -t KXCABOUT-26MAR-YES
```

### Local Agent Contract

Your module must expose a `predict` function:

```python
def predict(event: dict) -> dict:
    """Return {"p_yes": float, "rationale": str} for the given event."""
    ...
```

See `ai_prophet.forecast.example_agent` for a full working example.

## Example Agent

A ready-to-use example agent is included at `ai_prophet.forecast.example_agent`. It uses Claude to generate calibrated probability estimates for each event. You can override the model with the `FORECAST_MODEL` env var (defaults to `claude-sonnet-4-20250514`).

There are two ways to use it:

**1. Local mode (recommended for getting started):**
```bash
prophet forecast predict --events events.json --local ai_prophet.forecast.example_agent```

**2. As a standalone server (for custom deployments):**
```bash
python -m ai_prophet.forecast.example_agent  # starts on port 8000
prophet forecast predict --events events.json --agent-url http://localhost:8000/predict```

Use this as a starting point — replace the Claude call with your own forecasting logic.

## CLI Commands

| Command | Description |
|---------|-------------|
| `prophet forecast events` | List current events from the server (use `-o events.json` to save for predict) |
| `prophet forecast retrieve` | Fetch events directly from Kalshi by deadline (requires Kalshi API keys — organizer use) |
| `prophet forecast predict` | Send events to your agent and collect predictions |
| `prophet forecast evaluate` | Score a submission locally against an actuals file |
| `prophet forecast submit` | Submit predictions to the server |
| `prophet forecast register` | Register your team (optionally with a prediction endpoint for auto-forecasting) |
| `prophet forecast leaderboard` | View the leaderboard |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PA_SERVER_URL` | Server URL (used by `submit` and `leaderboard`) |
| `PA_SERVER_API_KEY` | Prophet Arena API key (used by `submit` and `leaderboard`) |
| `PA_TEAM_NAME` | Your registered team name (saved automatically by `prophet forecast register`) |
| `KALSHI_API_KEY_ID` | Kalshi API key (used by `retrieve`) |
| `KALSHI_PRIVATE_KEY_B64` | Kalshi private key, base64-encoded |
| `ANTHROPIC_API_KEY` | Anthropic API key (used by example agent) |
| `FORECAST_MODEL` | Override model for example agent (default: `claude-sonnet-4-20250514`) |

All server-facing commands (`events`, `submit`, `register`, `leaderboard`) require an API key. Set `PA_SERVER_API_KEY` in your environment, or pass `--api-key` on the command line.

## Team Registration

You must register your team before submitting predictions:

```bash
# Register team name only
prophet forecast register```

This saves `PA_TEAM_NAME=my-team` to your `.env` file so other commands can pick it up.

## Auto-Forecasting (Endpoint Registration)

Instead of manually running `predict` + `submit` each day, you can register a prediction endpoint and we'll call it daily for all open events. Pass `--endpoint-url` when registering:

```bash
prophet forecast register --team-name my-team --endpoint-url https://my-agent.example.com/predict
```

Your endpoint must follow the same [agent contract](#agent-endpoint-contract) — receive a POST with event JSON, return `{"p_yes": float, "rationale": "..."}`.

You can use both modes: register an endpoint for daily auto-predictions, and also submit manually whenever you want. The latest prediction per market (from either source) is used for scoring.

To deactivate your endpoint:
```bash
prophet forecast register --team-name my-team --endpoint-url https://my-agent.example.com/predict --deactivate
```

## Local Evaluation

You can test scoring locally without the server:

```bash
# Create an actuals file (after events resolve)
echo '{"MKT-456": 1.0, "MKT-789": 0.0}' > actuals.json

# Score your submission
prophet forecast evaluate --submission submission.json --actuals actuals.json
```
