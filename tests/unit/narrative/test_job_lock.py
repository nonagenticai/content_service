"""Tests for distributed job locking via Redis SET NX."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

# Inject the REAL redis-py exception, not RuntimeError: redis-py's
# ConnectionError derives from RedisError(Exception), so a handler catching only
# builtins would pass this test and still crash the job in production.
from redis.exceptions import ConnectionError as RedisConnectionError

from src.cache.job_lock import _OWNER_ID, acquire_job_lock, release_job_lock


class TestAcquireJobLock:
    """Tests for acquire_job_lock()."""

    async def test_returns_true_when_redis_is_none(self):
        """Fail-open: returns True if no Redis client available."""
        result = await acquire_job_lock(None, "job-1")
        assert result is True

    async def test_acquires_lock_successfully(self):
        """Returns True when SET NX succeeds."""
        mock_redis_inner = AsyncMock()
        mock_redis_inner.set = AsyncMock(return_value=True)

        mock_client = MagicMock()
        mock_client._ensure_connected.return_value = mock_redis_inner

        result = await acquire_job_lock(mock_client, "job-1", ttl=300)
        assert result is True
        mock_redis_inner.set.assert_called_once_with(
            "narrative:lock:job:job-1", _OWNER_ID, nx=True, ex=300
        )

    async def test_returns_false_when_locked_by_another_pod(self):
        """Returns False when SET NX fails (key already exists)."""
        mock_redis_inner = AsyncMock()
        mock_redis_inner.set = AsyncMock(return_value=None)

        mock_client = MagicMock()
        mock_client._ensure_connected.return_value = mock_redis_inner

        result = await acquire_job_lock(mock_client, "job-1")
        assert result is False

    async def test_returns_true_on_redis_error(self):
        """Fail-open: returns True if Redis raises an exception."""
        mock_client = MagicMock()
        mock_client._ensure_connected.side_effect = RedisConnectionError("connection lost")

        result = await acquire_job_lock(mock_client, "job-1")
        assert result is True


class TestReleaseJobLock:
    """Tests for release_job_lock()."""

    async def test_noop_when_redis_is_none(self):
        """Does nothing if no Redis client available."""
        await release_job_lock(None, "job-1")  # should not raise

    async def test_releases_lock_via_lua_script(self):
        """Calls eval with Lua script to release lock safely."""
        mock_redis_inner = AsyncMock()
        mock_redis_inner.eval = AsyncMock(return_value=1)

        mock_client = MagicMock()
        mock_client._ensure_connected.return_value = mock_redis_inner

        await release_job_lock(mock_client, "job-1")
        mock_redis_inner.eval.assert_called_once()
        args = mock_redis_inner.eval.call_args
        assert "narrative:lock:job:job-1" in args[0]

    async def test_silently_ignores_errors(self):
        """Does not raise on Redis errors."""
        mock_client = MagicMock()
        mock_client._ensure_connected.side_effect = RedisConnectionError("disconnected")

        await release_job_lock(mock_client, "job-1")  # should not raise


class TestContention:
    """Test lock contention scenarios."""

    async def test_second_acquire_fails_after_first_succeeds(self):
        """Simulates two pods trying to lock the same job."""
        call_count = 0

        async def mock_set(key, value, nx=False, ex=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return True  # first caller wins
            return None  # second caller loses

        mock_redis_inner = AsyncMock()
        mock_redis_inner.set = mock_set

        mock_client = MagicMock()
        mock_client._ensure_connected.return_value = mock_redis_inner

        first = await acquire_job_lock(mock_client, "job-1")
        second = await acquire_job_lock(mock_client, "job-1")

        assert first is True
        assert second is False
