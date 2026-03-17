# Trade Benchmark And Agent Architecture RFC

Status: draft

Scope:

- `packages/cli/ai_prophet/trade`
- context from `ai-prophet-internal/core_api`
- context from `ai-prophet-internal/indexer`
- context from `ai-prophet-internal/benchmark_server`
- context from `ai-prophet-internal/shared/db_schema.py`

## Executive Summary

AI Prophet has two related but distinct product goals:

1. serve as a foundational benchmark for models on prediction markets
2. serve as a foundation for people building prediction-market agents

Those goals should share data, execution primitives, and telemetry, but they
should not collapse into one abstraction.

This RFC makes three main recommendations:

1. keep the current benchmark kernel intact
2. simplify the current trade-agent refactor into a small bounded adaptive loop
   inside `AgentPipeline`
3. treat ticks as benchmark infrastructure, not the identity of the long-term
   agent system

For the current benchmark refactor, keep:

- `runner.py` as the outer authority for tick claiming, plan persistence,
  submission, and finalization
- `TickContext` as the immutable per-tick snapshot
- the current reasoning buckets for compatibility:
  `review`, `search`, `forecasts`, `decisions`
- the existing review, search, forecast, and action logic as reusable
  implementations behind the new executor

For the current benchmark refactor, change only:

- the inner execution strategy inside the trade agent
- from a fixed `review -> search -> forecast -> action` sequence
- to a bounded adaptive step loop that decides the next action

The near-term goal is better benchmark performance through adaptive effort
allocation. The broader product goal is to preserve a clean path to a future
free-form paper agent mode and a later live mode.

## Product Goals

1. Build a benchmark harness that compares models fairly on prediction markets.
2. Build a foundation that lets people create and iterate on prediction-market
   agents.
3. Track performance and agent trajectory over time, not just final scores.
4. Preserve enough simplicity that the architecture remains legible and
   maintainable.

## Why This System Exists

AI Prophet exists to turn prediction markets into a serious benchmark and a
future agent substrate.

In benchmark mode, the point is not to perfectly simulate a live trader. The
point is to create a fair, replayable environment where different models see the
same market universe, the same snapshot, the same capital, and the same
execution rules, so they can be compared on research quality, forecasting,
risk-taking, and trading outcomes over time.

Prediction markets are a strong benchmark domain because they force a model to
do more than answer questions. A strong system has to decide what is worth
looking at, gather evidence efficiently, convert evidence into calibrated
probabilities, size positions, and live with the portfolio consequences of being
wrong. That makes the benchmark much richer than a static reasoning task. The
output is not just "was the answer correct", but "did the system make good
decisions under uncertainty and improve portfolio outcomes over time."

The system also exists to become a foundation for future prediction-market
agents. The current benchmark shell uses ticks and pinned snapshots because
fairness, comparability, and replayability matter. But the deeper value of the
platform is the shared substrate underneath: market data ingestion, paper
execution, fills, positions, PnL, reasoning capture, and telemetry. That
substrate can support a later free-form paper agent mode and eventually a live
mode, even if those modes are scheduled very differently from the benchmark.

So the benchmark is not the whole product. It is the controlled evaluation
environment that lets us measure models rigorously, build better agent
primitives, and accumulate the telemetry needed to evolve toward freer and
eventually more live trading systems.

## System Context

At a high level, the system has four major parts.

- `indexer` collects upstream market data, normalizes it, and produces candidate
  snapshots.
- `core_api` is the benchmark control plane and paper-execution engine. It owns
  experiments, ticks, plans, intents, fills, positions, and PnL.
- `packages/cli/ai_prophet/trade` is the model-facing benchmark client. It
  builds prompts, runs the agent logic, and submits plans and intents.
- `benchmark_server` is the read-only telemetry layer for trades, positions,
  PnL, and reasoning.

The benchmark shell is intentionally strict. It gives fairness and
reproducibility. The long-term agent platform can be looser, but it should
reuse the same execution and tracking primitives wherever possible.

