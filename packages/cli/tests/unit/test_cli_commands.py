from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from ai_prophet.main import cli
from ai_prophet.trade.core.config import ClientConfig
from ai_prophet.trade.core.credentials import Credentials


def test_root_help_lists_top_level_commands():
    runner = CliRunner()

    result = runner.invoke(cli, ["help"])

    assert result.exit_code == 0
    assert "trade" in result.output
    assert "forecast" in result.output


def test_health_command_reports_service_status(monkeypatch):
    runner = CliRunner()

    monkeypatch.setattr(
        "ai_prophet.trade.main._load_runtime_credentials",
        lambda: Credentials(server_url="http://example.test", server_api_key="server-key"),
    )

    class FakeServerAPIClient:
        def __init__(self, base_url, api_key=None):
            assert base_url == "http://example.test"
            assert api_key == "server-key"

        def health_check(self):
            return SimpleNamespace(status="ok", service="core-api", version="1.2.3")

        def close(self):
            return None

    monkeypatch.setattr("ai_prophet.trade.main.ServerAPIClient", FakeServerAPIClient)

    result = runner.invoke(cli, ["trade", "health"])

    assert result.exit_code == 0
    assert "Checking: http://example.test" in result.output
    assert "Status:  ok" in result.output
    assert "Service: core-api" in result.output
    assert "Version: 1.2.3" in result.output


def test_eval_run_passes_explicit_runtime_config_to_runner(monkeypatch):
    runner = CliRunner()
    runtime_config = ClientConfig.from_mapping(
        {
            "pipeline": {"max_markets": 9},
            "search": {"max_queries_per_market": 2, "max_results_per_query": 4},
        }
    )
    captured: dict[str, object] = {}

    monkeypatch.delenv("PA_MEMORY_DIR", raising=False)
    monkeypatch.delenv("PA_MEMORY_MAX_ROWS", raising=False)
    monkeypatch.setattr("ai_prophet.trade.main.ClientConfig.load_runtime", lambda: runtime_config)
    monkeypatch.setattr(
        "ai_prophet.trade.main._load_runtime_credentials",
        lambda: Credentials(
            server_url="http://example.test",
            server_api_key="server-key",
            openai_api_key="openai-key",
        ),
    )
    monkeypatch.setattr("ai_prophet.trade.main._get_betting_engine", lambda: None)
    monkeypatch.setattr(
        "ai_prophet.trade.main._make_pipeline_builder",
        lambda creds, client_config, verbose, api_url, server_api_key, betting_engine: captured.update(
            {
                "builder_creds": creds,
                "builder_config": client_config,
                "builder_verbose": verbose,
                "builder_api_url": api_url,
                "builder_server_api_key": server_api_key,
                "builder_betting_engine": betting_engine,
            }
        )
        or "builder",
    )

    class FakeServerAPIClient:
        def __init__(self, base_url, api_key=None):
            captured["api_base_url"] = base_url
            captured["api_key"] = api_key

        def create_or_get_experiment(self, **kwargs):
            captured["experiment_kwargs"] = kwargs
            return SimpleNamespace(created=True, status="RUNNING")

        def close(self):
            captured["api_closed"] = True

    class FakeRunner:
        def __init__(self, **kwargs):
            captured["runner_kwargs"] = kwargs

        def run(self):
            captured["runner_ran"] = True

    monkeypatch.setattr("ai_prophet.trade.main.ServerAPIClient", FakeServerAPIClient)
    monkeypatch.setattr("ai_prophet.trade.main.ExperimentRunner", FakeRunner)

    result = runner.invoke(
        cli,
        ["trade", "eval", "run", "-m", "openai:gpt-5.2", "-s", "smoke_test", "--max-ticks", "1"],
    )

    assert result.exit_code == 0
    assert captured["builder_config"] is runtime_config
    assert captured["builder_server_api_key"] == "server-key"
    assert captured["api_key"] == "server-key"
    assert captured["runner_kwargs"]["client_config"] is runtime_config
    assert captured["runner_kwargs"]["api_key"] == "server-key"
    assert captured["runner_kwargs"]["memory_dir"] == Path("~/.pa_memory").expanduser()
    assert captured["runner_kwargs"]["memory_max_rows"] == 1000
    assert captured["runner_kwargs"]["build_pipeline"] == "builder"
    assert captured["runner_kwargs"]["betting_engine"] is None
    assert captured["builder_betting_engine"] is None
    assert captured["runner_ran"] is True
