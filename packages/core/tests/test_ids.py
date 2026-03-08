"""Tests for ID generation utilities."""

from ai_prophet_core.ids import (
    generate_uuid,
    generate_deterministic_uuid,
    hash_stable_id,
    candidate_snapshot_id,
)


def test_generate_uuid():
    """Test UUID generation."""
    uuid1 = generate_uuid()
    uuid2 = generate_uuid()
    
    assert len(uuid1) == 36
    assert len(uuid2) == 36
    assert uuid1 != uuid2  # Should be different


def test_generate_deterministic_uuid():
    """Test deterministic UUID generation."""
    # Same seed = same UUID
    uuid1 = generate_deterministic_uuid("test_seed")
    uuid2 = generate_deterministic_uuid("test_seed")
    assert uuid1 == uuid2
    
    # Different seed = different UUID
    uuid3 = generate_deterministic_uuid("different_seed")
    assert uuid1 != uuid3


def test_hash_stable_id():
    """Test stable hash ID generation."""
    # Same inputs = same hash
    hash1 = hash_stable_id("market", "2024-01-15", "snapshot")
    hash2 = hash_stable_id("market", "2024-01-15", "snapshot")
    assert hash1 == hash2
    assert len(hash1) == 16  # First 16 chars of SHA256
    
    # Different inputs = different hash
    hash3 = hash_stable_id("market", "2024-01-16", "snapshot")
    assert hash1 != hash3


def test_candidate_snapshot_id():
    """Test candidate snapshot ID generation."""
    snapshot_id = candidate_snapshot_id("2024-01-15T14:00:00Z", "filter_abc123")
    assert snapshot_id.startswith("snapshot_")
    
    # Same inputs = same ID
    snapshot_id2 = candidate_snapshot_id("2024-01-15T14:00:00Z", "filter_abc123")
    assert snapshot_id == snapshot_id2

