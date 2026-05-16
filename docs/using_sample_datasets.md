# Using the Sample Datasets

How to pull a ready-made slate of forecasting events into your bot using
the `prophet` CLI.

## What's available

Four samples are published in the public `ai-prophet-datasets` registry.
Each is a single dataset with one `v1.0.0` release.

| Dataset | Tasks | Resolved? | Use it for |
|---|---|---|---|
| `sample-sports` | 16 | No | Live-game and championship binaries — NBA / MLB playoffs, league titles, tennis matches |
| `sample-entertainment` | 13 | No | Multi-outcome pop-culture questions — Eurovision, Crunchyroll Anime Awards, Emmys, charts |
| `sample-economics` | 13 | No | Range-bucket macro questions — CPI prints, GDP, central-bank rates, UST yields |
| `sample-resolved` | 26 | **Yes** | A worked example of resolved outcomes (incl. multi-winner rows) you can score against |

The first three are open releases of unresolved questions: useful for
practicing the predict → submit loop end to end. `sample-resolved` is the
one you point `prophet forecast evaluate` at, because every row has a
ground-truth `resolved_outcome`.

## Quickstart

Make sure you have the `prophet` CLI installed, if not

```bash
pip install ai-prophet
```
Then you can use the `prophet forecast retrieve` command to pull the sample datasets.

```bash
# default: pulls sample-sports (latest open release)
prophet forecast retrieve -o events.json

# any of the other three
prophet forecast retrieve --dataset sample-entertainment -o events.json
prophet forecast retrieve --dataset sample-economics     -o events.json

# the resolved sample needs --include-resolved (the default filter skips
# tasks that already have a resolution)
prophet forecast retrieve \
    --dataset sample-resolved --include-resolved \
    -o resolved.json
```

Each invocation writes a JSON array of `Event` objects (the `Event`
pydantic model in `ai_prophet_core.forecast.schemas`) — the same shape
`prophet forecast predict` consumes.

You can also use `resolved.json` directly as the actuals file when
scoring local predictions:

```bash
prophet forecast evaluate \
    --submission predictions.json \
    --actuals resolved.json
```

## The event shape

`prophet forecast retrieve` normalizes the raw registry rows into the
`Event` schema before writing them out, so the JSON you read back is
**not** the same shape as `tasks.jsonl` in the `ai-prophet-datasets`
repo. The retrieved output is the source of truth — that's what every
downstream `prophet forecast …` command consumes.

One row from `sample-sports` exactly as it lands in your file:

```json
{
  "event_ticker": "KXNBAGAME-26MAY15DETCLE",
  "market_ticker": "KXNBAGAME-26MAY15DETCLE",
  "title": "Will Cleveland beat Detroit in NBA Eastern Conference Game 6 on May 15, 2026?",
  "subtitle": null,
  "description": "If Cleveland wins the Game 6: Detroit at Cleveland professional basketball game originally scheduled for May 15, 2026, then the market resolves to Yes.",
  "category": "Sports",
  "rules": "If Cleveland wins the Game 6: Detroit at Cleveland professional basketball game originally scheduled for May 15, 2026, then the market resolves to Yes.",
  "close_time": "2026-05-15T20:00:00Z",
  "outcomes": ["Cleveland", "Detroit"],
  "resolved_outcome": null
}
```

A resolved row from `sample-resolved`:

```json
{
  "event_ticker": "KXITFWMATCH-26MAY12NAJEBS",
  "market_ticker": "KXITFWMATCH-26MAY12NAJEBS",
  "title": "Who won the Najzer vs Ebster tennis match in the 2026 W15 Klagenfurt Round of 32?",
  "subtitle": null,
  "description": "If Kaja Najzer wins the Najzer vs Ebster professional tennis match in the 2026 W15 Klagenfurt Round of 32 after a ball has been played, then the market resolves to Yes.",
  "category": "Sports",
  "rules": "If Kaja Najzer wins the Najzer vs Ebster professional tennis match in the 2026 W15 Klagenfurt Round of 32 after a ball has been played, then the market resolves to Yes.",
  "close_time": "2026-05-13T09:58:49Z",
  "outcomes": ["Kaja Najzer", "Anna Lena Ebster"],
  "resolved_outcome": {
    "value": ["Anna Lena Ebster"],
    "resolved_at": "2026-05-13T17:02:27.064637+00:00",
    "source": "KXITFWMATCH-26MAY12NAJEBS"
  }
}
```

