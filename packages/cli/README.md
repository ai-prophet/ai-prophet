# AI Prophet CLI

[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![PyPI: ai-prophet](https://img.shields.io/badge/PyPI-ai--prophet-blue.svg)](https://pypi.org/project/ai-prophet/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/ai-prophet/ai-prophet/blob/main/LICENSE)

The `prophet` CLI is the entrypoint for the AI Prophet ecosystem.
Today, the primary shipped namespace is `prophet trade`, which runs the
Prophet Arena trade benchmark.

## Installation

```bash
python -m pip install ai-prophet
```

For local development from this repository:

```bash
python -m pip install -e packages/core
python -m pip install -e "packages/cli[dev]"
```

## Quick Start

```bash
# Set your LLM API keys
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
export PA_SERVER_API_KEY="prophet_..."

# Run a benchmark: 2 models, 2 replicates each, 96 ticks
prophet trade eval run \
  -m anthropic:claude-sonnet-4 \
  -m openai:gpt-5.2 \
  --replicates 2 \
  --slug my_experiment \
  --max-ticks 96
```

This creates 4 participants (2 models × 2 reps) and runs 96 fifteen-minute
ticks against the Prophet Arena API. Restarting with the same `--slug`
resumes from where it left off.

## How It Works

The client is stateless by default with respect to benchmark authority: the Core API owns experiment state, tick leasing, execution, and scoring. The client runs a 4-stage LLM pipeline for each participant on each tick:

1. **REVIEW** — Select markets for analysis from the candidate universe
2. **SEARCH** — Execute web searches and summarize findings (optional, requires Brave API key)
3. **FORECAST** — Generate calibrated probability estimates
4. **ACTION** — Convert forecasts into trade intents with position sizing

The Prophet Arena API handles execution, portfolio tracking, and scoring. All LLM calls run locally on your machine — the API only sees trade intents and results, never your prompts.

Optional local components (`ClientDatabase`, `EventStore`, trace sink, local reasoning store) are included for debugging and observability, but are not required for normal CLI runs.

## CLI Reference

```bash
prophet help

prophet trade eval run [OPTIONS]
  -m, --models TEXT       Model spec: provider:model (required, repeatable)
  -s, --slug TEXT         Experiment slug (stable across restarts)
  -r, --replicates INT    Replicates per model (default: 1)
  -t, --max-ticks INT     Target completed ticks (default: 96)
  --starting-cash FLOAT   Per-participant cash (default: 10000)
  --trace-dir PATH        Local trace directory
  --publish-reasoning     Persist per-stage reasoning in plan_json
  --dashboard             Open local dashboard alongside the run
  --api-url URL           Core API URL (default: hosted Core API)
  -v, --verbose           Verbose output

prophet trade              # Show trade subcommand help
prophet trade health       # Check API connectivity
prophet trade progress <id>   # Show experiment progress
prophet trade dashboard    # Open local results dashboard
prophet forecast           # Placeholder namespace; not implemented yet
```

## Supported LLM Providers

| Provider | Example |
|----------|---------|
| Anthropic | `anthropic:claude-sonnet-4` |
| OpenAI | `openai:gpt-5.2` |
| Google | `gemini:gemini-2.5-flash` |
| xAI | `xai:grok-3` |
| Any OpenAI-compatible | `together:meta-llama/llama-3-70b` |

Unknown providers are auto-routed through the OpenAI Chat Completions API. Set `{PROVIDER}_BASE_URL` to point at your endpoint (e.g. `TOGETHER_BASE_URL=https://api.together.xyz/v1`).
For unknown providers, set `{PROVIDER}_API_KEY` as well (e.g. `TOGETHER_API_KEY=...`).

## Configuration

Default config is bundled with the package. The `prophet` CLI loads
`config.local.yaml` from your working directory when present:

```yaml
pipeline:
  max_markets: 5
  min_size_usd: 1.0

search:
  max_queries_per_market: 1
  max_results_per_query: 3

llm:
  temperature: 0.7
  max_tokens: 4096
```

## Environment Variables

CLI commands read secrets and deployment overrides from environment variables.
For local development, the CLI also loads a `.env` file into the process
environment before resolving provider credentials. Library imports do not
implicitly load `.env` files.

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | Anthropic API key |
| `OPENAI_API_KEY` | OpenAI API key |
| `GEMINI_API_KEY` | Google Gemini API key (alias: `GOOGLE_API_KEY`) |
| `XAI_API_KEY` | xAI (Grok) API key |
| `{PROVIDER}_API_KEY` | API key for OpenAI-compatible providers (e.g. `TOGETHER_API_KEY`) |
| `BRAVE_API_KEY` | Brave Search API key (optional, for web search) |
| `PA_SERVER_URL` | Override API URL |
| `PA_SERVER_API_KEY` | Core API key for authenticated benchmark requests |
| `PA_VERBOSE` | Enable verbose LLM logging |
| `PA_MEMORY_DIR` | Local reasoning memory directory (default `~/.pa_memory`) |
| `PA_MEMORY_MAX_ROWS` | Max JSONL memory rows per participant (default `1000`) |
| `{PROVIDER}_BASE_URL` | Base URL for OpenAI-compatible providers (e.g. `TOGETHER_BASE_URL`) |

## Python Integration

The supported public interface for `ai-prophet` is the `prophet` CLI.

If you need Python access to the Prophet Arena API, use `ai-prophet-core` for
the typed SDK and API client. `ai_prophet.trade.ExperimentRunner` remains
available for advanced embedding, but it expects explicit pipeline wiring and
is not the stable integration surface for this package.

## License

MIT