## Design Principles

- Prefer simple, direct architecture over agent-framework abstractions.
- Keep the benchmark kernel narrow and stable.
- Treat ticks as benchmark infrastructure, not agent identity.
- Keep the builder-facing contract small.
- Preserve separate forecast and action observability.
- Require forecast before trade in the benchmark path.
- Make search expensive, optional, and budgeted.
- Record enough data provenance that decisions can be replayed and audited.

## Product Decisions

These decisions are locked in for the current benchmark refactor.

- Keep `runner.py` exactly as the outer authority.
- Keep `TickContext` exactly as the benchmark snapshot.
- Keep the current reasoning buckets for compatibility.
- Replace only the inner stage executor with a bounded adaptive loop.
- Reuse existing stage internals as the implementation substrate.
- Keep forecast visibility. A unified internal trace is acceptable, but the
  benchmark output must still clearly show when forecasting happened.
- Require forecast before trade execution in the benchmark path.
- Make search sparse and budgeted. The agent should be encouraged not to waste
  search calls.
- Optimize for benchmark performance first.
- Do not turn the benchmark refactor into a general agent framework.

## Non-Goals

- No changes to benchmark authority semantics in `runner.py`.
- No changes to tick claiming, leasing, completion, or idempotency semantics.
- No changes to `TickContext`.
- No changes to `plan_json["intents"]` as the benchmark contract.
- No fully open-ended, unbounded agent runtime inside the benchmark.
- No attempt to solve live Kalshi trading in this refactor.
- No claim that the benchmark tick model is the final form of a money-making
  trading agent.

## Why This Refactor

Today the code already uses provider-native tool calling inside individual
stages, but the stage ordering is hard-coded in Python. That means every tick
follows the same macro shape even when:

- a market obviously does not need search
- only one or two markets deserve deeper analysis
- a market should be forecasted without fresh external research
- the agent should stop early once it has enough confidence

The current structure is robust and benchmark-friendly. The opportunity is not
to throw it away, but to keep the benchmark shell and make the inner executor
adaptive.

## Current Public Trade Stack

### Public Surface Today

- `packages/cli/README.md` treats the `prophet` CLI as the supported public
  interface.
- `ai_prophet.trade.ExperimentRunner` is already available for advanced
  embedding, but it is explicitly not documented as the stable integration
  surface.
- This is a strong signal that the benchmark kernel should remain narrow and
  stable.

### Benchmark Kernel Today

- `packages/cli/ai_prophet/trade/main.py` wires model selection, credentials,
  client construction, and pipeline creation.
- `packages/cli/ai_prophet/trade/runner.py` is the benchmark orchestrator. It
  claims ticks, builds `TickContext`, executes a pipeline, persists the plan,
  submits intents, and finalizes the participant tick.
- `packages/cli/ai_prophet/trade/core/tick_context.py` defines the immutable
  snapshot that the agent sees for one benchmark decision window.

### Current Agent Flow

The inner flow is currently fixed:

1. review candidate markets
2. search selected markets
3. forecast searched markets
4. convert forecasts into trade decisions

That flow lives in `packages/cli/ai_prophet/trade/agent/pipeline.py`.

### Current Finite-State Architecture

Today the system is finite-state at multiple layers.

#### Layer 1: Core Benchmark Control Plane

The backend benchmark control plane is explicitly stateful.

- `shared/db_schema.py` defines experiment, tick, and participant-tick tables and
  status enums.
- `ExperimentTable` uses `CREATED`, `RUNNING`, `COMPLETED`, and `ABORTED`.
- `TickTable` uses `PENDING`, `IN_PROGRESS`, `COMPLETED`,
  `SKIPPED_NO_SNAPSHOT`, `SKIPPED_LATE`, and `FAILED_STUCK`.
- `ParticipantTickTable` uses `PENDING`, `PLANNED`, `EXECUTING`, `COMPLETED`,
  `TIMEOUT`, `FAILED`, and `SKIPPED`.
