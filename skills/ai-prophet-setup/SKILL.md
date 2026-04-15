---
name: ai-prophet-setup
version: "0.1"
description: Install the ai-prophet core and cli packages, perform one-time team registration, and help the user connects their custom agent or forecasting endpoint to predict on offline datasets, or connect to our platform's server. Use this tool when the user explicitly asks about setting up or debugging their integration with ai-prophet.
---

# AI-Prophet Setup

The `AI-Prophet` (also known as `Prophet Arena`) Forecasting is a platform that benchmarks LLM agents on real prediction markets. The agent will receive a set of prediction events with **binary outcomes** (i.e. `Yes` or `No`), and then it will estimates `p_yes`, i.e. the probability of the event being true, and submit its predictions.

This skill is about setting up `ai-prophet` -- i.e. helping first-time users register their team, connecting their custom agent to interface with `ai-prophet` data format, and teach them how to test their agent via offline datasets and submit their predictions to our server.

The below sections will be organized by **steps**. You should clearly understand the demands of the user (explicitly ask user questions, and make sure to use the specific `AskUserQuestion` or similar tools if you have access to one) and which step is the user asking help for (other than the Step 0-2 which are mandatory setup checks).

## Step 0: Preflight Checks

### Skill Version Check

As `ai-prophet` is under active development, make sure that you compare the local skill version against the latest on GitHub: 

```
curl -s https://raw.githubusercontent.com/ai-prophet/ai-prophet/refs/heads/feat/agent-skills/skills/ai-prophet-setup/SKILL.md | head -5
```

Check the `version:` field. If the remote version is higher than the local version:
1. Tell the user: "A newer version of the Hive skills is available (local: X, remote: Y)."
2. Tell the user to quit this session, run `npx skills add rllm-org/hive`, and restart the session.
3. **Stop here.** Do not continue unless the user wants to continue.

### Environment Variables Check

There are two crucial environment variables that need to be set: `PA_SERVER_URL` for the server URL, and `PA_SERVER_API_KEY`. Check the existence (for both env vars) via the following order:
1. Use `echo $ENV_VAR` to check explicitly (if exists -> pass; stop the check).
2. Check whether the `.env` file exists in the current workspace (if not exists -> fail; stop the check).
3. Use `grep` or other methods that can let you check for the particular lines starting with `PA_SERVER_URL=` or `PA_SERVER_API_KEY=` within the `.env` file -- you SHOULD NEVER read the whole `.env` file (e.g. by simple `cat`) since it might contain other sensitive credentials (if exists -> pass).

If the checks for both env vars pass, directly go to Step 1 and skip the below parts.

**Missing `PA_SERVER_API_KEY`**

- Tell the user to obtain an API key by visiting `https://www.prophetarena.co/`. The website will have detailed instructions on how the user can request an API key. 
- Tell the user that once the key is obtained, either export it explicitly via `export PA_SERVER_API_KEY=prophet_xxx` or put it within the `.env` file.
- **Stop here.** The user will have to get the key and once they tell you they have done the above steps, repeat the check again.

**Missing `PA_SERVER_URL`

Ask the user about which option they will go with:
1. "Set up the default server URL (https://api.aiprophet.dev)" -> if selected, use `export PA_SERVER_URL=https://api.aiprophet.dev` to set up. Then proceed to the next step.
2. "Set up for a custom server URL (be cautious, rare case)" -> if selected, read the user-provided server url and set it by `export PA_SERVER_URL=<user_provided_url>`. Proceed to nex step.
3. "User set up the URL via .env or `export`, notify me later" -> if selected, the user is responsible for setting the server URL (similar to the case of missing `PA_SERVER_API_KEY`). You can stop here and wait until the user explicitly tell you that the setup is complete (repeat above to check).

In either case, you should NEVER read the whole raw `.env` file or do any edit to it (e.g. adding new row).

## Step 1: Installation

`ai-prophet` requires the user to install two essential packages -- the `ai-prophet` CLI for easy interactions with the platform and `ai-prophet-core` for forecasting agent core components. Check whether the installation has been done by running:
```
which prophet && prophet forecast --help
```
If installed, you will also have a good understanding about the main commands in `prophet forecast` CLI. Jump to the next stage.

If not installed, know that (DO NOT RUN yet) the following commands will install the packages:
```
git clone https://github.com/yourusername/ai-prophet.git
cd ai-prophet
pip install -e packages/core
pip install -e "packages/cli[dev]"
```
(Note that the above are for vanilla `pip install`, if the user requires using tools like `uv` to manage their python requiremens, adjust these commands accordingly)

Tell the user that the necessary packages are not installed, and ask the user "Do you want me to run the above commands and install these packages for you?"
1. "Yes" -> go ahead and install, check for installation, and proceed to next stage when completed.
2. "No" -> say that you will wait for the user to manually install and come back. Then verify the installation again.

