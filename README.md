# ai-prophet

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![PyPI: ai-prophet-core](https://img.shields.io/badge/PyPI-ai--prophet--core-blue.svg)](https://pypi.org/project/ai-prophet-core/)
[![PyPI: ai-prophet](https://img.shields.io/badge/PyPI-ai--prophet-blue.svg)](https://pypi.org/project/ai-prophet/)
[![Discord](https://img.shields.io/badge/Discord-join-blue.svg?logo=discord)](https://discord.gg/aTsY7979zP)

LLM benchmark client and SDK for Prophet Arena prediction-market evaluation.

## Quick Start (Ensemble Agent)

This fork adds an ensemble forecasting agent designed for Prophet Hacks 2026.
The agent runs as an HTTP service that accepts a single event or a batch of
events and returns calibrated `p_yes` predictions with rationales. Full
design notes live at [`packages/cli/ai_prophet/forecast/ENSEMBLE_AGENT.md`](packages/cli/ai_prophet/forecast/ENSEMBLE_AGENT.md).

**Build and run the container:**

```bash
docker build -t ensemble-agent .
docker run -p 8000:8000 \
    -e GROQ_API_KEY=gsk_...           \
    -e OPENROUTER_API_KEY=sk-or-...   \
    ensemble-agent
```

Optional env vars (all have sensible defaults — see [ENSEMBLE_AGENT.md](packages/cli/ai_prophet/forecast/ENSEMBLE_AGENT.md)):
`ANTHROPIC_API_KEY`, `KALSHI_API_KEY`, `PREDICTION_DELAY`, `CACHE_TTL_HOURS`,
`ENABLE_CACHE`, `ENABLE_DELIBERATION`.

**Verify liveness:**

```bash
curl http://localhost:8000/health
# → {"status":"ok","service":"ensemble-forecast-agent"}
```

**Single-event prediction:**

```bash
curl -X POST http://localhost:8000/predict \
    -H "Content-Type: application/json" \
    -d '{"event_ticker":"TEST","market_ticker":"TEST","title":"Will X happen?","category":"Test","close_time":"2026-06-01T00:00:00Z","outcomes":["Yes","No"]}'
# → {"p_yes": 0.42, "rationale": "..."}
```

**Batch prediction (post a JSON array, get an array back):**

```bash
curl -X POST http://localhost:8000/predict \
    -H "Content-Type: application/json" \
    -d '[{"market_ticker":"E1","title":"Q1?","close_time":"2026-06-01T00:00:00Z","outcomes":["Yes","No"]},
         {"market_ticker":"E2","title":"Q2?","close_time":"2026-06-01T00:00:00Z","outcomes":["Yes","No"]}]'
# → [{"p_yes":0.6,"rationale":"..."}, {"p_yes":0.3,"rationale":"..."}]
```

`POST /predictions` is an alias for `POST /predict` and supports the same
single/batch auto-detection.

**Or run it as a CLI agent against a local events file:**

```bash
pip install -e packages/core && pip install -e "packages/cli[dev]"
prophet forecast predict --events events.json --strategy ensemble
```

## Packages

- `packages/core` - typed SDK (`ai-prophet-core`) for API models and client calls
- `packages/cli` - benchmark runner and CLI package (`ai-prophet`, command `prophet`)

## Docs

- [Build a trading bot](docs/build_a_bot.md) - end-to-end guide for writing
  a custom bot against the Prophet Arena benchmark using `ai-prophet-core`
- [Using the sample datasets](docs/using_sample_datasets.md) - pull a
  ready-made event slate from `ai-prophet-datasets` via `prophet forecast retrieve`

## Local Setup

```bash
python -m pip install -e packages/core
python -m pip install -e "packages/cli[dev]"
pre-commit install
```

## Checks

```bash
ruff check --config packages/cli/pyproject.toml packages/core packages/cli
pytest packages/core/tests
pytest packages/cli/tests
```

## License

MIT. See `LICENSE`.
