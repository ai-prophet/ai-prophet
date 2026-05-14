"""Forecast CLI for the Prophet Arena hackathon.

Usage:
    prophet forecast retrieve --deadline "2026-03-15T23:59:59Z" --output events.json
    prophet forecast predict --events events.json --agent-url http://localhost:8000/predict
    prophet forecast evaluate --submission submission.json --actuals actuals.json
    prophet forecast submit --submission submission.json --server-url http://localhost:8000
    prophet forecast leaderboard --server-url http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import click
import requests
from ai_prophet_core.client import ServerAPIClient
from ai_prophet_core.forecast.calibrate import (
    apply_calibration,
    fit_calibration,
    load_calibration,
    save_calibration,
)
from ai_prophet_core.forecast.evaluate import load_actuals, load_submission, score
from ai_prophet_core.forecast.kalshi_client import KalshiForecastClient
from ai_prophet_core.forecast.retrieve import select_events
from ai_prophet_core.forecast.schemas import Prediction, Submission

logger = logging.getLogger(__name__)


def _setup_logging(verbose: bool = False):
    from dotenv import load_dotenv

    load_dotenv()
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )


@click.group(name="forecast", invoke_without_command=True)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Forecast ecosystem commands."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@cli.command(name="retrieve")
@click.option(
    "--deadline",
    required=True,
    help="ISO 8601 deadline — only include markets closing before this time.",
)
@click.option(
    "--events-per-category",
    type=int,
    default=3,
    show_default=True,
    help="Max events to select per category.",
)
@click.option(
    "--categories",
    default=None,
    help="Comma-separated category list (defaults to built-in set).",
)
@click.option(
    "--output",
    "-o",
    default="events.json",
    show_default=True,
    help="Output file path.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def retrieve(
    deadline: str,
    events_per_category: int,
    categories: str | None,
    output: str,
    verbose: bool,
) -> None:
    """Retrieve and select daily events from Kalshi across categories."""
    _setup_logging(verbose)

    deadline_dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
    if deadline_dt.tzinfo is None:
        deadline_dt = deadline_dt.replace(tzinfo=UTC)

    cat_list = [c.strip() for c in categories.split(",")] if categories else None

    client = KalshiForecastClient()
    try:
        events = select_events(
            client,
            deadline_dt,
            events_per_category=events_per_category,
            categories=cat_list,
        )
    finally:
        client.close()

    out_path = Path(output)
    out_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in events], indent=2)
    )
    click.echo(f"Selected {len(events)} events → {out_path}")


@cli.command(name="events")
@click.option(
    "--status",
    type=click.Choice(["all", "open", "closed"], case_sensitive=False),
    default="open",
    show_default=True,
    help="Filter events by status.",
)
@click.option(
    "--server-url",
    default=None,
    help="Core API URL (default: PA_SERVER_URL env var).",
)
@click.option(
    "--api-key",
    default=None,
    help="API key (default: PA_SERVER_API_KEY env var).",
)
@click.option(
    "--output",
    "-o",
    default=None,
    help="Save events to a JSON file (for use with predict).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def events(
    status: str,
    server_url: str | None,
    api_key: str | None,
    output: str | None,
    verbose: bool,
) -> None:
    """List forecast events from the server."""
    _setup_logging(verbose)

    url, key = _resolve_server(server_url, api_key)

    client = ServerAPIClient(base_url=url, api_key=key)
    try:
        event_list = client.get_forecast_events(status=status)
    finally:
        client.close()

    if not event_list:
        click.echo(f"No {status} events found.")
        return

    if output:
        out_path = Path(output)
        out_path.write_text(
            json.dumps([e.model_dump(mode="json") for e in event_list], indent=2)
        )
        click.echo(f"{len(event_list)} events → {out_path}")
    else:
        click.echo(f"{'Ticker':<40}{'Category':<18}{'Close Time':<22}Title")
        click.echo("-" * 110)
        for e in event_list:
            close = e.close_time.strftime("%Y-%m-%d %H:%M") if e.close_time else "—"
            click.echo(f"{e.market_ticker:<40}{e.category:<18}{close:<22}{e.title[:30]}")


@cli.command(name="register")
@click.option(
    "--team-name",
    required=True,
    help="Team name.",
)
@click.option(
    "--endpoint-url",
    default=None,
    help="Prediction endpoint URL (optional — registers team name only if omitted).",
)
@click.option(
    "--deactivate",
    is_flag=True,
    default=False,
    help="Deactivate the endpoint instead of activating it.",
)
@click.option(
    "--server-url",
    default=None,
    help="Core API URL (default: PA_SERVER_URL env var).",
)
@click.option(
    "--api-key",
    default=None,
    help="API key (default: PA_SERVER_API_KEY env var).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def register(
    team_name: str,
    endpoint_url: str | None,
    deactivate: bool,
    server_url: str | None,
    api_key: str | None,
    verbose: bool,
) -> None:
    """Register your team. Optionally include an endpoint URL for daily auto-forecasting."""
    _setup_logging(verbose)

    url, key = _resolve_server(server_url, api_key)

    client = ServerAPIClient(base_url=url, api_key=key)
    try:
        result = client.register_forecast_team(
            team_name=team_name,
            endpoint_url=endpoint_url,
            is_active=not deactivate,
        )
    finally:
        client.close()

    click.echo(f"Team '{result.team_name}' registered.")
    if result.endpoint_url:
        status = "active" if result.is_active else "inactive"
        click.echo(f"Endpoint: {result.endpoint_url} ({status})")

    # Save PA_TEAM_NAME to .env
    _save_team_name_to_env(team_name)


def _save_team_name_to_env(team_name: str) -> None:
    """Append or update PA_TEAM_NAME in the local .env file."""
    env_path = Path(".env")
    key_line = f"PA_TEAM_NAME={team_name}\n"

    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("PA_TEAM_NAME="):
                lines[i] = key_line
                env_path.write_text("".join(lines))
                click.echo(f"Updated PA_TEAM_NAME={team_name} in .env")
                return
        # Not found -- append
        text = env_path.read_text()
        if text and not text.endswith("\n"):
            text += "\n"
        env_path.write_text(text + key_line)
    else:
        env_path.write_text(key_line)

    click.echo(f"Saved PA_TEAM_NAME={team_name} to .env")


@cli.command(name="predict")
@click.option(
    "--events",
    required=True,
    type=click.Path(exists=True),
    help="Path to events JSON file.",
)
@click.option(
    "--agent-url",
    default=None,
    help="Agent prediction endpoint URL.",
)
@click.option(
    "--local",
    default=None,
    help="Python module path with a predict(event: dict) -> dict function. "
    "Example: ai_prophet.forecast.example_agent",
)
@click.option(
    "--output",
    "-o",
    default="submission.json",
    show_default=True,
    help="Output submission file path.",
)
@click.option(
    "--timeout",
    type=int,
    default=30,
    show_default=True,
    help="Request timeout per event (seconds).",
)
@click.option(
    "--ticker",
    "-t",
    default=None,
    multiple=True,
    help="Only predict specific market ticker(s). Can be repeated.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def predict(
    events: str,
    agent_url: str | None,
    local: str | None,
    output: str,
    timeout: int,
    ticker: tuple[str, ...],
    verbose: bool,
) -> None:
    """Collect predictions from an agent endpoint and produce a submission file."""
    _setup_logging(verbose)

    if not agent_url and not local:
        raise click.ClickException("Provide --agent-url or --local <module.path>")
    if agent_url and local:
        raise click.ClickException("Use --agent-url or --local, not both")

    # Load the local agent's predict function if --local is given
    local_predict = None
    if local:
        import importlib

        try:
            mod = importlib.import_module(local)
        except ModuleNotFoundError as e:
            raise click.ClickException(f"Could not import module '{local}': {e}") from e
        local_predict = getattr(mod, "predict", None)
        if not callable(local_predict):
            raise click.ClickException(
                f"Module '{local}' must expose a predict(event: dict) -> dict function"
            )

    events_data = json.loads(Path(events).read_text())

    if ticker:
        filter_set = set(ticker)
        events_data = [e for e in events_data if e.get("market_ticker") in filter_set]
        if not events_data:
            raise click.ClickException(
                f"No events matched ticker(s): {', '.join(ticker)}"
            )
        click.echo(f"Filtered to {len(events_data)} event(s)")

    predictions: list[Prediction] = []

    now = datetime.now(UTC)

    for event in events_data:
        market_ticker = event.get("market_ticker", "unknown")

        close_str = event.get("close_time", "")
        if close_str:
            try:
                close_time = datetime.fromisoformat(close_str.replace("Z", "+00:00"))
                if close_time <= now:
                    click.echo(f"  {market_ticker}: SKIPPED (market closed at {close_str})")
                    continue
            except (ValueError, TypeError):
                pass

        try:
            if local_predict:
                result = local_predict(event)
            else:
                resp = requests.post(agent_url, json=event, timeout=timeout)
                resp.raise_for_status()
                result = resp.json()

            p_yes = float(result["p_yes"])
            predictions.append(
                Prediction(
                    market_ticker=market_ticker,
                    p_yes=p_yes,
                    rationale=result.get("rationale"),
                )
            )
            click.echo(f"  {market_ticker}: p_yes={p_yes:.3f}")
        except Exception as e:
            logger.warning("Skipping %s: %s", market_ticker, e)
            click.echo(f"  {market_ticker}: SKIPPED ({e})")
            continue

    if not predictions:
        raise click.ClickException("No predictions collected -- nothing to submit.")

    submission = Submission(
        timestamp=datetime.now(UTC),
        predictions=predictions,
    )

    out_path = Path(output)
    out_path.write_text(submission.model_dump_json(indent=2))
    click.echo(f"\nSubmission ({len(predictions)} predictions) → {out_path}")


@cli.group(name="calibrate")
def calibrate() -> None:
    """Fit and apply post-hoc calibration corrections to forecast submissions."""


@calibrate.command(name="fit")
@click.option(
    "--submission",
    required=True,
    type=click.Path(exists=True),
    help="Path to the submission to fit calibration from.",
)
@click.option(
    "--actuals",
    required=True,
    type=click.Path(exists=True),
    help="Path to actuals JSON (resolved outcomes).",
)
@click.option(
    "--output",
    "-o",
    default="calibration.json",
    show_default=True,
    help="Output path for the fitted calibration table.",
)
@click.option(
    "--n-bins",
    type=int,
    default=10,
    show_default=True,
    help="Number of equal-width probability buckets.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def calibrate_fit(
    submission: str, actuals: str, output: str, n_bins: int, verbose: bool
) -> None:
    """Fit a binned calibration table from (submission, actuals).

    Prints the bucket-by-bucket calibration table and saves it for use
    with `calibrate apply`. Buckets where |mean_p - mean_actual| > 0.10
    are flagged as systematic bias.
    """
    _setup_logging(verbose)

    sub = load_submission(submission)
    act = load_actuals(actuals)
    table = fit_calibration(sub.predictions, act, n_bins=n_bins)
    save_calibration(table, output)

    click.echo(f"{'Bucket':<14}{'N':>5}{'mean_p':>9}{'mean_actual':>13}{'  bias':<10}")
    click.echo("-" * 55)
    for b in table:
        bias = b["mean_actual"] - b["mean_p"]
        flag = "  ← BIAS" if abs(bias) > 0.10 else ""
        click.echo(
            f"  [{b['bucket_lo']:.2f}-{b['bucket_hi']:.2f})"
            f"{b['n']:>5}{b['mean_p']:>9.3f}{b['mean_actual']:>13.3f}"
            f"{bias:>+8.3f}{flag}"
        )
    click.echo(f"\nCalibration table → {output}")


@calibrate.command(name="apply")
@click.option(
    "--submission",
    required=True,
    type=click.Path(exists=True),
    help="Path to the submission to correct.",
)
@click.option(
    "--calibration",
    required=True,
    type=click.Path(exists=True),
    help="Path to a calibration table produced by `calibrate fit`.",
)
@click.option(
    "--output",
    "-o",
    default="submission-calibrated.json",
    show_default=True,
    help="Output path for the corrected submission.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def calibrate_apply(
    submission: str, calibration: str, output: str, verbose: bool
) -> None:
    """Apply a fitted calibration table to a submission.

    Predictions falling in each bucket are replaced by that bucket's
    observed actual-yes-rate. Buckets with no data pass through
    unchanged.
    """
    _setup_logging(verbose)

    sub = load_submission(submission)
    table = load_calibration(calibration)

    new_preds = []
    for p in sub.predictions:
        new_p = apply_calibration(p.p_yes, table)
        new_preds.append(
            Prediction(
                market_ticker=p.market_ticker,
                p_yes=new_p,
                rationale=(p.rationale or "") + f" [calibrated from {p.p_yes:.3f}]",
            )
        )
    new_sub = Submission(timestamp=sub.timestamp, predictions=new_preds)
    Path(output).write_text(new_sub.model_dump_json(indent=2))
    click.echo(f"Calibrated submission ({len(new_preds)} predictions) → {output}")


@cli.command(name="evaluate")
@click.option(
    "--submission",
    required=True,
    type=click.Path(exists=True),
    help="Path to submission JSON file.",
)
@click.option(
    "--actuals",
    required=True,
    type=click.Path(exists=True),
    help="Path to actuals JSON file.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def evaluate(submission: str, actuals: str, verbose: bool) -> None:
    """Evaluate a submission against actual outcomes using Brier score."""
    _setup_logging(verbose)

    sub = load_submission(submission)
    act = load_actuals(actuals)
    result = score(sub.predictions, act)

    click.echo(f"Predictions: {result['n_predictions']}")
    click.echo(f"Matched: {result['n_matched']}")
    brier = result["brier_score"]
    click.echo(f"Brier Score: {brier if brier is not None else 'N/A (no matched predictions)'}")


def _resolve_server(server_url: str | None, api_key: str | None) -> tuple[str, str]:
    """Resolve server URL and API key from flags or env vars."""
    from ai_prophet_core import DEFAULT_API_URL

    url = server_url or os.environ.get("PA_SERVER_URL", DEFAULT_API_URL)
    key = api_key or os.environ.get("PA_SERVER_API_KEY")
    if not key:
        raise click.ClickException(
            "API key required: use --api-key or set PA_SERVER_API_KEY"
        )
    return url, key


@cli.command(name="submit")
@click.option(
    "--submission",
    required=True,
    type=click.Path(exists=True),
    help="Path to submission JSON file (from predict step).",
)
@click.option(
    "--server-url",
    default=None,
    help="Core API URL (default: PA_SERVER_URL env var).",
)
@click.option(
    "--api-key",
    default=None,
    help="API key (default: PA_SERVER_API_KEY env var).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def submit(submission: str, server_url: str | None, api_key: str | None, verbose: bool) -> None:
    """Submit predictions to the forecast server."""
    _setup_logging(verbose)

    url, key = _resolve_server(server_url, api_key)

    sub = load_submission(submission)
    predictions = [p.model_dump() for p in sub.predictions]

    client = ServerAPIClient(base_url=url, api_key=key)
    try:
        result = client.submit_forecast(predictions=predictions)
    finally:
        client.close()

    click.echo(f"Submitted {result.n_predictions} predictions for team '{result.team_name}'")
    click.echo(f"Submission ID: {result.submission_id}")
    click.echo(f"Timestamp: {result.submitted_at}")


@cli.command(name="leaderboard")
@click.option(
    "--server-url",
    default=None,
    help="Core API URL (default: PA_SERVER_URL env var).",
)
@click.option(
    "--api-key",
    default=None,
    help="API key (default: PA_SERVER_API_KEY env var).",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def leaderboard(server_url: str | None, api_key: str | None, verbose: bool) -> None:
    """View the forecast leaderboard."""
    _setup_logging(verbose)

    url, key = _resolve_server(server_url, api_key)

    client = ServerAPIClient(base_url=url, api_key=key)
    try:
        scores = client.get_forecast_leaderboard()
    finally:
        client.close()

    if not scores:
        click.echo("No scores yet.")
        return

    click.echo(f"{'Rank':<6}{'Team':<25}{'Brier Score':<14}{'Matched':<10}{'Scored At'}")
    click.echo("-" * 75)
    for i, entry in enumerate(scores, 1):
        click.echo(
            f"{i:<6}{entry.team_name:<25}{entry.brier_score:<14.6f}"
            f"{entry.n_matched:<10}{entry.scored_at.strftime('%Y-%m-%d %H:%M')}"
        )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
