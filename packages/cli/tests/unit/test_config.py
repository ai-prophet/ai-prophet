from ai_prophet.core.config import ClientConfig


def test_get_uses_bundled_defaults_without_cwd_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.local.yaml").write_text(
        "pipeline:\n  max_markets: 99\n",
        encoding="utf-8",
    )

    ClientConfig.reset()
    config = ClientConfig.get()

    assert config.pipeline.max_markets == 5
    ClientConfig.reset()


def test_load_runtime_applies_cwd_local_override(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.local.yaml").write_text(
        "pipeline:\n  max_markets: 99\n",
        encoding="utf-8",
    )

    ClientConfig.reset()
    config = ClientConfig.load_runtime()

    assert config.pipeline.max_markets == 99
    ClientConfig.reset()
