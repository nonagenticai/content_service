"""Edge case tests for cache keys, templates, and TTS timeout."""

from __future__ import annotations

import datetime
import uuid

from src.cache.cache_keys import _default_serializer, _stable_hash, job_status_ttl


class TestCacheKeyDeterminism:
    """Test that cache key hashing is deterministic."""

    def test_same_input_same_hash(self):
        data = {"a": 1, "b": [2, 3], "c": "hello"}
        h1 = _stable_hash(data)
        h2 = _stable_hash(data)
        assert h1 == h2

    def test_different_input_different_hash(self):
        h1 = _stable_hash({"a": 1})
        h2 = _stable_hash({"a": 2})
        assert h1 != h2

    def test_datetime_deterministic(self):
        dt = datetime.datetime(2024, 1, 15, 12, 30, 0, tzinfo=datetime.UTC)
        data = {"ts": dt}
        h1 = _stable_hash(data)
        h2 = _stable_hash(data)
        assert h1 == h2

    def test_uuid_deterministic(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        data = {"id": uid}
        h1 = _stable_hash(data)
        h2 = _stable_hash(data)
        assert h1 == h2

    def test_key_order_independent(self):
        h1 = _stable_hash({"b": 2, "a": 1})
        h2 = _stable_hash({"a": 1, "b": 2})
        assert h1 == h2


class TestDefaultSerializer:
    """Test the explicit serializer for non-JSON types."""

    def test_datetime_uses_isoformat(self):
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0)
        result = _default_serializer(dt)
        assert result == dt.isoformat()

    def test_uuid_uses_hex(self):
        uid = uuid.UUID("12345678-1234-5678-1234-567812345678")
        result = _default_serializer(uid)
        assert result == uid.hex

    def test_other_types_use_repr(self):
        result = _default_serializer(set([1, 2]))
        assert "1" in result and "2" in result


class TestJobStatusTtl:
    """Test dynamic job status TTL."""

    def test_processing_ttl(self):
        assert job_status_ttl("processing") == 30

    def test_completed_ttl(self):
        assert job_status_ttl("completed") == 300

    def test_failed_ttl(self):
        assert job_status_ttl("failed") == 300

    def test_unknown_status_ttl(self):
        assert job_status_ttl("pending") == 10
        assert job_status_ttl("unknown") == 10
