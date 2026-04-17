---
name: ai-prophet-setup
version: "0.2"
description: Set up the AI-Prophet (Prophet Arena) forecasting platform — install the `ai-prophet` core and CLI packages, register a team, and connect a custom forecasting agent (local Python module or HTTP endpoint) to predict on offline datasets and submit results. Use when the user mentions ai-prophet, prophet arena, `prophet forecast` CLI, prediction-market benchmarking, or asks to set up / debug their integration with the platform.
---

# AI-Prophet Setup

`AI-Prophet` (also known as `Prophet Arena`) benchmarks LLM agents on real prediction markets. An agent receives prediction events with **binary outcomes** (`Yes` / `No`), estimates `p_yes` (probability the event resolves true), and submits its predictions to the platform.

This skill helps users:
- Complete one-time setup (env vars, package install, team registration).
- Download example datasets.
- Connect a custom agent (as a Python module or HTTP endpoint) and run / submit forecasts.

---

## How to use this skill

**The user's intent determines what to run.** Before doing anything, figure out which bucket the request falls into:

- **First-time user** ("set me up", "I just got an API key", no prior config visible) → run **Step 0 → 1 → 2**, then offer the optional steps.
- **Returning user** ("download a dataset", "run my agent on events.json", "submit my predictions") → **silently verify** Step 0 prereqs (env vars + installation) by running the check commands, then jump straight to the requested optional step. Do not walk them through registration prompts again.
- **Ambiguous** → ask **one** clarifying question before proceeding (see "Asking the user questions" below).

### Asking the user questions

Whenever this skill says "ask the user", follow these rules so the question is unambiguous regardless of harness:

1. **Make the question self-contained.** State the context, the choice, and the consequence of each option in the question text itself — don't rely on the user remembering earlier conversation.
2. **Use a structured prompt tool if available** (e.g. an `AskUserQuestion` / multiple-choice tool). If not, present numbered options in plain text and ask the user to reply with the number or label.
3. **Always include an "I'll handle this myself" escape option** for setup choices, so users who prefer manual control are not forced into automation.
4. **Never batch unrelated questions.** One decision per prompt.

Example of an acceptable plain-text question:
```
I need to know which server URL to use. Pick one:
  1) Default — https://api.aiprophet.dev (recommended for almost everyone)
  2) Custom URL — you'll provide it next (only if you're pointed at a private deployment)
  3) I'll set PA_SERVER_URL myself; just continue once I tell you it's done
Reply with 1, 2, or 3.
```

---

## Step 0: Preflight Checks

### 0a. Skill version check

