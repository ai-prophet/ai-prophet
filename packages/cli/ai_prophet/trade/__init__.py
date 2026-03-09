"""Trading client implementation and CLI namespace."""

from .main import cli
from .runner import ExperimentRunner, compute_config_hash

__all__ = ["cli", "ExperimentRunner", "compute_config_hash"]