- `core_api/db/tick_claims.py` is the key lease-based state transition engine for
  benchmark ticks.
- `core_api/api/participant_ticks.py` persists per-participant plans and marks
  participant ticks terminal.

This is the outer benchmark finite-state machine. It is responsible for fairness,
restarts, idempotency, and progress accounting.

#### Layer 2: CLI Runner Lifecycle

`packages/cli/ai_prophet/trade/runner.py` implements a fixed benchmark control
flow on the client side:

1. initialize the experiment and participants
2. claim the next tick from Core
3. fetch the pinned candidate snapshot once
4. process each participant for that tick
5. generate a plan through the pipeline
6. persist the plan
7. submit intents from the authoritative plan
8. finalize the participant tick
9. ask Core to complete the tick

The participant path is also effectively finite-state:

- no pipeline means `SKIPPED`
- successful plan plus execution leads to `COMPLETED`
- timeout leads to `TIMEOUT`
- exceptions lead to `FAILED`

This layer is already the correct benchmark shell and should stay intact.

#### Layer 3: Inner Agent Pipeline

Inside the CLI benchmark agent, `packages/cli/ai_prophet/trade/agent/pipeline.py`
hard-codes a four-stage sequence:

1. `ReviewStage`
2. `SearchStage`
3. `ForecastStage`
4. `ActionStage`

This is a true ordered dependency chain, not just a loose convention.

- `ReviewStage` is the first stage and selects markets plus search queries.
- `SearchStage` requires the `review` result.
- `ForecastStage` requires the `search` result.
- `ActionStage` requires the `forecast` result.

If a stage fails, the pipeline aborts. If all stages succeed, the pipeline
extracts intents from the action result and optionally emits reasoning buckets.

This is the finite-state architecture that the current refactor is meant to
change.

#### EventStore State Vocabulary

There is also a state vocabulary in
`packages/cli/ai_prophet/trade/core/event_store.py`:

- `INITIALIZING`
- `REVIEWING`
- `SEARCHING`
- `FORECASTING`
- `ACTING`
- `SUBMITTING`
- `COMPLETED`
- `ERROR`

Important nuance:

- this vocabulary exists
- but the standard CLI benchmark path does not currently persist the full stage
  timeline, because `main.py` builds `AgentPipeline(..., event_store=None)`
- `AgentPipeline` writes `INITIALIZING` and `COMPLETED` when an event store is
  present, but the hard stage ordering itself is enforced mainly by the fixed
  stage list and stage-result dependencies

So the current system is "finite-state" primarily because of its benchmark
orchestration and hard-coded stage sequencing, not because it has a general
runtime FSM engine driving everything.

#### Why This Matters

The current refactor should change only the innermost finite-state layer.

- keep layer 1: Core benchmark control plane
- keep layer 2: runner lifecycle and benchmark shell
- replace only layer 3: the hard-coded inner stage sequence

### Existing Structured Outputs

The stages already use provider-native tool calling for structured outputs:

- `packages/cli/ai_prophet/trade/agent/stages/review.py`
- `packages/cli/ai_prophet/trade/agent/stages/search.py`
- `packages/cli/ai_prophet/trade/agent/stages/forecast.py`
- `packages/cli/ai_prophet/trade/agent/stages/action.py`
- `packages/cli/ai_prophet/trade/agent/tool_schemas.py`

So the system is not raw text-driven today. What is rigid is the macro
orchestration in Python, not the stage outputs.

### Existing Reasoning Compatibility

The current reasoning and memory path already assumes stable buckets:

- `review`
- `search`
- `forecasts`
- `decisions`

This matters because reasoning is published and memory is later distilled from
those shapes in:

- `packages/cli/ai_prophet/trade/agent/reasoning_memory.py`
- `packages/cli/ai_prophet/trade/runner.py`

## Internal Backend Investigation

This section summarizes findings from the internal backend code:

- `ai-prophet-internal/core_api`
- `ai-prophet-internal/indexer`
- `ai-prophet-internal/benchmark_server`
- `ai-prophet-internal/shared/db_schema.py`

