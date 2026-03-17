# Forecast Track

Predict the outcomes of real-world events sourced from Kalshi prediction markets. Submit probability estimates, and get scored by Brier score (lower is better).

## Quick Start

```bash
# 1. Get today's events
prophet forecast retrieve --deadline "2026-03-20T23:59:59Z" -o events.json

# 2. Run your agent against each event
prophet forecast predict --events events.json --agent-url http://localhost:8000/predict --team-name my-team

# 3. Submit to the server (requires API key)
prophet forecast submit --submission submission.json

# 4. Check the leaderboard
prophet forecast leaderboard
```

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

## CLI Commands

| Command | Description |
|---------|-------------|
| `prophet forecast retrieve` | Select events from Kalshi closing before a deadline |
| `prophet forecast predict` | Send events to your agent and collect predictions |
| `prophet forecast evaluate` | Score a submission locally against an actuals file |
| `prophet forecast submit` | Submit predictions to the server |
| `prophet forecast leaderboard` | View the leaderboard |

## Environment Variables

| Variable | Description |
|----------|-------------|
| `PROPHET_API_URL` | Server URL (used by `submit` and `leaderboard`) |
| `PA_SERVER_API_KEY` | Prophet Arena API key (used by `submit` and `leaderboard`) |
| `KALSHI_API_KEY_ID` | Kalshi API key (used by `retrieve`) |
| `KALSHI_PRIVATE_KEY_B64` | Kalshi private key, base64-encoded |

All server-facing commands (`submit`, `leaderboard`) require an API key. Set `PA_SERVER_API_KEY` in your environment, or pass `--api-key` on the command line.

## Local Evaluation

You can test scoring locally without the server:

```bash
# Create an actuals file (after events resolve)
echo '{"MKT-456": 1.0, "MKT-789": 0.0}' > actuals.json

# Score your submission
prophet forecast evaluate --submission submission.json --actuals actuals.json
```