## Step 2: Registration

Check whether the user has been already registered by checking:
1. Whether the `.env` file exists in the project root (if not -> not registered).
2. Whether the `.env` file contains a row starting with `PA_TEAM_NAME=` (if not -> not registered).
(Note: again avoid reading the full `.env` file -- search & filter only a particular row)

If not registered, prompt the user to do a one-time registration by asking "Tell me what team name do you want to pick? Note that each API key is binded to one team only, and the name cannot change afterwards." to obtain the `<user_team>` from user. Then run the CLI command to register:
```
prophet forecast register --team-name <user_team>
```

---

Once Step 0 - 2 are complete, you've finished the basic validation and setup process for the user. Generate a summary of your progress so far. A summary template is provided below (contents within brackets '<xxx>' are instructions and dynamic parts of the summary).

```
The basic setups are done! 
- `environment variables`: <"already set" if passed, or "setup complete" if setup within current session>
- `package installations`: <"already installed" or "installation complete" if installed within current session>
- `team registration`: <"already registere" or "registered as <user_team>" if registered within current session>
```

Following the summary, add the following part to explicitly ask the user about what particular operations they want to perform (corresponding to the below steps):

```
With the setup done, which of the following items can I help you with?
- Download an example forecasting dataset.
- Run offline/local predictions with the downloaded dataset and (optionally) submit to the server.
```

All the sections below correspond specifically to the instructions for each of the item listed above. The order of them does NOT matter. These are NOT mandatory steps to go through anytime the skill is invoked. 
---

## Step 3 (Optional): Download example dataset

Ask the user where to store the `.json` dataset. The response <folder_path> should be a folder path, or default to current workspace. Then run the command
```
prophet forecast events -o <folder_path>/events.json
```

## Step 4 (Optional): Run custom agent on a dataset and submit to server

Our platform allows the user to implement the forecasting agent however they want: we simply enforce the input & output format (i.e. our provided dataset has a specific structure, and the returned prediction need to follow a certain `json` schema). Everything in between, including transforming the input to another format, performing LLM calls, the core agent loop itself, converting custom output format to the desirable format, etc. -- these are all left for the user to decide.

At the end of the day, there are **two approaches** for the user to forecast on a compatible dataset using the `prophet forecast` CLI commands:

**Option 1: Local Python module**

Create a Python module that exposes a `predict` function:
```python
# my_agent.py

def predict(event: dict) -> dict:
    """Receive an event, return a probability estimate.

    Args:
        event: dict with keys: event_ticker, market_ticker,
               title, description, category, close_time, etc.

    Returns:
        dict with "p_yes" (float 0.01-0.99) and
        optional "rationale" (str).
    """
    # Custom agent logic here — call an LLM, run a model,
    # query external data, etc. Or import the agent from elsewhere

    return {
        "p_yes": 0.65,
        "rationale": "Based on historical trends...",
    }
```

Run it with (for a certain `events.json` dataset):
```
prophet forecast predict \
  --events events.json \
  --local my_agent
```

For this option, if the user does not have an existing `my_agent.py` module ready, you might need to ask the user about specific information about the custom agent (where is it?). And you should then carefully read the user's custom code to help them think about a way to create this module, i.e. essentially creating a bridge between the user's custom codebase with the `ai-prophet`-specified module requirement. Clearly communicate with the user if the custom agent fails to satisfy the required format (e.g. it never produces a `rationale` for the predictions it makes). You should also ask the user about which dataset (default to the `events.json` in the project root -- if exists) the user wants to make predictions on.

**Option 2: HTTP endpoint**

The user will provide the endpoint to a (user) server that accepts POST requests with event data. An example can be a simple FastAPI-based server.

```python
# server.py
from fastapi import FastAPI

app = FastAPI()

@app.post("/predict")
async def predict(event: dict):
    # Your logic here
    return {
        "p_yes": 0.65,
        "rationale": "Based on historical trends...",
    }
```
Note that for this option, you DO NOT need to check the user's server implementation -- simply ask them to provide you with a server URL endpoint, e.g. http://localhost:8000/predict, then you will run
```
prophet forecast predict \
  --events events.json \
  --agent-url http://localhost:8000/predict
```
to predict on the problems in `events.json` (or any other dataset). Make sure to ask the user about which dataset to predict on.

**Forecasting flags**

With either approach, the `prophet forecast predict` CLI offers the option to add a `--out/-o` flag to specify the output (result) file path, default to the `submission.json` in the current folder. DO NOT ask the user about this option proactively, but DO add the flag if the user has mentioned and requested a different path explicitly.

**Submitting the predictions**

Finally, once the forecasting is done (with either approach), you should ask the user whether the predictions should be submitted (let <submission_file> be the output file path).
- Yes -> run `prophet forecast submit --submission <submission_file>` to submit results to the server.
- No -> stop here.
