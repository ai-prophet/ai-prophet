from importlib.metadata import version

import ai_prophet
from ai_prophet import ExperimentRunner


def test_runtime_version_matches_distribution_metadata():
    assert ai_prophet.__version__ == version("ai-prophet")


def test_experiment_runner_is_exported_from_package_root():
    assert ExperimentRunner.__name__ == "ExperimentRunner"
