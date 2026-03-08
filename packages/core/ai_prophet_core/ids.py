"""ID generation utilities for deterministic and stable identifiers."""

import hashlib
import uuid
from typing import Any


def generate_uuid() -> str:
    """Generate a random UUID v4 string.

    Returns:
        UUID string (e.g. '550e8400-e29b-41d4-a716-446655440000')
    """
    return str(uuid.uuid4())


def generate_deterministic_uuid(seed: str) -> str:
    """Generate a deterministic UUID v5 from a seed string.

    Useful for testing and reproducible scenarios.

    Args:
        seed: Seed string to generate UUID from

    Returns:
        Deterministic UUID string

    Example:
        >>> generate_deterministic_uuid("test_run_1")
        'a6c8f0f8-...'  # Always same for same seed
    """
    namespace = uuid.UUID("00000000-0000-0000-0000-000000000000")
    return str(uuid.uuid5(namespace, seed))


def hash_stable_id(*parts: Any) -> str:
    """Generate a stable hash ID from multiple parts.

    Useful for generating composite IDs that need to be stable across runs.

    Args:
        *parts: Parts to hash together

    Returns:
        Hexadecimal hash string (first 16 chars)

    Example:
        >>> hash_stable_id("market", "2024-01-15", "snapshot")
        '3f7a2d9e1c4b8a6f'
    """
    content = "|".join(str(p) for p in parts)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def candidate_snapshot_id(as_of_ts: str, filter_hash: str) -> str:
    """Generate a stable ID for a candidate set snapshot.

    Args:
        as_of_ts: ISO timestamp of snapshot
        filter_hash: Hash of filter parameters used

    Returns:
        Stable snapshot ID
    """
    return f"snapshot_{hash_stable_id(as_of_ts, filter_hash)}"

