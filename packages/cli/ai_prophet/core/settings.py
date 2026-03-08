"""Backward-compatible credentials module.

Use ``ai_prophet.core.credentials`` as the canonical import path.
"""

from .credentials import DEFAULT_API_URL, Credentials, load_dotenv_file

__all__ = ["Credentials", "DEFAULT_API_URL", "load_dotenv_file"]

