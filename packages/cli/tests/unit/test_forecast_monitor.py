"""Tests for `prophet forecast monitor`.

Covers the three checks the command runs (endpoint health, calibration
GCS freshness, submission healthy-signal rate), plus the exit-code
contract.

External dependencies are stubbed at the module level: `requests.get`
for the endpoint probe, `google.cloud.storage` for the calibration
check. The CLI's own argument parsing is exercised via Click's
CliRunner — same pattern as the other forecast-CLI tests in this
package.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_prophet.main import cli
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Don't let an ambient CALIBRATION_GCS_URI in the test runner's env
    cause incidental calibration-check failures. Each test that needs
    the env var sets it explicitly via monkeypatch.setenv.

    Also no-op `dotenv.load_dotenv` so `_setup_logging` doesn't repopulate
    env vars from a parent project's .env after we've cleared them."""
    monkeypatch.delenv("CALIBRATION_GCS_URI", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: False)


# ---------------------------------------------------------------------------
# Endpoint check
# ---------------------------------------------------------------------------


def test_monitor_endpoint_ok_returns_zero(monkeypatch):
    """A 200 on /health → check passes, overall exit 0."""

    class FakeResp:
        status_code = 200
        elapsed = timedelta(seconds=0.12)

    captured: dict = {}

    def fake_get(url, timeout):
        captured["url"] = url
        captured["timeout"] = timeout
        return FakeResp()

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.get", fake_get
    )

    result = CliRunner().invoke(
        cli,
        ["forecast", "monitor", "--agent-url", "http://localhost:8000"],
    )
    assert result.exit_code == 0
    assert "[✓] endpoint" in result.output
    assert "HEALTHY" in result.output
    assert captured["url"] == "http://localhost:8000/health"


def test_monitor_endpoint_strips_predict_suffix(monkeypatch):
    """Passing a /predict URL auto-derives /health on the same host."""

    class FakeResp:
        status_code = 200
        elapsed = timedelta(seconds=0.05)

    captured: dict = {}

    def fake_get(url, timeout):
        captured["url"] = url
        return FakeResp()

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.get", fake_get
    )

    CliRunner().invoke(
        cli,
        ["forecast", "monitor", "--agent-url", "https://example.com/predict"],
    )
    assert captured["url"] == "https://example.com/health"


def test_monitor_endpoint_non_200_fails(monkeypatch):
    class FakeResp:
        status_code = 503
        elapsed = timedelta(seconds=0.10)

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.get", lambda *a, **kw: FakeResp()
    )

    result = CliRunner().invoke(
        cli, ["forecast", "monitor", "--agent-url", "http://x.example.com"]
    )
    assert result.exit_code == 1
    assert "503" in result.output
    assert "ATTENTION NEEDED" in result.output


def test_monitor_endpoint_network_error_fails(monkeypatch):
    import requests

    def fake_get(*_a, **_kw):
        raise requests.RequestException("timeout")

    monkeypatch.setattr("ai_prophet.forecast.main.requests.get", fake_get)

    result = CliRunner().invoke(
        cli, ["forecast", "monitor", "--agent-url", "http://x.example.com"]
    )
    assert result.exit_code == 1
    assert "failed" in result.output


# ---------------------------------------------------------------------------
# Submission file check
# ---------------------------------------------------------------------------


def _submission_with_rationales(
    rationales: list[str], extra_field: dict | None = None
) -> dict:
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "predictions": [
            {
                "market_ticker": f"TKR-{i}",
                "p_yes": 0.6,
                "rationale": r,
                **(extra_field or {}),
            }
            for i, r in enumerate(rationales)
        ],
    }


def test_monitor_submission_clean_passes(tmp_path: Path):
    """Submission with no uniform-fallback rationales → healthy."""
    sub_path = tmp_path / "submission.json"
    sub_path.write_text(
        json.dumps(_submission_with_rationales([
            "market price 0.62 shrunk to 0.61",
            "LLM (grounded) said 0.7",
            "tail-anchor returned market mid 0.95",
        ]))
    )

    result = CliRunner().invoke(
        cli, ["forecast", "monitor", "--submission", str(sub_path)]
    )
    assert result.exit_code == 0
    assert "[✓] submission" in result.output


