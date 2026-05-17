"""Forecast CLI for the Prophet Arena hackathon.

Usage:
    prophet forecast retrieve --output events.json
    prophet forecast predict --events events.json --agent-url http://localhost:8000/predict
    prophet forecast evaluate --submission predictions.json --actuals actuals.json
    prophet forecast leaderboard --server-url http://localhost:8000
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import click
import requests
from ai_prophet_core.client import ServerAPIClient
from ai_prophet_core.forecast.dataset_retrieve import retrieve_dataset_events
from ai_prophet_core.forecast.evaluate import load_actuals, load_submission, score
from ai_prophet_core.forecast.schemas import MarketProbability, Prediction, Submission

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
    "--dataset",
    default=None,
    help="Dataset name (default: PA_FORECAST_DATASET or sample-sports).",
)
@click.option(
    "--release",
    "release_id",
    default=None,
    help="Dataset release id (default: PA_FORECAST_RELEASE or latest open release).",
)
@click.option(
    "--repo-path",
    default=None,
    help="Local ai-prophet-datasets clone. If omitted, reads the public registry.",
)
@click.option(
    "--repo-url",
    default=None,
    help="Dataset registry repo URL override.",
)
@click.option(
    "--branch",
    default=None,
    help="Dataset registry branch or commit sha (default: PA_FORECAST_DATASET_BRANCH or main).",
)
@click.option(
    "--include-resolved",
    is_flag=True,
    default=False,
    help="Include tasks that already have resolved_outcome.",
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
    dataset: str | None,
    release_id: str | None,
    repo_path: str | None,
    repo_url: str | None,
    branch: str | None,
    include_resolved: bool,
    output: str,
    verbose: bool,
) -> None:
    """Retrieve forecast events from the default dataset release."""
    _setup_logging(verbose)

    try:
        events, dataset_name, selected_release = retrieve_dataset_events(
            dataset=dataset,
            release_id=release_id,
            repo_path=repo_path,
            repo_url=repo_url,
            branch=branch,
            include_resolved=include_resolved,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    out_path = Path(output)
    out_path.write_text(
        json.dumps([e.model_dump(mode="json") for e in events], indent=2)
    )
    click.echo(
        f"Retrieved {len(events)} events from "
        f"{dataset_name}/{selected_release} → {out_path}"
    )


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
    default="predictions.json",
    show_default=True,
    help="Output predictions file path.",
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
    """Collect predictions from an agent endpoint and write a local predictions file."""
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

            if "probabilities" in result:
                probabilities = _normalize_probabilities(result["probabilities"])
                predictions.append(
                    Prediction(
                        market_ticker=market_ticker,
                        probabilities=probabilities,
                        rationale=result.get("rationale"),
                    )
                )
                summary = ", ".join(
                    f"{p.market}={p.probability:.3f}" for p in probabilities
                )
                click.echo(f"  {market_ticker}: probabilities=[{summary}]")
            else:
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
        raise click.ClickException("No predictions collected -- nothing to write.")

    submission = Submission(
        timestamp=datetime.now(UTC),
        predictions=predictions,
    )

    out_path = Path(output)
    out_path.write_text(submission.model_dump_json(indent=2, exclude_none=True))
    click.echo(f"\nPredictions ({len(predictions)} markets) → {out_path}")


def _normalize_probabilities(raw: Any) -> list[MarketProbability]:
    """Validate and normalize an agent probability-distribution response."""
    if isinstance(raw, dict):
        items = [
            {"market": market, "probability": probability}
            for market, probability in raw.items()
        ]
    elif isinstance(raw, list):
        items = raw
    else:
        raise TypeError("probabilities must be a list or object")

    values = [
        (str(item["market"]), float(item["probability"]))
        for item in items
    ]
    if any(probability > 1.0 for _market, probability in values):
        values = [(market, probability / 100) for market, probability in values]

    probabilities = [
        MarketProbability(
            market=market,
            probability=max(0.0, min(1.0, probability)),
        )
        for market, probability in values
    ]
    if not probabilities:
        raise ValueError("probabilities must contain at least one entry")

    total = sum(item.probability for item in probabilities)
    if total <= 0:
        raise ValueError("probabilities must sum to a positive value")

    return [
        MarketProbability(
            market=item.market,
            probability=item.probability / total,
        )
        for item in probabilities
    ]


@cli.command(name="evaluate")
@click.option(
    "--submission",
    required=True,
    type=click.Path(exists=True),
    help="Path to predictions JSON file.",
)
@click.option(
    "--actuals",
    required=True,
    type=click.Path(exists=True),
    help="Path to actuals JSON file.",
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def evaluate(submission: str, actuals: str, verbose: bool) -> None:
    """Evaluate a predictions file against actual outcomes using Brier score."""
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


@cli.command(name="monitor")
@click.option(
    "--agent-url",
    default=None,
    help="Agent endpoint URL. When given, GET <url>/health is checked.",
)
@click.option(
    "--submission",
    default=None,
    type=click.Path(exists=False),
    help=(
        "Path to a recent submission file (output of `prophet forecast "
        "predict -o ...`). When given, summary stats and uniform-fallback "
        "rate are reported."
    ),
)
@click.option(
    "--calibration-gcs-uri",
    default=None,
    help=(
        "gs://bucket/path/to/calibration.json. Default: CALIBRATION_GCS_URI "
        "env var. When given, the object's update timestamp is checked "
        "against --stale-hours."
    ),
)
@click.option(
    "--stale-hours",
    type=float,
    default=36.0,
    show_default=True,
    help=(
        "Calibration object age (hours) above which the check fails. The "
        "default 36h leaves room for a nightly refit job that ran ~yesterday."
    ),
)
@click.option(
    "--timeout",
    type=float,
    default=10.0,
    show_default=True,
    help="HTTP timeout (seconds) for the agent /health probe.",
)
@click.option(
    "--uniform-fallback-marker",
    default="uniform",
    show_default=True,
    help=(
        "Substring (case-insensitive) in a prediction's rationale that "
        "marks it as a uniform/no-signal fallback. Used to compute the "
        "healthy-signal rate. Set to empty to skip this check."
    ),
)
@click.option(
    "--min-healthy-rate",
    type=float,
    default=0.98,
    show_default=True,
    help=(
        "Minimum healthy-signal rate (1 - fallback_rate) on the submission "
        "below which the check fails."
    ),
)
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging.")
def monitor(
    agent_url: str | None,
    submission: str | None,
    calibration_gcs_uri: str | None,
    stale_hours: float,
    timeout: float,
    uniform_fallback_marker: str,
    min_healthy_rate: float,
    verbose: bool,
) -> None:
    """Run an eval-window health check on your forecasting agent.

    Surfaces the three operational risks that compound over a multi-day
    evaluation window:

    \b
      * Endpoint reachability (does your agent answer?).
      * Calibration table freshness (is the daily refit job still
        running, if you have one?).
      * Healthy-signal rate in recent predictions (are you serving
        real forecasts or silently falling back to a uniform prior?).

    All three checks are optional; only the ones whose inputs are
    configured will run. Exit status is 0 when all configured checks
    pass and 1 when any fail, so this command can be wired into a cron
    or launchd job to page you on regression during the eval.
    """
    _setup_logging(verbose)
    overall_ok = True
    ran_anything = False

    # 1. Endpoint health.
    if agent_url:
        ran_anything = True
        health_url = agent_url.rstrip("/")
        # Allow either the bare base URL or the /predict URL; auto-derive /health.
        if health_url.endswith("/predict"):
            health_url = health_url.rsplit("/", 1)[0] + "/health"
        elif not health_url.endswith("/health"):
            health_url = health_url + "/health"
        try:
            resp = requests.get(health_url, timeout=timeout)
            if resp.status_code == 200:
                click.echo(
                    f"[✓] endpoint: GET {health_url} → 200 "
                    f"({resp.elapsed.total_seconds():.2f}s)"
                )
            else:
                overall_ok = False
                click.echo(
                    f"[✗] endpoint: GET {health_url} → {resp.status_code}"
                )
        except requests.RequestException as exc:
            overall_ok = False
            click.echo(f"[✗] endpoint: GET {health_url} failed — {exc}")

    # 2. Calibration freshness (env-var fallback for the URI).
    cal_uri = calibration_gcs_uri or os.environ.get("CALIBRATION_GCS_URI")
    if cal_uri:
        ran_anything = True
        ok, msg = _check_calibration_freshness(cal_uri, stale_hours=stale_hours)
        marker = "[✓]" if ok else "[✗]"
        click.echo(f"{marker} calibration: {msg}")
        overall_ok &= ok

    # 3. Submission completeness.
    if submission:
        ran_anything = True
        ok, msg = _check_submission_health(
            Path(submission),
            uniform_fallback_marker=uniform_fallback_marker.lower().strip(),
            min_healthy_rate=min_healthy_rate,
        )
        marker = "[✓]" if ok else "[✗]"
        click.echo(f"{marker} submission: {msg}")
        overall_ok &= ok

    if not ran_anything:
        click.echo(
            "monitor: no checks configured. Provide at least one of "
            "--agent-url, --submission, or --calibration-gcs-uri "
            "(or set CALIBRATION_GCS_URI)."
        )
        return

    click.echo("")
    click.echo("OVERALL: " + ("✓ HEALTHY" if overall_ok else "✗ ATTENTION NEEDED"))
    if not overall_ok:
        raise SystemExit(1)


def _check_calibration_freshness(
    gcs_uri: str, *, stale_hours: float
) -> tuple[bool, str]:
    """Report (ok, message) for a calibration GCS object's age.

    Tries google-cloud-storage first; falls back to `gcloud storage ls`
    parsing so the check works for teams with `gcloud` installed but no
    `google-cloud-storage` pip package in their environment.
    """
    if not gcs_uri.startswith("gs://"):
        return False, f"invalid GCS URI: {gcs_uri}"
    rest = gcs_uri[len("gs://"):]
    bucket_name, _, object_name = rest.partition("/")
    if not bucket_name or not object_name:
        return False, f"unparseable GCS URI: {gcs_uri}"

    try:
        from google.cloud import storage  # type: ignore

        client = storage.Client()
        blob = client.bucket(bucket_name).get_blob(object_name)
        if blob is None:
            return True, (
                f"no calibration object yet at {gcs_uri} "
                f"(expected pre-eval or first refit)"
            )
        updated = blob.updated
        age_h = (datetime.now(UTC) - updated).total_seconds() / 3600.0
        ok = age_h <= stale_hours
        return ok, (
            f"object updated {age_h:.1f}h ago"
            + ("" if ok else f" (> {stale_hours}h threshold)")
        )
    except ImportError:
        return _check_calibration_via_gcloud(gcs_uri, stale_hours=stale_hours)
    except Exception as exc:  # network / auth
        return False, f"GCS check raised: {exc}"


def _check_calibration_via_gcloud(
    gcs_uri: str, *, stale_hours: float
) -> tuple[bool, str]:
    """Parse `gcloud storage ls --long <uri>` for object age."""
    import subprocess

    try:
        proc = subprocess.run(
            ["gcloud", "storage", "ls", "--long", gcs_uri],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"gcloud unavailable: {exc}"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if "matched no objects" in stderr.lower():
            return True, f"no calibration object yet at {gcs_uri}"
        return False, f"gcloud failed: {stderr[:160]}"
    lines = [ln.strip() for ln in proc.stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return False, "empty gcloud ls output"
    parts = lines[0].split()
    if len(parts) < 2:
        return False, f"unparseable: {lines[0][:120]}"
    try:
        ts = datetime.fromisoformat(parts[1].replace("Z", "+00:00"))
    except ValueError:
        return False, f"unparseable timestamp: {parts[1]}"
    age_h = (datetime.now(UTC) - ts).total_seconds() / 3600.0
    ok = age_h <= stale_hours
    return ok, (
        f"object updated {age_h:.1f}h ago"
        + ("" if ok else f" (> {stale_hours}h threshold)")
    )


def _check_submission_health(
    path: Path,
    *,
    uniform_fallback_marker: str,
    min_healthy_rate: float,
) -> tuple[bool, str]:
    """Inspect a submission file's prediction count + uniform-fallback rate."""
    if not path.exists():
        return False, f"submission file not found: {path}"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"unreadable: {exc}"
    predictions = data.get("predictions") if isinstance(data, dict) else None
    if not predictions:
        return False, "no predictions in file"
    total = len(predictions)
    if not uniform_fallback_marker:
        return True, f"{total} predictions"
    fallback = 0
    for pred in predictions:
        rationale = (pred.get("rationale") or "").lower()
        if uniform_fallback_marker in rationale:
            fallback += 1
    healthy_rate = 1.0 - (fallback / total)
    ok = healthy_rate >= min_healthy_rate
    return ok, (
        f"{total} predictions, {fallback} match '{uniform_fallback_marker}' "
        f"(healthy-signal rate {healthy_rate:.1%}, threshold {min_healthy_rate:.0%})"
    )


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