### Backend Map

- `core_api` is the benchmark control plane and paper-execution service.
- `indexer` polls upstream market data and writes canonical market, quote, and
  candidate snapshot rows into the shared database.
- `benchmark_server` is a read-only telemetry API over the same database.
- `shared/db_schema.py` is the real shared model layer for market data,
  snapshots, experiments, ticks, fills, positions, PnL, and some live-betting
  artifacts.

### Current Data Flow

The current benchmark data path is:

1. `indexer/cli.py` runs the indexer loop, defaulting to every 300 seconds.
2. `indexer/main.py` normalizes upstream markets and writes `MarketTable` and
   `QuoteTable` rows.
3. `indexer/main.py` builds a `CandidateSetSnapshotTable` snapshot at a
   normalized tick boundary.
4. `core_api/db/tick_claims.py` claims the next benchmark tick and binds it to a
   snapshot.
5. `core_api/api/candidates.py` returns the pinned candidate universe and
   `candidate_set_id`.
6. `core_api/api/trades.py` executes paper trades against quotes selected using
   the same pinned snapshot cutoff.
7. `benchmark_server/app.py` exposes fills, PnL, positions, and reasoning for
   dashboards and analysis.

### What Is Benchmark-Specific Today

The current backend is strongly benchmark-shaped.

- `experiment_id`, `participant_idx`, and `tick_id` are pervasive identifiers.
- `core_api/db/tick_claims.py` enforces the lease-based tick model.
- `core_api/api/candidates.py` serves pinned candidate snapshots, not a general
  live market-data surface.
- `core_api/api/participant_ticks.py` is plan-first and tick-finalization
  oriented.
- `core_api/api/trades.py` expects `experiment_id`, `participant_idx`, `tick_id`,
  and `candidate_set_id`.
- `shared/db_schema.py` stores PnL on a per-tick basis in
  `PnLTimeseriesTable`.

### What Is Reusable Today

There is already a lot of reusable substrate for future agent systems.

- `core_api/execution/engine.py` is a reusable paper-execution engine.
- `core_api/execution/state.py` and `core_api/execution/ledger.py` model cash,
  positions, settlement, and mark-to-market.
- `shared/db_schema.py` already stores `quote_id` on `FillTable`, which is good
  for replayability and audit.
- `PositionTable` and `PnLTimeseriesTable` already support trajectory tracking.
- `core_api/api/participant_ticks.py` already shows the useful pattern of
  persisting plan and reasoning separately from execution.
- `benchmark_server/app.py` already exposes the telemetry surfaces we care
  about:
  `/trades`, `/pnl`, `/positions`, `/reasoning`.

### Market Data And Kalshi Findings

The benchmark path is not a live Kalshi quote API today.

- Market data is polled and DB-backed.
- Candidate selection is snapshot-based.
- Execution prices are chosen from quotes already written to the DB.
- The default benchmark data path is not a continuously streaming market-data
  system.

Kalshi-specific findings:

- `core_api/execution/adapters/kalshi.py` shows that real Kalshi order routing
  exists as an adapter.
- `indexer/main.py` includes a `run_once_kalshi()` path and
  `indexer/utils/kalshi_normalize.py` normalizes Kalshi markets and quotes.
- The benchmark platform is still not a clean source-aware live Kalshi agent
  substrate.
- The market-data read path is still primarily benchmark-windowed and
  snapshot-based.

### Freshness And Determinism Findings

These details matter for later agent-mode design.

- `indexer/cli.py` defaults to a 300 second polling interval.
- `indexer/main.py` builds snapshots at normalized tick boundaries.
- `core_api/db/tick_claims.py` currently binds a tick to the latest snapshot
  where `as_of_ts <= tick_id`, even though the frozen benchmark rules document
  describes exact `as_of_ts == tick_id` matching.
- `core_api/api/candidates.py` returns `data_asof_ts` and `candidate_set_id`,
  which is good benchmark behavior.