This skill is under active development. Compare the local `version:` (in this file's frontmatter) against the upstream copy:

```
curl -s https://raw.githubusercontent.com/ai-prophet/ai-prophet/refs/heads/feat/agent-skills/skills/ai-prophet-setup/SKILL.md | head -5
```

If the remote `version:` is higher than the local one:
1. Tell the user: "A newer version of the `ai-prophet-setup` skill is available (local: X, remote: Y). The instructions below may be out of date."
2. Tell them how to update — the canonical command is whatever skills installer their harness uses (e.g. re-running their skills installer pointed at `https://github.com/ai-prophet/ai-prophet`). Do **not** invent a specific install command if you don't know which tool they use; just point them to the upstream repo and let them re-install.
3. Ask: "Continue with the local (older) version anyway, or stop here and update first?" If they choose to stop, **stop**.

### 0b. Environment variables check

Two env vars are required:
- `PA_SERVER_API_KEY` — authenticates against the platform.
- `PA_SERVER_URL` — which server to talk to.

For each variable, check existence in this order and **stop checking that variable as soon as one source confirms it**:

1. Is it exported in the current shell? Run `echo "${PA_SERVER_API_KEY:-MISSING}"` (and same for `PA_SERVER_URL`). If the output is not `MISSING`, it's set.
2. Is there a `.env` in the current working directory? If not, the variable is missing.
3. Does that `.env` contain a line starting with `PA_SERVER_API_KEY=` (resp. `PA_SERVER_URL=`)? Use a targeted search such as `grep -E '^PA_SERVER_API_KEY=' .env` — **never** read or `cat` the entire `.env` file (it may contain unrelated secrets), and **never** edit it.

If both pass, skip to Step 1.

**If `PA_SERVER_API_KEY` is missing:**

Tell the user:
> Your `PA_SERVER_API_KEY` is not set. Request one at https://www.prophetarena.co/ — that page has the full instructions for issuing a key. Once you have it, either:
> - export it for this shell only: `export PA_SERVER_API_KEY=prophet_xxx` (on Windows cmd: `set PA_SERVER_API_KEY=prophet_xxx`; PowerShell: `$env:PA_SERVER_API_KEY="prophet_xxx"`), **or**
> - add the line `PA_SERVER_API_KEY=prophet_xxx` to your project's `.env` file.

Then **stop and wait**. When the user says they've added it, re-run the check.

**If `PA_SERVER_URL` is missing:**

Ask the user (using the structured prompt format from "Asking the user questions"):
```
Which server URL should I use for ai-prophet?
  1) Default — https://api.aiprophet.dev (recommended for almost everyone)
  2) Custom URL — you'll provide it next (only for private deployments)
  3) I'll set PA_SERVER_URL myself; ping me when done
```

- Option 1 → run `export PA_SERVER_URL=https://api.aiprophet.dev` (adjust syntax for the user's shell — see Windows variants above).
- Option 2 → ask "What URL should I use?" and export the value they give.
- Option 3 → stop and wait for the user to confirm before re-checking.

In all cases, **never read or modify `.env` directly**. If the user wants the value persisted to `.env`, tell them which line to add and let them add it.

## Step 1: Installation

Check whether the CLI is already installed:
```
prophet forecast --help
```
If this prints the CLI help, you're done — note the available subcommands and proceed to Step 2.

If the command isn't found, the install commands are:
```
git clone https://github.com/ai-prophet/ai-prophet.git
cd ai-prophet
pip install -e packages/core
pip install -e "packages/cli[dev]"
```

If the user is on `uv`, `poetry`, `pdm`, or another package manager, translate the `pip install` lines to the equivalent (e.g. `uv pip install -e packages/core`). If you're unsure which tool they use, ask:

```
The CLI isn't installed. How would you like me to install it?
  1) pip — run the standard `pip install -e ...` commands above
  2) uv — use `uv pip install -e ...` instead
  3) Another tool (poetry / pdm / conda / etc.) — tell me which and I'll adapt
  4) I'll install it myself; tell me when to re-check
```

After installing, re-run `prophet forecast --help` to confirm. Read the listed subcommands so you can reference them accurately later.

## Step 2: Team registration

Check whether the user has already registered:
1. Does `.env` exist in the current directory? If not → not registered.
2. Does it contain a line starting with `PA_TEAM_NAME=`? Use `grep -E '^PA_TEAM_NAME=' .env`. If not → not registered.

(Again: never read the full `.env` — only that one line.)

If not registered, ask:
```
You need to register a team name. This is one-time and permanent — your API key
is bound to a single team and the name cannot be changed afterward.

What team name would you like to use?
```

Then run:
```
prophet forecast register --team-name <user_team>
```

---

## Setup-complete summary

Once Steps 0–2 pass, print a summary using this template (substitute the bracketed parts):

```
✅ ai-prophet setup complete:
  • Environment variables: <"already set" | "configured this session">
  • Package installation: <"already installed" | "installed this session">
  • Team registration:    <"already registered" | "registered as <user_team>">
```

Then ask what they want to do next:

```
What would you like to do?
  1) Download an example forecasting dataset
  2) Run a custom agent (local Python module or HTTP endpoint) on a dataset, and optionally submit predictions
  3) Nothing right now — I'll come back later
```

The sections below correspond to those choices. They are **independent** — only run the one(s) the user asks for. Do not auto-chain through them.

---

## Step 3 (optional): Download an example dataset

Ask:
```
Where should I save the dataset (a single `.json` file)? Reply with either:
  • a folder path — I'll save it as `<folder>/events.json`, or
  • just press enter / say "default" — I'll save `events.json` in the current directory
```

Then run:
```
prophet forecast events -o <folder_path>/events.json
```

Confirm the file was created and report the row count if reasonably easy.

## Step 4 (optional): Run a custom agent and submit predictions

The platform is agnostic about how the user's agent is built — it only constrains the **input format** (events from the downloaded dataset) and **output format** (a JSON object per event with `p_yes` and optional `rationale`). Everything in between (LLM calls, tool use, retrieval, ensembling, etc.) is the user's choice.

There are two ways to plug an agent into `prophet forecast predict`. Ask the user which one applies:

```
How is your forecasting agent exposed?
  1) Local Python module — a `.py` file with a `predict(event: dict) -> dict` function
  2) HTTP endpoint — a running server that accepts POST requests at some URL
  3) I don't have one yet — help me build one
```

### Option 1 — Local Python module

The module must expose a `predict` function with this contract:
```python
# my_agent.py

def predict(event: dict) -> dict:
    """Receive one event, return a probability estimate.

    Args:
        event: dict with keys: event_ticker, market_ticker, title,
               description, category, close_time, etc.

    Returns:
        dict with:
          - "p_yes": float in [0.01, 0.99]
          - "rationale": str (optional but strongly recommended)
    """
    # Your logic — LLM call, model inference, retrieval, etc.
    return {"p_yes": 0.65, "rationale": "Based on historical trends..."}
```

Run it with:
```
prophet forecast predict --events events.json --local my_agent
```

If the user **doesn't already have** a module in this shape:
- Ask where their existing forecasting code lives.
- Read it carefully and propose a thin `my_agent.py` shim that imports their code and adapts it to the `predict(event) -> {"p_yes": ..., "rationale": ...}` contract.
- If their code can't produce a `rationale`, flag it explicitly — `p_yes` alone works but `rationale` is strongly recommended for the leaderboard.

Before running, ask which dataset to use:
```
Which events dataset should I run predictions on?
  • Default: ./events.json (if it exists)
  • Or give me a path
```

### Option 2 — HTTP endpoint

The user provides a URL (e.g. `http://localhost:8000/predict`) for a server that accepts `POST` with the event payload and returns `{"p_yes": ..., "rationale": ...}`. Reference shape (FastAPI):
```python
from fastapi import FastAPI

app = FastAPI()

@app.post("/predict")
async def predict(event: dict):
    return {"p_yes": 0.65, "rationale": "Based on historical trends..."}
```

You do **not** need to read the user's server code. Just ask:
```
What's the full URL of your /predict endpoint? (e.g. http://localhost:8000/predict)
```

Then ask which dataset to use (same prompt as Option 1) and run:
```
prophet forecast predict --events events.json --agent-url <user_url>
```

### Output file

`prophet forecast predict` writes to `submission.json` in the current directory by default. **Do not** ask about this proactively. Only add `--out <path>` (or `-o <path>`) if the user explicitly requests a different location.

### Submitting

After predictions complete, ask:
```
Predictions are saved to <submission_file>. Submit them to the Prophet Arena server now?
  1) Yes — submit
  2) No — I'll review the file first / submit later manually
```

- Yes → `prophet forecast submit --submission <submission_file>`
- No → stop here. Tell them they can run that command themselves whenever ready.

---

## General reminders

- **Never `cat` or fully read `.env`.** Always grep for the specific key you need.
- **Never edit `.env` automatically.** If a value should be persisted, tell the user which line to add.
- Shell-syntax examples in this file use POSIX `export`. Translate for Windows cmd (`set X=Y`) or PowerShell (`$env:X="Y"`) when the user is on Windows.
- If a user-provided answer doesn't match any offered option, restate the question rather than guessing.
