"""CLI for Prophet Arena client.

Usage:
    ai-prophet eval run -m openai:gpt-4o -m anthropic:claude-4 --slug prod_001
    ai-prophet health
    ai-prophet progress <id>
"""

import logging
import os
import re
import traceback
from pathlib import Path

import click
from ai_prophet_core.client import ServerAPIClient

from ai_prophet.agent.pipeline import AgentPipeline
from ai_prophet.cli.dashboard import open_dashboard
from ai_prophet.core.config import ClientConfig
from ai_prophet.core.credentials import (
    Credentials,
    load_dotenv_file,
    normalize_provider_name,
)
from ai_prophet.llm import create_llm_client
from ai_prophet.runner import ExperimentRunner, compute_config_hash
from ai_prophet.search import SearchClient

logger = logging.getLogger(__name__)


def _bump_slug(slug: str) -> str:
    """Increment version suffix: baseline_v01 → baseline_v02, foo → foo_v2."""
    m = re.search(r'_v(\d+)$', slug)
    if m:
        n = int(m.group(1)) + 1
        return slug[:m.start()] + f"_v{n:02d}"
    return f"{slug}_v2"


def _split_model_spec(model_spec: str) -> tuple[str, str]:
    """Parse ``provider:model`` specs, defaulting to OpenAI."""
    if ":" in model_spec:
        provider, model_name = model_spec.split(":", 1)
        return provider, model_name
    return "openai", model_spec


def _validate_model_credentials(model_configs: list[dict], creds: Credentials) -> None:
    """Fail fast when requested models do not have matching credentials."""
    missing: dict[str, str] = {}

    for model_cfg in model_configs:
        model_spec = str(model_cfg["model"])
        provider, _model_name = _split_model_spec(model_spec)
        llm_provider = normalize_provider_name(provider)
        if not creds.has_api_key(llm_provider):
            missing[llm_provider] = f"{llm_provider.upper()}_API_KEY"

    if missing:
        missing_details = ", ".join(
            f"{provider} ({env_key})"
            for provider, env_key in sorted(missing.items())
        )
        raise click.ClickException(
            "Missing API credentials for requested model providers: "
            f"{missing_details}"
        )


def _setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.ERROR)
    logging.getLogger("httpcore").setLevel(logging.ERROR)
    logging.getLogger("trafilatura.main_extractor").setLevel(logging.WARNING)


@click.group()
def cli():
    """Prophet Arena Client."""
    pass


@cli.group(name="eval")
def eval_group():
    """Evaluation commands."""
    pass


def _run_options(command_func):
    options = [
        click.option("--models", "-m", multiple=True, required=True, help="Model specs (e.g., openai:gpt-4o)"),
        click.option("--slug", "-s", required=True, help="Experiment slug (stable across restarts)"),
        click.option("--replicates", "-r", type=int, default=1, help="Replicates per model"),
        click.option("--max-ticks", "-t", type=int, default=96, help="Target completed ticks"),
        click.option("--starting-cash", type=float, default=10000.0, help="Per-participant starting cash"),
        click.option("--trace-dir", type=click.Path(), default=None, help="Local trace directory"),
        click.option("--publish-reasoning", is_flag=True, help="Persist per-stage reasoning in plan_json"),
        click.option("--dashboard", is_flag=True, help="Open local dashboard in browser alongside the run"),
        click.option("--api-url", default=None, help="Core API URL"),
        click.option("-v", "--verbose", is_flag=True, help="Verbose output"),
    ]
    for option in reversed(options):
        command_func = option(command_func)
    return command_func


def _load_runtime_credentials() -> Credentials:
    """Load CLI credentials after applying dotenv overrides."""
    load_dotenv_file()
    return Credentials.from_env()