- `indexer/utils/kalshi_normalize.py` sets `ts` and `ingested_at` from local
  capture time, so freshness is effectively poll-time oriented, not
  exchange-timestamp oriented.

### Existing Performance And Trajectory Tracking

One important result of the backend investigation is that the platform already
has solid trajectory primitives.

- `shared/db_schema.py` has `FillTable`, `PositionTable`, and
  `PnLTimeseriesTable`.
- `benchmark_server/app.py` exposes fills, PnL, current positions, and
  reasoning.
- This means a freer agent mode does not need to invent performance tracking
  from scratch.

## Product Framing Decision

The benchmark system and the future agent system should be treated as related
products built on shared primitives, not as one abstraction.

| Mode | Purpose | Scheduler | Data shape | Execution | Tracking |
| --- | --- | --- | --- | --- | --- |
| Benchmark mode | fair model evaluation | server ticks | pinned candidate snapshots | paper execution | fills, PnL, reasoning |
| Free-form paper agent mode | agent development and trajectory tracking | agent-controlled or event-driven | latest DB-backed market state | paper execution | fills, PnL, event trail |
| Live mode | real trading | agent-controlled or event-driven | fresher source-aware market data | real execution | live fills, PnL, event trail |

The current RFC should optimize benchmark mode while preserving a clean path to
the other two.

## Ticks Are Benchmark Infrastructure, Not Agent Identity

This is a core architectural decision.

- `TickContext` is a benchmark envelope.
- `runner.py` is a benchmark scheduler.
- tick leasing and pinned snapshots are fairness machinery.

Those things are valuable for benchmark mode, but they should not define the
long-term shape of the general prediction-market agent system.

This resolves the main product tension:

- the benchmark can stay tick-based
- a future freer agent system does not need to be tick-native

## Benchmark Refactor Now

### Scope

The current refactor should only improve the built-in benchmark agent.

Keep:

- `runner.py`
- `TickContext`
- benchmark plan persistence
- current reasoning buckets
- current paper-trading submission path

Change:

- only the inner control flow inside `AgentPipeline`

### Chosen Shape

Do not build a general agent framework here.

Use a small bounded adaptive loop inside `AgentPipeline`.

High-level call chain:

1. `ExperimentRunner` builds the pipeline and constructs `TickContext`.
2. `AgentPipeline.execute()` delegates to a small internal executor.
3. The executor asks the model for one structured next action.
4. Python performs the action, updates internal state, and repeats.
5. The executor returns intents and the compatibility reasoning buckets.
6. `runner.py` persists and submits the plan exactly as it does today.

### Simplicity Decision

Version 1 should be simpler than a generic multi-tool agent runtime.

Avoid in v1:

- a generic tool registry
- a provider-agnostic multi-tool conversational runtime
- a public planner framework surface
- extra abstractions added only for hypothetical future tools

Prefer in v1:

- one bounded executor
- one internal loop state object
- one structured next-step schema
- Python-enforced rules and budgets
- reused stage internals

### Simple Adaptive Loop

Version 1 can be modeled as:

```python
while steps < max_steps:
    next_action = planner.choose_next_action(state)

    if next_action.type == "REVIEW":
        run_review(state)
    elif next_action.type == "SEARCH":
        run_search(state, next_action.market_id)
    elif next_action.type == "FORECAST":
        run_forecast(state, next_action.market_id)
    elif next_action.type == "TRADE":
        run_trade(state, next_action.market_id)
    elif next_action.type == "FINISH":
        break
```

This gets the benchmark adaptivity we want without building a mini framework.

### Step Types

Version 1 step types should stay small:

- `REVIEW`
- `SEARCH`
- `FORECAST`
- `TRADE`
- `FINISH`

This is enough to support the current benchmark design.

### Hard Rules

Python should enforce these rules, not merely the prompt.