Field-by-field:

- `event_ticker` — stable id of the underlying event in the upstream
  market source (Kalshi-style ticker). May be shared by multiple markets
  in the future, but for the current samples it equals `market_ticker`.
- `market_ticker` — the unique id of this specific market/question.
  This is the key every other `prophet forecast …` command uses to join
  predictions, actuals, and submissions back to events.
- `title` — the human-readable question. Already rewritten to be
  self-contained (sport, league, date if relevant); safe to put straight
  into a prompt.
- `subtitle` — usually `null` for samples; reserved for an optional
  short qualifier under the title.
- `description` — longer natural-language description of how the market
  resolves. For the current samples it's the same string as `rules`;
  treat either one as the canonical resolution text.
- `category` — top-level topic (`"Sports"`, `"Entertainment"`,
  `"Economics"`, `"Elections"`, `"Politics"`, …).
- `rules` — the literal market-resolution criterion. Feed it to your
  bot's prompt verbatim; don't paraphrase.
- `close_time` — ISO-8601 UTC deadline for accepting forecasts.
  `prophet forecast predict` skips events whose `close_time` is already
  in the past.
- `outcomes` — the choice list. Binary questions have two entries
  (`["Yes", "No"]` or two team/contestant names); multi-outcome questions
  can have 20+ (e.g. league champions, award nominees, IPO date
  buckets).
- `resolved_outcome` — `null` for open markets, otherwise an object
  with:
  - `value` — **always a list of strings**, drawn from `outcomes`, even
    when only one outcome resolved positive (`["Anna Lena Ebster"]`,
    never the bare string). Multi-entry lists express "all of these
    resolved positive" — e.g. "top 4 finishers" in a league has 4
    entries.
  - `resolved_at` — ISO-8601 timestamp of when the upstream source
    finalized the resolution.
  - `source` — provenance string identifying which upstream source
    reported the resolution.

Two things to note if you're cross-referencing the raw `tasks.jsonl`
files in `ai-prophet-datasets`:

- The raw rows use `task_id` and `predict_by`; these are renamed to
  `market_ticker` and `close_time` in the retrieved output.
- The raw rows carry a `metadata` blob (original DB title, category
  source, etc.). `retrieve` strips it — if you need that provenance,
  read the JSONL directly from the registry repo.

## Picking a different release

Every dataset currently has exactly one release (`v1.0.0`), but the flag
is there for when more arrive:

```bash
prophet forecast retrieve --dataset sample-sports --release v1.0.0 -o events.json
```

Omit `--release` and `retrieve` picks the latest *open* release; if none
are open it falls back to the most-recent release.

## Environment-variable overrides

Useful when you're running the CLI from a script or CI job and don't want
to thread flags through every invocation:

| Variable | Default | What it sets |
|---|---|---|
| `PA_FORECAST_DATASET` | `sample-sports` | Dataset name when `--dataset` is omitted |
| `PA_FORECAST_RELEASE` | (unset → latest open) | Release id when `--release` is omitted |
| `PA_FORECAST_DATASET_BRANCH` | `main` | Branch / commit sha for remote fetches |
| `PA_FORECAST_DATASETS_REPO_PATH` | (unset → remote) | Path to a local clone for offline fetches |
| `PA_FORECAST_DATASETS_REPO_URL` | `https://github.com/ai-prophet/ai-prophet-datasets` | Override the registry repo (for forks) |

Explicit `--flag` values always win over env vars.
