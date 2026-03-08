from importlib.metadata import version

import ai_prophet_core
from ai_prophet_core import ServerAPIClient


def test_runtime_version_matches_distribution_metadata():
    assert ai_prophet_core.__version__ == version("ai-prophet-core")


def test_server_api_client_is_exported_from_package_root():
    assert ServerAPIClient.__name__ == "ServerAPIClient"
