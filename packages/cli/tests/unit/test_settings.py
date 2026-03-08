"""Tests for Credentials."""

import os

from ai_prophet.core.credentials import Credentials, load_dotenv_file


def test_default_credentials():
    """Test default credentials initialization."""
    creds = Credentials()

    assert creds.server_url  # has a default
    assert creds.anthropic_api_key is None
    assert creds.openai_api_key is None
    assert creds.gemini_api_key is None
    assert creds.xai_api_key is None
    assert creds.brave_api_key is None
    assert creds.verbose is False


def test_from_env(monkeypatch):
    """Test loading credentials from environment variables."""
    monkeypatch.setenv("PA_SERVER_URL", "http://prod.example.com:8000")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant_key_123")
    monkeypatch.setenv("OPENAI_API_KEY", "oai_key_456")
    monkeypatch.setenv("GEMINI_API_KEY", "gem_key_789")
    monkeypatch.setenv("XAI_API_KEY", "xai_key_abc")
    monkeypatch.setenv("BRAVE_API_KEY", "brave_key_def")
    monkeypatch.setenv("PA_VERBOSE", "true")

    creds = Credentials.from_env()

    assert creds.server_url == "http://prod.example.com:8000"
    assert creds.anthropic_api_key == "ant_key_123"
    assert creds.openai_api_key == "oai_key_456"
    assert creds.gemini_api_key == "gem_key_789"
    assert creds.xai_api_key == "xai_key_abc"
    assert creds.brave_api_key == "brave_key_def"
    assert creds.verbose is True


def test_from_env_does_not_implicitly_load_dotenv(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=dotenv_key\n", encoding="utf-8")

    creds = Credentials.from_env()

    assert creds.openai_api_key is None


def test_load_dotenv_file_populates_process_environment(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=dotenv_key\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    load_dotenv_file(str(env_path))

    assert Credentials.from_env().openai_api_key == "dotenv_key"


def test_from_env_google_alias(monkeypatch):
    """Test that GOOGLE_API_KEY works as alias for GEMINI_API_KEY."""
    # Clear both so we isolate the alias behavior.
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google_key_999")

    creds = Credentials(
        gemini_api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"),
    )

    assert creds.gemini_api_key == "google_key_999"


def test_get_api_key():
    """Test get_api_key returns correct key per provider."""
    creds = Credentials(
        anthropic_api_key="ant",
        openai_api_key="oai",
        gemini_api_key="gem",
        xai_api_key="xai",
    )

    assert creds.get_api_key("anthropic") == "ant"
    assert creds.get_api_key("openai") == "oai"
    assert creds.get_api_key("gemini") == "gem"
    assert creds.get_api_key("google") == "gem"  # alias
    assert creds.get_api_key("xai") == "xai"
    assert creds.get_api_key("grok") == "xai"  # alias
    assert creds.get_api_key("unknown") is None


def test_get_api_key_unknown_provider_from_env(monkeypatch):
    """Test generic OpenAI-compatible providers resolve through env vars."""
    monkeypatch.setenv("TOGETHER_API_KEY", "together")

    creds = Credentials()

    assert creds.get_api_key("together") == "together"
    assert creds.has_api_key("together") is True


def test_has_any_llm_key():
    """Test has_any_llm_key check."""
    assert not Credentials().has_any_llm_key()
    assert Credentials(anthropic_api_key="k").has_any_llm_key()
    assert Credentials(openai_api_key="k").has_any_llm_key()
    assert Credentials(gemini_api_key="k").has_any_llm_key()
    assert Credentials(xai_api_key="k").has_any_llm_key()


def test_repr_masks_secrets():
    """Test that repr masks API keys."""
    creds = Credentials(
        anthropic_api_key="secret_key_123",
        brave_api_key="another_secret",
    )
    r = repr(creds)

    assert "secret_key_123" not in r
    assert "another_secret" not in r
    assert "***" in r


def test_verbose_flag_values(monkeypatch):
    """Test various truthy/falsy values for PA_VERBOSE."""
    for truthy in ("true", "True", "1", "yes"):
        monkeypatch.setenv("PA_VERBOSE", truthy)
        assert Credentials.from_env().verbose is True

    for falsy in ("false", "0", "no", ""):
        monkeypatch.setenv("PA_VERBOSE", falsy)
        assert Credentials.from_env().verbose is False