- `REVIEW` should happen before deeper per-market work.
- `FORECAST` must happen before `TRADE` for the same market.
- `SEARCH` is optional.
- `SEARCH` should usually be capped at one call per market in v1.
- the loop must stop on budget exhaustion or safe auto-finish conditions.

### Forecast And Action Observability

The benchmark should retain visibility into whether the model actually
forecasted before acting.

Version 1 rules:

- keep `forecasts` and `decisions` as separate reasoning buckets
- keep trade decisions dependent on prior forecasts
- keep forecast outputs separate from action outputs
- do not replace those buckets with a monolithic internal trace

### Search Thriftiness

Search should be available but expensive by design.

Version 1 controls:

- cap total search calls per tick
- cap search calls per market, defaulting to one
- allow forecasting without search
- prefer review-first triage
- keep prompts explicit that search budget is scarce

Suggested default budgets:

- `max_steps = 12`
- `max_search_calls_total = 3`
- `max_search_calls_per_market = 1`
- `max_markets_with_forecasts = 5`

### Internal State

Keep internal state simple and local to the executor.

Suggested fields:

- reviewed markets
- suggested queries by market
- search summaries by market
- forecasts by market
- decisions by market
- intents
- step count
- search counters
- per-market counters

If this remains small, `LoopState` does not need to become a framework-level
concept.

## Reasoning Compatibility

The current reasoning buckets must remain intact:

- `review`
- `search`
- `forecasts`
- `decisions`

This matters because existing reasoning publication and local memory distillation
already depend on those shapes.

Version 1 rule:

- treat the current buckets as the stable benchmark compatibility surface
- optional richer trace data can be added later, but should not replace those
  buckets in this refactor
- future free-form agent modes may store additional trajectory data, but that is
  separate from the benchmark compatibility contract

## Reusing Existing Stage Logic

This refactor should extract current stage internals into reusable helper
functions or a very small service layer.

Target reuse points:

- review generation
- search execution
- search summarization
- forecast generation
- trade decision generation
- intent conversion

Desired shape:

- `ReviewStage.execute()` becomes a thin wrapper over reusable review logic
- `SearchStage.execute()` becomes a thin wrapper over reusable search logic
- `ForecastStage.execute()` becomes a thin wrapper over reusable forecast logic
- `ActionStage.execute()` becomes a thin wrapper over reusable action logic
- the new adaptive executor calls the same reusable logic

This avoids maintaining two copies of the same domain behavior.

## Proposed File-Level Changes

Prefer a small file set.

Likely additions:

- `packages/cli/ai_prophet/trade/agent/adaptive_executor.py`
- `packages/cli/ai_prophet/trade/agent/planner_schema.py`

Likely refactors:

- `packages/cli/ai_prophet/trade/agent/pipeline.py`
- `packages/cli/ai_prophet/trade/agent/stages/review.py`
- `packages/cli/ai_prophet/trade/agent/stages/search.py`
- `packages/cli/ai_prophet/trade/agent/stages/forecast.py`
- `packages/cli/ai_prophet/trade/agent/stages/action.py`

Possible extracted helper module:

- `packages/cli/ai_prophet/trade/agent/stage_ops.py`

Optional later split if the helper module grows:

- `packages/cli/ai_prophet/trade/agent/services/review.py`
- `packages/cli/ai_prophet/trade/agent/services/search.py`
- `packages/cli/ai_prophet/trade/agent/services/forecast.py`
- `packages/cli/ai_prophet/trade/agent/services/action.py`

Start simple. Split further only when the code actually needs it.

## Builder-Facing Contract

If AI Prophet is also meant to be a foundation for user-built agents, the
public seam should be small.

The right long-term builder-facing idea is closer to:

```python
execute_tick(tick_ctx: TickContext) -> AgentResult
```

Where `AgentResult` still looks like:

- `intents`
- `reasoning`

The internal adaptive loop should remain an implementation detail of the built-in
benchmark agent, not the public product surface.

## LLM Requirements For The Benchmark Refactor

Version 1 does not need a true multi-provider multi-tool runtime.

