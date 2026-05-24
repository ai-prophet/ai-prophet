# Ensemble Forecasting Agent

A multi-strategy forecasting agent for the `prophet forecast predict` CLI.
Replaces the single-LLM-call [`example_agent`](example_agent.py) with a
research-grounded ensemble that minimizes Brier score by combining
independent estimates in log-odds space with adaptive shrinkage.

## Overview

`example_agent` makes one LLM call with the event title and no external
context. That is fast to write but loses to anything that grounds its
forecast in current information and hedges against overconfidence.

`ensemble_agent` does the work a calibrated human forecaster would:

1. Researches the event on the open web.
2. Runs three independent analytical strategies against the same brief.
3. Combines their estimates in log-odds space, weighted by self-reported
   confidence.
4. Shrinks the result toward 0.5 — more aggressively when the strategies
   disagree, less when they converge.

Brier score punishes overconfidence quadratically: being 90% wrong costs
0.81, but being 60% wrong costs only 0.36. The shrinkage step is the main
reason this agent beats a single LLM call.

## Architecture

```
                    EVENT
                      │
                      ▼
            ┌──────────────────────┐
            │   web research       │   1. Generate 5 search queries
            │   (researcher.py)    │   2. DuckDuckGo HTML search
            │                      │   3. trafilatura extraction
            │   brief ≤ 8K chars   │   4. Compile single brief
            └──────────┬───────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│  evidence-   │ │  base-rate   │ │  contrarian  │
│  weighted    │ │  analyst     │ │  analyst     │
│  (reasoning) │ │  (research)  │ │  (research)  │
└──────┬───────┘ └──────┬───────┘ └──────┬───────┘
       │                │                │
       └────────────────┼────────────────┘
                        │  3 × Estimate(p_yes, confidence, rationale)
                        ▼
            ┌────────────────────────┐
            │   ensemble.py          │   1. Filter confidence ≤ 0.15
            │                        │   2. Log-odds weighted mean
            │                        │   3. Adaptive shrinkage → 0.5
            │                        │   4. Clamp to [0.01, 0.99]
            └───────────┬────────────┘
                        ▼
                {p_yes, rationale}
```

Strategies run concurrently via `ThreadPoolExecutor`. A strategy that
crashes or returns invalid JSON is replaced with a `failed_estimate`
(confidence 0.1) and filtered out at the ensemble step. The agent never
raises; the worst case is `p_yes=0.5` with an explanatory rationale.

## Quick start

```bash
pip install -e packages/core && pip install -e "packages/cli[dev]"
export GROQ_API_KEY=gsk_...        # free at https://console.groq.com
prophet forecast predict --events events.json --local ai_prophet.forecast.ensemble_agent
```

Or run it as an HTTP service:

```bash
python -m ai_prophet.forecast.ensemble_agent
prophet forecast predict --events events.json --agent-url http://localhost:8000/predict
```

## Configuration

All configuration is via environment variables. `.env` is loaded
automatically (via `python-dotenv`).

| Variable | Default | Purpose |
|---|---|---|
| `GROQ_API_KEY` | — | Groq key. Required for research-tier calls unless OpenRouter or Anthropic is available. |
| `OPENROUTER_API_KEY` | — | OpenRouter key. Primary provider for the reasoning tier. |
| `ANTHROPIC_API_KEY` | — | Anthropic key. Final fallback in both tiers. |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model id. |
| `OPENROUTER_MODEL` | `anthropic/claude-sonnet-4` | OpenRouter model slug. |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model id. |
| `PREDICTION_DELAY` | `5` | Seconds to sleep after each `predict()` call. Set `0` to disable. Used to stay inside provider rate limits when iterating over many events. |
| `LLM_TIMEOUT_SECONDS` | `45` | Per-request timeout for every provider. |
| `LLM_RATE_LIMIT_DELAY_SECONDS` | `10` | Pause before the single retry on HTTP 429. |
| `ENSEMBLE_HOST` | `0.0.0.0` | Bind address for the FastAPI server. |
| `ENSEMBLE_PORT` | `8000` | Bind port for the FastAPI server. |

Provider chains:

- `tier="research"` → Groq → OpenRouter → Anthropic
- `tier="reasoning"` → OpenRouter → Anthropic → Groq

A 429 from any provider triggers one retry after
`LLM_RATE_LIMIT_DELAY_SECONDS`. If the retry also fails the dispatch loop
falls through to the next provider in the chain.

## Adding a custom strategy

Strategies implement a simple protocol from
[strategies/base.py](strategies/base.py):

```python
@dataclass
class Estimate:
    p_yes: float        # clamped to [0.01, 0.99]
    rationale: str      # 2-3 sentence justification
    strategy: str       # identifier, e.g. "my_strategy"
    confidence: float   # clamped to [0.1, 1.0] — used as ensemble weight


class Strategy(Protocol):
    name: str
    def estimate(self, *, title, description, category, rules,
                 close_time, research) -> Estimate: ...
```

