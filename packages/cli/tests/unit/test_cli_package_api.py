from importlib.metadata import version

import ai_prophet


def test_runtime_version_matches_distribution_metadata():
    assert ai_prophet.__version__ == version("ai-prophet")


def test_root_package_only_exports_version():
    assert ai_prophet.__all__ == ["__version__"]
    assert not hasattr(ai_prophet, "ExperimentRunner")