Current stage calls are already shaped like:

- one request
- one forced tool schema
- one structured output

For the adaptive benchmark loop, that is enough.

Use a single structured planner output per step, for example:

- action type
- market id if needed
- optional reason

This avoids having to first redesign every provider client around a generic
multi-tool conversation protocol.

## Performance Considerations

The planner adds overhead, so v1 must be disciplined.

This refactor only makes sense for benchmark performance if the adaptive savings
outweigh the planner cost.

Ways it can win:

- fewer search calls overall
- fewer forecasts on low-value markets
- early stop once enough opportunities are found
- less wasted action work on markets with no edge

Ways it can lose:

- planner indecision
- repeated tool churn
- unnecessary search
- extra LLM round trips that do not change decisions

Design rule:

- keep the planner prompt compact
- keep step budgets tight
- prefer deterministic validation over open-ended conversation

## Configuration

Add adaptive-loop settings under pipeline config.

Example:

```yaml
pipeline:
  mode: staged  # or adaptive
  max_steps: 12
  max_search_calls_total: 3
  max_search_calls_per_market: 1
  require_forecast_before_action: true
  auto_finish_on_budget_exhaustion: true
```

Recommended rollout behavior:

- keep the current staged executor available
- ship the new adaptive executor behind a config flag
- allow side-by-side comparisons

## Testing Plan

### Unit Tests

- planner loop respects max steps
- `decide_trade` fails without a prior forecast
- repeated search beyond budget is rejected
- compatibility reasoning buckets still compile correctly
- `finish_plan` returns stable intents

### Integration Tests

- `AgentPipeline.execute()` still returns the same outward result shape
- `runner.py` persists and submits plans unchanged
- local reasoning memory still reads `forecasts` and `decisions` successfully
- no regression in idempotent plan behavior

### Benchmark Validation

Compare staged vs adaptive on the same experiments:

- accepted intents
- search volume
- average step count
- latency per participant
- token usage
- PnL and benchmark performance

## Rollout Plan

### Phase 1

Extract reusable review/search/forecast/action helpers without changing
behavior.

### Phase 2

Introduce the small adaptive executor, but keep the staged path intact.

### Phase 3

Wire the adaptive loop behind a config flag.

### Phase 4

Add strict budget enforcement and compatibility reasoning projection.

### Phase 5

Run staged vs adaptive experiments and tune budgets.

### Phase 6

Promote the adaptive loop to default only if it shows better benchmark
performance.

### Phase 7

Write a follow-on RFC for free-form paper agent mode using the same backend
primitives.

### Phase 8

Implement freer agent-mode backend surfaces only after the benchmark path is
stable.

## Future Free-Form Paper Agent Mode

The freer agent mode should not be modeled as "benchmark ticks, but more
frequently".

It should be a separate operating mode with:

- agent-controlled wakeups or event-driven scheduling
- current DB-backed market data
- paper execution
- persistent trajectory tracking
- reasoning and event logs

It can still use the same backend family, but it should not be forced through
`experiment_id + participant_idx + tick_id` as its main mental model.

### Why A Non-Live Paper Mode Is Still Valuable

It is okay if the first freer agent mode is not fully live.

Even with polled, DB-backed data, a free-form paper agent mode would already be
useful for:

- developing agent behavior
- testing agent trajectories
- comparing prompting and control strategies
- validating risk management and position management
- building a public agent-development surface before solving live execution

## Live Mode Later

A real live mode would require more than the benchmark stack currently provides.

Likely needs:

- source-aware market-data reads
- fresher Kalshi data
- exchange timestamps distinct from ingest timestamps
- stronger live execution accounting
- a coherent live agent surface, not just benchmark endpoints plus a Kalshi
  adapter

The current internal code suggests that pieces exist, but they do not yet form
one clean live-agent platform.

## Core API Evolution For A Freer Agent System

The backend investigation suggests a reasonable future direction.

Reuse from today's system:

- execution engine
- fills with `quote_id`
- positions
- PnL timeseries
- reasoning persistence patterns
- benchmark-server telemetry ideas