def _run_impl(models, slug, replicates, max_ticks, starting_cash, trace_dir, publish_reasoning, dashboard, api_url, verbose):
    _setup_logging(verbose)

    client_config = ClientConfig.load_runtime()
    creds = _load_runtime_credentials()
    api_url = api_url or creds.server_url

    model_configs = []
    for spec in models:
        for rep in range(replicates):
            model_configs.append({"model": spec, "rep": rep})

    _validate_model_credentials(model_configs, creds)

    config = {
        "models": list(models),
        "replicates": replicates,
        "starting_cash": starting_cash,
    }

    trace_path = Path(trace_dir) if trace_dir else None

    click.echo(f"Experiment: {slug}")
    click.echo(f"Models: {', '.join(models)} x {replicates} rep(s) = {len(model_configs)} participants")
    click.echo(f"Target: {max_ticks} ticks")
    click.echo(f"API: {api_url}")

    # If slug is completed or conflicts (different config), auto-bump.
    api = ServerAPIClient(base_url=api_url)
    config_hash = compute_config_hash(config)
    try:
        resp = api.create_or_get_experiment(
            slug=slug, config_hash=config_hash, config_json=config, n_ticks=max_ticks,
        )
        if not resp.created and resp.status == "COMPLETED":
            slug = _bump_slug(slug)
            click.echo(f"Previous experiment completed. Starting new: {slug}")
    except SystemExit:
        raise
    except Exception as e:
        if "409" in str(e):
            slug = _bump_slug(slug)
            click.echo(f"Config changed. Starting new experiment: {slug}")
        # Otherwise: can't reach API yet -- runner.init() will handle it.
    finally:
        api.close()

    if dashboard:
        open_dashboard(api_url=api_url, slug=slug)

    click.echo()

    hook = _get_shared_live_hook(model_configs)

    runner = ExperimentRunner(
        api_url=api_url,
        experiment_slug=slug,
        models=model_configs,
        config=config,
        n_ticks=max_ticks,
        starting_cash=starting_cash,
        trace_dir=trace_path,
        build_pipeline=_make_pipeline_builder(creds, client_config, verbose, api_url),
        publish_reasoning=publish_reasoning,
        live_betting_hook=hook,
        client_config=client_config,
        memory_dir=Path(os.environ.get("PA_MEMORY_DIR", "~/.pa_memory")).expanduser(),
        memory_max_rows=int(os.environ.get("PA_MEMORY_MAX_ROWS", "1000")),
    )
    try:
        runner.run()
    except Exception as e:
        click.echo(f"\nFATAL: {type(e).__name__}: {e}", err=True)
        traceback.print_exc()
        raise SystemExit(1) from e


@cli.command(hidden=True)
@_run_options
def run(models, slug, replicates, max_ticks, starting_cash, trace_dir, publish_reasoning, dashboard, api_url, verbose):
    """Legacy alias for `eval run`."""
    _run_impl(models, slug, replicates, max_ticks, starting_cash, trace_dir, publish_reasoning, dashboard, api_url, verbose)


@eval_group.command(name="run")
@_run_options
def eval_run(models, slug, replicates, max_ticks, starting_cash, trace_dir, publish_reasoning, dashboard, api_url, verbose):
    """Run an experiment. Restarts resume from where they left off."""
    _run_impl(models, slug, replicates, max_ticks, starting_cash, trace_dir, publish_reasoning, dashboard, api_url, verbose)


_live_hook_holder: dict = {}

def _get_shared_live_hook(model_configs: list[dict] | None = None):
    """Create or return the shared LiveBettingHook.

    Args:
        model_configs: List of participant model config dicts from the experiment.
            Used to filter BETTING_MODEL_SPECS to only models actually running.
    """
    if "hook" in _live_hook_holder:
        return _live_hook_holder["hook"]

    try:
        from ai_prophet.live_betting.config import BETTING_MODEL_SPECS
        from ai_prophet.live_betting.db import create_db_engine
        from ai_prophet.live_betting.hook import (
            LIVE_BETTING_DRY_RUN,
            LIVE_BETTING_ENABLED,
            LiveBettingHook,
        )

        if not LIVE_BETTING_ENABLED:
            click.echo("[LIVE BETTING] Hook DISABLED (LIVE_BETTING_ENABLED=false)")
            _live_hook_holder["hook"] = None
            return None

        # Only count betting models that are actually in this experiment
        if model_configs:
            experiment_models = {cfg["model"] for cfg in model_configs}
            active_betting_models = [
                m for m in BETTING_MODEL_SPECS if m in experiment_models
            ]
        else:
            active_betting_models = BETTING_MODEL_SPECS

        if not active_betting_models:
            click.echo("[LIVE BETTING] Hook DISABLED — no betting models in experiment")
            _live_hook_holder["hook"] = None
            return None

        click.echo(
            f"[LIVE BETTING] Hook ENABLED — dry_run={LIVE_BETTING_DRY_RUN}, "
            f"models={active_betting_models} ({len(active_betting_models)} of {len(BETTING_MODEL_SPECS)} configured)"
        )

        # Use the same DATABASE_URL as the core_api / benchmark_server
        db_engine = create_db_engine()

        hook = LiveBettingHook(
            betting_model_names=active_betting_models,
            db_engine=db_engine,
            dry_run=LIVE_BETTING_DRY_RUN,
        )
        click.echo("[LIVE BETTING] Hook created successfully")
        _live_hook_holder["hook"] = hook
        return hook
    except Exception as e:
        click.echo(f"[LIVE BETTING] Hook FAILED to create: {type(e).__name__}: {e}", err=True)
        logger.warning(f"Live betting hook unavailable: {e}", exc_info=True)
        _live_hook_holder["hook"] = None
        return None

