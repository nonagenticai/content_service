"""Async Redis client for caching operations."""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Every cache op below degrades on ``(OSError, RedisError)``. Both halves are
# load-bearing and neither implies the other:
#   * ``redis.exceptions.RedisError`` derives from ``Exception``, NOT from
#     ``OSError``. Its subclasses ``redis.exceptions.ConnectionError`` and
#     ``redis.exceptions.TimeoutError`` shadow the builtin names of the same
#     spelling, so a tuple of only builtins never matches anything the redis
#     client raises — that made the original handlers dead code and turned any
#     Redis blip into a 500 instead of a cache miss.
#   * ``OSError`` still covers socket-level failures that surface from below
#     the client (DNS, ECONNRESET during a raw send) without being wrapped.
# ``RedisError`` is the base, so listing it covers ConnectionError /
# TimeoutError / BusyLoadingError / ResponseError without enumerating them.


class RedisClient:
    """Async Redis client wrapping ``redis.asyncio``."""

    def __init__(
        self,
        redis_url: str,
        *,
        max_connections: int = 10,
        socket_timeout: int = 5,
        socket_connect_timeout: int = 5,
        decode_responses: bool = True,
    ) -> None:
        self._redis_url = redis_url
        self._max_connections = max_connections
        self._socket_timeout = socket_timeout
        self._socket_connect_timeout = socket_connect_timeout
        self._decode_responses = decode_responses
        self._pool: ConnectionPool | None = None
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        """Create the connection pool and verify connectivity."""
        if self._pool is None:
            self._pool = ConnectionPool.from_url(
                self._redis_url,
                max_connections=self._max_connections,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                decode_responses=self._decode_responses,
            )
            self._client = redis.Redis(connection_pool=self._pool)
            ping_result = self._client.ping()
            if hasattr(ping_result, "__await__"):
                await ping_result
            logger.info("Redis connection successful")

    async def disconnect(self) -> None:
        """Close the Redis client and connection pool."""
        if self._client:
            await self._client.aclose()
            self._client = None
        if self._pool:
            await self._pool.aclose()
            self._pool = None

    async def __aenter__(self) -> RedisClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.disconnect()

    def _ensure_connected(self) -> redis.Redis:
        if self._client is None:
            raise RuntimeError("RedisClient not connected. Call connect() first.")
        return self._client

    @staticmethod
    def _serialize(value: Any) -> str:
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        return json.dumps(value)

    @staticmethod
    def _deserialize(value: str | bytes | None) -> Any:
        # ``redis.asyncio`` returns ``bytes`` unless the connection was created
        # with ``decode_responses=True``; both are accepted by ``json.loads``.
        if value is None:
            return None
        try:
            return json.loads(value)
        except TypeError, ValueError:
            return None

    async def get(self, key: str) -> Any | None:
        """Retrieve and deserialize a value by key."""
        client = self._ensure_connected()
        try:
            value = await client.get(key)
            return self._deserialize(value) if value else None
        except (OSError, RedisError) as e:
            logger.error("Error getting key '%s': %s", key, e)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        """Serialize and store a value, optionally with a TTL."""
        client = self._ensure_connected()
        try:
            serialized = self._serialize(value)
            if ttl is not None:
                result = await client.setex(key, ttl, serialized)
            else:
                result = await client.set(key, serialized)
            return bool(result)
        except (OSError, RedisError) as e:
            logger.error("Error setting key '%s': %s", key, e)
            return False

    async def delete(self, key: str) -> bool:
        """Delete a key from Redis."""
        client = self._ensure_connected()
        try:
            result = await client.delete(key)
            return bool(result)
        except (OSError, RedisError) as e:
            logger.error("Error deleting key '%s': %s", key, e)
            return False

    async def exists(self, key: str) -> bool:
        """Return True if *key* exists in Redis."""
        client = self._ensure_connected()
        try:
            result = await client.exists(key)
            return bool(result)
        except (OSError, RedisError) as e:
            logger.error("Error checking key '%s': %s", key, e)
            return False

    async def ping(self) -> bool:
        """Ping Redis and return True if reachable."""
        client = self._ensure_connected()
        try:
            ping_result = client.ping()
            if hasattr(ping_result, "__await__"):
                result = await ping_result
            else:
                result = ping_result
            return bool(result)
        except (OSError, RedisError) as e:
            logger.error("Redis ping failed: %s", e)
            return False
