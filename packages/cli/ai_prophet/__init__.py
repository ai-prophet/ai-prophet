"""CLI package for Prophet Arena benchmark runs.

The supported public interface for this package is the ``ai-prophet`` CLI.
``ExperimentRunner`` remains importable for advanced embedding, but non-CLI
usage is expected to provide explicit pipeline wiring.
"""

from ._version import __version__
from .runner import ExperimentRunner

__all__ = ["ExperimentRunner", "__version__"]