def _make_pipeline_builder(
    creds: Credentials,
    client_config: ClientConfig,
    verbose: bool,
    api_url: str,
):
    """Return a callable that builds an AgentPipeline for a participant config.

    For live-betting models (identified by PIPELINE_MODEL_SPECS in
    live_betting/config.py), the pipeline is configured with the
    model's avoid_market_search / include_market_stats flags, and an
    on_forecast callback is wired to the shared LiveBettingHook.
    """
    def builder(participant_cfg: dict):
        model_spec = participant_cfg["model"]

        # Check if this is a live-betting model with custom config
        betting_cfg = None
        try:
            from ai_prophet.live_betting.config import get_pipeline_config
            betting_cfg = get_pipeline_config(model_spec)
            if betting_cfg:
                logger.info(f"Loaded betting config for {model_spec}: provider={betting_cfg['provider']}, model={betting_cfg['api_model']}")
        except Exception as e:
            logger.warning(f"Could not load betting config for {model_spec}: {e}")

        # Resolve provider and model name
        if betting_cfg:
            # Use the actual api_model from betting config
            provider = betting_cfg["provider"]
            llm_provider = normalize_provider_name(provider)
            model_name = betting_cfg["api_model"]
        else:
            provider, model_name = _split_model_spec(model_spec)
            llm_provider = normalize_provider_name(provider)

        api_key = creds.get_api_key(llm_provider)
        if not api_key:
            raise click.ClickException(
                f"No API key found for provider '{llm_provider}'. "
                f"Set the {llm_provider.upper()}_API_KEY environment variable."
            )

        llm_client = create_llm_client(
            provider=llm_provider, model=model_name, api_key=api_key,
            verbose=verbose,
            config=client_config.llm,
        )

        search_client = None
        if creds.brave_api_key:
            search_client = SearchClient(
                api_key=creds.brave_api_key,
                config=client_config.search,
            )
        # IMPORTANT: use the resolved api_url (CLI flag overrides env defaults).
        api_client = ServerAPIClient(base_url=api_url)

        # Build pipeline config
        pipeline_config: dict = {
            "search_client": search_client,
            "max_queries_per_market": client_config.search.max_queries_per_market,
            "max_results_per_query": client_config.search.max_results_per_query,
            "max_markets": client_config.pipeline.max_markets,
            "min_size_usd": client_config.pipeline.min_size_usd,
        }

        # Add live-betting callback if this is a betting model
        if betting_cfg:
            # Wire the on_forecast hook (shared instance)
            hook = _get_shared_live_hook()
            if hook:
                def on_forecast_cb(
                    tick_ts, market_id, p_yes, yes_ask, no_ask, question,
                    _model=model_spec, _hook=hook,
                ):
                    _hook.on_forecast(
                        model_name=_model,
                        tick_ts=tick_ts,
                        market_id=market_id,
                        p_yes=p_yes,
                        yes_ask=yes_ask,
                        no_ask=no_ask,
                        question=question,
                    )

                pipeline_config["on_forecast"] = on_forecast_cb

        pipeline = AgentPipeline(
            llm_client=llm_client,
            event_store=None,
            api_client=api_client,
            config=pipeline_config,
            client_config=client_config,
        )
        return pipeline

    return builder


@cli.command()
@click.option("--api-url", "api_url", default=None, help="Core API URL")
@click.option("--url", "legacy_url", default=None, hidden=True)
def health(api_url, legacy_url):
    """Check core API health."""
    creds = _load_runtime_credentials()
    api_url = api_url or legacy_url or creds.server_url

    click.echo(f"Checking: {api_url}")
    client = ServerAPIClient(api_url)
    try:
        resp = client.health_check()
        click.echo(f"Status:  {resp.status}")
        click.echo(f"Service: {resp.service}")
        click.echo(f"Version: {resp.version}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@cli.command()
@click.argument("experiment_id")
@click.option("--api-url", "api_url", default=None, help="Core API URL")
@click.option("--url", "legacy_url", default=None, hidden=True)
def progress(experiment_id, api_url, legacy_url):
    """Show experiment progress."""
    creds = _load_runtime_credentials()
    api_url = api_url or legacy_url or creds.server_url

    client = ServerAPIClient(api_url)
    try:
        p = client.get_progress(experiment_id)
        click.echo(f"Experiment: {p.experiment_id}")
        click.echo(f"Status:     {p.status}")
        click.echo(f"Completed:  {p.completed}/{p.n_ticks}")
        click.echo(f"Skipped:    {p.skipped}")
        click.echo(f"Failed:     {p.failed_stuck}")
        click.echo(f"In progress:{p.in_progress}")
        if p.last_completed_tick:
            click.echo(f"Last tick:  {p.last_completed_tick}")
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        raise SystemExit(1) from e
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


@cli.command()
@click.option("--api-url", default=None, help="Core API URL")
@click.option("--slug", "-s", default=None, help="Only show this experiment slug")
def dashboard(api_url, slug):
    """Open a local web dashboard for experiment results."""
    creds = _load_runtime_credentials()
    api_url = api_url or creds.server_url

    click.echo("PA Dashboard")
    open_dashboard(api_url=api_url, slug=slug or "")


def main():
    cli()


if __name__ == "__main__":
    main()
