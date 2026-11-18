"""Tests for infra.redis_client.RedisClient."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.infra.redis_client import RedisClient


class TestRedisClientInit:
    def test_initial_state(self):
        rc = RedisClient("redis://localhost:6379")
        assert rc._pool is None
        assert rc._client is None


class TestEnsureConnected:
    def test_raises_when_not_connected(self):
        rc = RedisClient("redis://localhost:6379")
        with pytest.raises(RuntimeError, match="not connected"):
            rc._ensure_connected()


class TestSerializeDeserialize:
    def test_serialize_dict(self):
        result = RedisClient._serialize({"key": "val"})
        assert '"key"' in result

    def test_serialize_pydantic(self):
        mock_model = MagicMock()
        mock_model.model_dump.return_value = {"a": 1}
        result = RedisClient._serialize(mock_model)
        assert '"a"' in result

    def test_deserialize_none(self):
        assert RedisClient._deserialize(None) is None

    def test_deserialize_valid_json(self):
        assert RedisClient._deserialize('{"a": 1}') == {"a": 1}

    def test_deserialize_invalid_json(self):
        assert RedisClient._deserialize("not json{") is None


class TestRedisClientOperations:
    @pytest.fixture
    def connected_client(self):
        rc = RedisClient("redis://localhost:6379")
        rc._client = AsyncMock()
        rc._pool = MagicMock()
        return rc

    async def test_get_success(self, connected_client):
        connected_client._client.get = AsyncMock(return_value='{"key": "val"}')
        result = await connected_client.get("test-key")
        assert result == {"key": "val"}

    async def test_get_none_value(self, connected_client):
        connected_client._client.get = AsyncMock(return_value=None)
        result = await connected_client.get("missing-key")
        assert result is None

    async def test_get_error(self, connected_client):
        connected_client._client.get = AsyncMock(side_effect=ConnectionError("conn error"))
        result = await connected_client.get("key")
        assert result is None

    async def test_set_without_ttl(self, connected_client):
        connected_client._client.set = AsyncMock(return_value=True)
        result = await connected_client.set("key", {"data": 1})
        assert result is True

    async def test_set_with_ttl(self, connected_client):
        connected_client._client.setex = AsyncMock(return_value=True)
        result = await connected_client.set("key", {"data": 1}, ttl=300)
        assert result is True
        connected_client._client.setex.assert_awaited_once()

    async def test_set_error(self, connected_client):
        connected_client._client.set = AsyncMock(side_effect=ConnectionError("err"))
        result = await connected_client.set("key", "val")
        assert result is False

    async def test_delete_success(self, connected_client):
        connected_client._client.delete = AsyncMock(return_value=1)
        result = await connected_client.delete("key")
        assert result is True

    async def test_delete_not_found(self, connected_client):
        connected_client._client.delete = AsyncMock(return_value=0)
        result = await connected_client.delete("key")
        assert result is False

    async def test_delete_error(self, connected_client):
        connected_client._client.delete = AsyncMock(side_effect=ConnectionError("err"))
        result = await connected_client.delete("key")
        assert result is False

    async def test_exists_true(self, connected_client):
        connected_client._client.exists = AsyncMock(return_value=1)
        result = await connected_client.exists("key")
        assert result is True

    async def test_exists_false(self, connected_client):
        connected_client._client.exists = AsyncMock(return_value=0)
        result = await connected_client.exists("key")
        assert result is False

    async def test_exists_error(self, connected_client):
        connected_client._client.exists = AsyncMock(side_effect=ConnectionError("err"))
        result = await connected_client.exists("key")
        assert result is False

    async def test_ping_success(self, connected_client):
        connected_client._client.ping = AsyncMock(return_value=True)
        result = await connected_client.ping()
        assert result is True

    async def test_ping_non_awaitable(self, connected_client):
        connected_client._client.ping = MagicMock(return_value=True)
        result = await connected_client.ping()
        assert result is True

    async def test_ping_error(self, connected_client):
        connected_client._client.ping = AsyncMock(side_effect=ConnectionError("err"))
        result = await connected_client.ping()
        assert result is False


class TestRedisClientConnect:
    async def test_connect(self):
        with (
            patch("src.infra.redis_client.ConnectionPool") as mock_pool_cls,
            patch("src.infra.redis_client.redis.Redis") as mock_redis_cls,
        ):
            mock_pool = MagicMock()
            mock_pool_cls.from_url.return_value = mock_pool
            mock_client = MagicMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_redis_cls.return_value = mock_client

            rc = RedisClient("redis://localhost:6379")
            await rc.connect()

            assert rc._pool is mock_pool
            assert rc._client is mock_client


class TestRedisClientDisconnect:
    async def test_disconnect(self):
        rc = RedisClient("redis://localhost:6379")
        rc._client = AsyncMock()
        rc._pool = AsyncMock()

        await rc.disconnect()
        assert rc._client is None
        assert rc._pool is None

    async def test_disconnect_when_not_connected(self):
        rc = RedisClient("redis://localhost:6379")
        await rc.disconnect()
        # Should not raise


class TestRedisClientContextManager:
    async def test_aenter_aexit(self):
        with (
            patch("src.infra.redis_client.ConnectionPool") as mock_pool_cls,
            patch("src.infra.redis_client.redis.Redis") as mock_redis_cls,
        ):
            mock_pool = AsyncMock()
            mock_pool_cls.from_url.return_value = mock_pool
            mock_client = AsyncMock()
            mock_client.ping = AsyncMock(return_value=True)
            mock_redis_cls.return_value = mock_client

            rc = RedisClient("redis://localhost:6379")
            async with rc as client:
                assert client is rc
            # After exit, should be disconnected