def test_monitor_submission_dirty_fails(tmp_path: Path):
    """Submission with too many fallback rationales → fails (default 98%)."""
    sub_path = tmp_path / "submission.json"
    # 10 predictions, 5 "uniform" → healthy-signal rate 50% < 98%.
    sub_path.write_text(
        json.dumps(_submission_with_rationales([
            "LLM unavailable; uniform prior",
            "LLM unavailable; uniform prior",
            "LLM unavailable; uniform prior",
            "LLM unavailable; uniform prior",
            "LLM unavailable; uniform prior",
            "real signal 0.7",
            "real signal 0.3",
            "real signal 0.5",
            "real signal 0.9",
            "real signal 0.1",
        ]))
    )

    result = CliRunner().invoke(
        cli, ["forecast", "monitor", "--submission", str(sub_path)]
    )
    assert result.exit_code == 1
    assert "[✗] submission" in result.output
    assert "50.0%" in result.output or "0.5" in result.output


def test_monitor_submission_missing_marker_disables_check(tmp_path: Path):
    """Empty --uniform-fallback-marker → only reports count, no failure."""
    sub_path = tmp_path / "submission.json"
    sub_path.write_text(
        json.dumps(_submission_with_rationales([
            "LLM unavailable; uniform prior",
            "LLM unavailable; uniform prior",
        ]))
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--submission", str(sub_path),
            "--uniform-fallback-marker", "",
        ],
    )
    assert result.exit_code == 0
    assert "2 predictions" in result.output


def test_monitor_submission_threshold_tunable(tmp_path: Path):
    """--min-healthy-rate lets the check pass at a more permissive bar."""
    sub_path = tmp_path / "submission.json"
    # 5 predictions, 1 uniform → 80% healthy. Default 98% fails; 75% passes.
    sub_path.write_text(
        json.dumps(_submission_with_rationales([
            "LLM unavailable; uniform prior",
            "real",
            "real",
            "real",
            "real",
        ]))
    )

    permissive = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--submission", str(sub_path),
            "--min-healthy-rate", "0.75",
        ],
    )
    assert permissive.exit_code == 0
    assert "[✓] submission" in permissive.output

    strict = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--submission", str(sub_path),
            "--min-healthy-rate", "0.95",
        ],
    )
    assert strict.exit_code == 1


def test_monitor_submission_missing_file_fails(tmp_path: Path):
    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--submission", str(tmp_path / "does-not-exist.json"),
        ],
    )
    assert result.exit_code == 1
    assert "not found" in result.output


# ---------------------------------------------------------------------------
# Calibration freshness (gcloud-fallback path; the google-cloud-storage path
# is exercised only when the package is present in the env)
# ---------------------------------------------------------------------------


def _gcloud_ls_stdout(updated_iso: str) -> str:
    return f"  1234  {updated_iso}  gs://bucket/calibration.json\n"


def test_monitor_calibration_fresh_passes(monkeypatch):
    """Calibration object updated 2h ago → ok."""
    fresh = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")

    import ai_prophet.forecast.main as main_mod

    # Force the gcloud fallback path by pretending google.cloud.storage is missing.
    monkeypatch.setattr(
        main_mod,
        "_check_calibration_freshness",
        lambda uri, *, stale_hours: main_mod._check_calibration_via_gcloud(
            uri, stale_hours=stale_hours
        ),
    )

    def fake_run(_args, **_kw):
        return SimpleNamespace(returncode=0, stdout=_gcloud_ls_stdout(fresh), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--calibration-gcs-uri", "gs://bucket/calibration.json",
        ],
    )
    assert result.exit_code == 0
    assert "[✓] calibration" in result.output


def test_monitor_calibration_stale_fails(monkeypatch):
    """Calibration object > stale-hours old → check fails."""
    stale = (datetime.now(UTC) - timedelta(hours=48)).isoformat().replace("+00:00", "Z")

    import ai_prophet.forecast.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "_check_calibration_freshness",
        lambda uri, *, stale_hours: main_mod._check_calibration_via_gcloud(
            uri, stale_hours=stale_hours
        ),
    )

    def fake_run(_args, **_kw):
        return SimpleNamespace(returncode=0, stdout=_gcloud_ls_stdout(stale), stderr="")

    monkeypatch.setattr("subprocess.run", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--calibration-gcs-uri", "gs://bucket/calibration.json",
            "--stale-hours", "36",
        ],
    )
    assert result.exit_code == 1
    assert "[✗] calibration" in result.output