Skeleton for a new strategy:

```python
# strategies/my_strategy.py
from __future__ import annotations

from ..llm_utils import call_llm_json
from .base import Estimate, clamp_confidence, clamp_probability, failed_estimate

STRATEGY_NAME = "my_strategy"

_SYSTEM_PROMPT = """You are a ...

Respond with ONLY a JSON object:
{"p_yes": <float 0.01-0.99>, "confidence": <float 0.1-1.0>,
 "rationale": "<2-3 sentences>"}"""


class MyStrategy:
    name = STRATEGY_NAME

    def estimate(self, *, title, description=None, category=None,
                 rules=None, close_time=None, research="") -> Estimate:
        user = f"Event: {title}\n\nResearch:\n{research}"
        try:
            data = call_llm_json(
                _SYSTEM_PROMPT, user,
                tier="research",       # or "reasoning" for higher quality
                temperature=0.3,
                max_tokens=500,
            )
        except Exception as exc:
            return failed_estimate(STRATEGY_NAME, str(exc))

        return Estimate(
            p_yes=clamp_probability(float(data["p_yes"])),
            confidence=clamp_confidence(float(data.get("confidence", 0.5))),
            rationale=str(data.get("rationale", "")).strip(),
            strategy=STRATEGY_NAME,
        )
```

Register it by adding the instance to `_build_strategies()` in
[ensemble_agent.py](ensemble_agent.py). No other wiring is needed.

## Calibration math

### Why log-odds, not linear

Linear averaging of probabilities is biased near the extremes. For two
estimates at 0.9 and 0.95:

- Linear mean: `(0.9 + 0.95) / 2 = 0.925`
- Log-odds mean: `inv_logit((logit(0.9) + logit(0.95)) / 2) ≈ 0.928`

The difference is tiny at moderate values and widens sharply near 0 and 1
— exactly where Brier penalties are quadratic. Log-odds also has the
property that an estimate of `p=0` or `p=1` infinitely dominates the
mean, which matches what "this is impossible / certain" actually means.

The implementation in [ensemble.py](ensemble.py):

```python
def logit(p):     return math.log(p / (1 - p))         # clamped p ∈ [0.01, 0.99]
def inv_logit(x): return 1 / (1 + exp(-x))             # numerically stable both signs

raw_p = inv_logit( sum(conf_i * logit(p_i)) / sum(conf_i) )
```

`confidence` acts as the ensemble weight. A strategy that reports 0.8
confidence pulls the ensemble harder than one reporting 0.3.

### Why shrinkage

Even after sound combination the result can still be overconfident — the
strategies share a research brief, share an LLM provider, and may share
biases the brief alone can't detect. Shrinkage hedges against that by
pulling the answer back toward the maximum-uncertainty value:

```python
calibrated = raw_p * (1 - s) + 0.5 * s
```

With `s = 0`, no change. With `s = 1`, collapse to 0.5. The shrinkage
factor is *adaptive*:

```python
s = BASE_SHRINKAGE * (1 - agreement * 0.5)        # BASE_SHRINKAGE = 0.10
agreement = 1 - stdev(p_i) / 0.5                  # 1 = identical, 0 = max spread
```

`BASE_SHRINKAGE = 0.10` is the empirically-tuned default, set via the
self-calibration tool (`python -m ai_prophet.forecast.calibrate`) on a
19-event resolved sample.

So:

- All three strategies at 0.75 → `agreement ≈ 1.0`, `s ≈ 0.05`. Final
  result lands near 0.74 — the strategies converged, so shrink less.
- Strategies at 0.3 / 0.55 / 0.85 → `agreement ≈ 0.55`, `s ≈ 0.07`. Final
  result is pulled noticeably harder toward 0.5 because the spread itself
  is evidence we don't know enough to be confident.

### What the rationale numbers mean

Every prediction's rationale includes the diagnostic line:

```
Ensemble p_yes=0.387 (raw=0.375, agreement=0.74, shrinkage=0.09).
```

- `raw` — log-odds weighted mean before shrinkage.
- `agreement` — `1 - stdev/0.5`, in `[0, 1]`.
- `shrinkage` — fraction of the pull toward 0.5 actually applied, in
  `[0.075, 0.15]` under the default config.

Together they make the ensemble's reasoning auditable from the
predictions file alone.

## Performance

- ~4.5s per event end-to-end (research + 3 parallel strategies + ensemble)
  on Groq Llama 3.3 70B.
- 35 unit tests covering logit/inv_logit, calibration, agreement,
  full-pipeline failure paths, the 429 retry helper, and the
  `PREDICTION_DELAY` pacing logic.
- Zero modifications to existing files. `example_agent.py` and the
  upstream CLI behave exactly as before. Swap in the new agent by
  changing `--local ai_prophet.forecast.example_agent` to
  `--local ai_prophet.forecast.ensemble_agent`.