Add for freer agent mode:

- explicit market data endpoints
- quote freshness metadata
- optional quote history endpoints
- agent session or account concepts separate from benchmark participants
- agent event logging for trajectory
- paper execution endpoints not tied to benchmark ticks

Possible future concepts:

- `agent_sessions`
- `agent_events`
- `paper_accounts`
- market-data version identifiers

## Future Tools

The benchmark refactor should leave room for more actions, but future tools are
not the point of version 1.

Likely later additions:

- `get_market_details`
- `get_portfolio_risk`
- `get_price_history`
- `get_market_memory`
- `compare_markets`
- `check_resolution_criteria`
- `score_information_value`

Rule:

- add them only when they improve the built-in agent or the builder surface
- do not let hypothetical future tools drive version 1 architecture

## Code Investigated

Public benchmark and CLI code:

- `packages/cli/README.md`
- `packages/cli/ai_prophet/trade/main.py`
- `packages/cli/ai_prophet/trade/runner.py`
- `packages/cli/ai_prophet/trade/core/tick_context.py`
- `packages/cli/ai_prophet/trade/agent/pipeline.py`
- `packages/cli/ai_prophet/trade/agent/stages/review.py`
- `packages/cli/ai_prophet/trade/agent/stages/search.py`
- `packages/cli/ai_prophet/trade/agent/stages/forecast.py`
- `packages/cli/ai_prophet/trade/agent/stages/action.py`
- `packages/cli/ai_prophet/trade/agent/tool_schemas.py`
- `packages/cli/ai_prophet/trade/agent/reasoning_memory.py`
- `packages/cli/ai_prophet/trade/llm/base.py`

Internal backend and data-path code:

- `ai-prophet-internal/core_api/api/candidates.py`
- `ai-prophet-internal/core_api/api/participant_ticks.py`
- `ai-prophet-internal/core_api/api/trades.py`
- `ai-prophet-internal/core_api/db/tick_claims.py`
- `ai-prophet-internal/core_api/db/execution.py`
- `ai-prophet-internal/core_api/execution/engine.py`
- `ai-prophet-internal/core_api/execution/state.py`
- `ai-prophet-internal/core_api/execution/ledger.py`
- `ai-prophet-internal/core_api/execution/adapters/kalshi.py`
- `ai-prophet-internal/indexer/cli.py`
- `ai-prophet-internal/indexer/main.py`
- `ai-prophet-internal/indexer/utils/kalshi_normalize.py`
- `ai-prophet-internal/indexer/utils/filters.py`
- `ai-prophet-internal/benchmark_server/app.py`
- `ai-prophet-internal/shared/db_schema.py`
- `ai-prophet-internal/docs/benchmark/ruleset_v1.md`

## Open Questions

- For benchmark mode, should the planner use the same model as the
  forecasting/action steps, or a cheaper control model?
- Should `REVIEW` remain mandatory as the first step in the adaptive benchmark
  loop?
- Should the benchmark agent be allowed to re-forecast a market after new search
  evidence, and if so how many times?
- For free-form paper mode, what should the main identity be:
  `agent_session`, `paper_account`, or something similar?
- For live mode, do we want to evolve `core_api` directly, or spin out a more
  explicit live-agent service on the same shared database primitives?

## Recommendation

Proceed in two layers.

For the current benchmark work:

- preserve the current outer benchmark shape
- keep compatibility outputs unchanged
- replace the hard-coded inner stage executor with a small bounded adaptive loop
- enforce forecast-before-action structurally
- make search sparse and budgeted
- reuse existing stage internals
- keep the implementation simple and direct

For the broader product direction:

- keep benchmark mode as the fair evaluation harness
- later add a free-form paper agent mode on shared backend primitives
- only after that, pursue a live mode with fresher Kalshi data and stronger live
  execution support

This is the lowest-risk path to better benchmark performance while still
preserving a credible foundation for future prediction-market agents.