def test_monitor_calibration_object_missing_is_ok(monkeypatch):
    """No calibration object yet (pre-eval or pre-first-refit) → ok, not failure."""
    import ai_prophet.forecast.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "_check_calibration_freshness",
        lambda uri, *, stale_hours: main_mod._check_calibration_via_gcloud(
            uri, stale_hours=stale_hours
        ),
    )

    def fake_run(_args, **_kw):
        return SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="ERROR: (gcloud.storage.ls) One or more URLs matched no objects.",
        )

    monkeypatch.setattr("subprocess.run", fake_run)

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--calibration-gcs-uri", "gs://bucket/calibration.json",
        ],
    )
    assert result.exit_code == 0
    assert "[✓] calibration" in result.output


def test_monitor_calibration_env_var_default(monkeypatch):
    """CALIBRATION_GCS_URI env var supplies the default when --flag is absent."""
    monkeypatch.setenv(
        "CALIBRATION_GCS_URI", "gs://from-env/calibration.json"
    )
    fresh = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")

    captured = {}
    import ai_prophet.forecast.main as main_mod

    def fake_check(uri, *, stale_hours):
        captured["uri"] = uri
        return True, "from env, fresh"

    monkeypatch.setattr(main_mod, "_check_calibration_freshness", fake_check)

    result = CliRunner().invoke(cli, ["forecast", "monitor"])
    assert captured.get("uri") == "gs://from-env/calibration.json"
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# No-checks-configured edge case
# ---------------------------------------------------------------------------


def test_monitor_no_checks_configured_explains(monkeypatch):
    """Without any input flags AND without CALIBRATION_GCS_URI, command
    prints guidance and exits 0."""
    monkeypatch.delenv("CALIBRATION_GCS_URI", raising=False)
    result = CliRunner().invoke(cli, ["forecast", "monitor"])
    assert result.exit_code == 0
    assert "no checks configured" in result.output


# ---------------------------------------------------------------------------
# Multiple checks at once
# ---------------------------------------------------------------------------


def test_monitor_runs_all_configured_checks(monkeypatch, tmp_path: Path):
    """All three checks pass → exit 0, all three [✓] lines printed."""

    class FakeResp:
        status_code = 200
        elapsed = timedelta(seconds=0.05)

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.get", lambda *a, **kw: FakeResp()
    )

    import ai_prophet.forecast.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "_check_calibration_freshness",
        lambda uri, *, stale_hours: (True, "fresh 1h"),
    )

    sub_path = tmp_path / "submission.json"
    sub_path.write_text(
        json.dumps(_submission_with_rationales(["real signal"] * 5))
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--agent-url", "https://example.com",
            "--calibration-gcs-uri", "gs://bucket/calibration.json",
            "--submission", str(sub_path),
        ],
    )
    assert result.exit_code == 0
    assert "[✓] endpoint" in result.output
    assert "[✓] calibration" in result.output
    assert "[✓] submission" in result.output


def test_monitor_any_failure_fails_overall(monkeypatch, tmp_path: Path):
    """Endpoint ok, submission ok, but calibration stale → exit 1."""

    class FakeResp:
        status_code = 200
        elapsed = timedelta(seconds=0.05)

    monkeypatch.setattr(
        "ai_prophet.forecast.main.requests.get", lambda *a, **kw: FakeResp()
    )

    import ai_prophet.forecast.main as main_mod

    monkeypatch.setattr(
        main_mod,
        "_check_calibration_freshness",
        lambda uri, *, stale_hours: (False, "stale 48h"),
    )

    sub_path = tmp_path / "submission.json"
    sub_path.write_text(
        json.dumps(_submission_with_rationales(["real signal"] * 5))
    )

    result = CliRunner().invoke(
        cli,
        [
            "forecast", "monitor",
            "--agent-url", "https://example.com",
            "--calibration-gcs-uri", "gs://bucket/calibration.json",
            "--submission", str(sub_path),
        ],
    )
    assert result.exit_code == 1
    assert "[✗] calibration" in result.output
    assert "ATTENTION NEEDED" in result.output
