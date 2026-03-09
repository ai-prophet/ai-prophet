import click
import pytest

from ai_prophet.trade.main import _validate_model_credentials
from ai_prophet.trade.core.credentials import Credentials


def test_validate_model_credentials_accepts_generic_provider_env(monkeypatch):
    monkeypatch.setenv("TOGETHER_API_KEY", "test-key")

    _validate_model_credentials(
        [{"model": "together:meta-llama/llama-3-70b", "rep": 0}],
        Credentials(),
    )


def test_validate_model_credentials_rejects_missing_provider_key(monkeypatch):
    monkeypatch.delenv("TOGETHER_API_KEY", raising=False)

    with pytest.raises(click.ClickException, match=r"together \(TOGETHER_API_KEY\)"):
        _validate_model_credentials(
            [{"model": "together:meta-llama/llama-3-70b", "rep": 0}],
            Credentials(),
        )
