"""Runtime version helpers for the installed distribution."""

from importlib.metadata import PackageNotFoundError, version


def _resolve_version() -> str:
    try:
        return version("ai-prophet-core")
    except PackageNotFoundError:
        return "0+local"


__version__ = _resolve_version()
