"""Tests for the ``src.asgi`` Redis startup hook.

── The startup "Redis connected" line must never carry the Redis password ──

REGRESSION: the lifespan logged ``settings.redis.url`` raw at INFO on the
connect path. A Redis URL is ``redis://[user]:<password>@host:port/db``, so
every pod start wrote the live Redis password to this pod's stdout and to
anything shipping those logs, indefinitely.

The test drives the REAL ``_startup_redis`` rather than the redactor, because
the defect is at the CALL SITE: a test that only exercises
``w2v_common.observability.redis_endpoint`` passes for the whole duration of the
leak. That helper has its own unit tests in w2v_common; this one exists to fail
if anyone hands the raw url back to the logger here.

``_startup_redis`` was extracted verbatim out of ``_startup`` in the same change
precisely so this call site is drivable -- the surrounding lifespan wires ~20
narrative/script collaborators that have nothing to do with the leak.
"""

from __future__ import annotations

import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

# Wholly synthetic and shares no prefix with any real credential -- this string
# is committed, printed by pytest on failure, and ends up in CI logs, i.e.
# exactly the places this change exists to keep secrets out of.
_FAKE_PASSWORD = "not-a-real-password-xyzzy"  # pragma: allowlist secret
_FAKE_USER = "content_cache"
_FAKE_ENDPOINT = "redis.w2v:6379"
_FAKE_REDIS_URL = f"redis://{_FAKE_USER}:{_FAKE_PASSWORD}@{_FAKE_ENDPOINT}/2"


async def test_startup_redis_log_does_not_leak_the_redis_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The emitted 'Redis connected' record must not contain the Redis password."""
    from src import asgi

    monkeypatch.setattr(asgi.settings.redis, "url", _FAKE_REDIS_URL)

    # A handler attached directly to the module logger, NOT ``caplog``. This
    # service calls ``configure_logging()``, which re-points the root handlers,
    # so caplog's root handler can observe nothing and every assertion below
    # would pass vacuously.
    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.INFO)
    asgi.logger.addHandler(handler)
    # ``setLevel`` rather than assigning ``.level`` (or monkeypatching it):
    # ``Logger`` memoizes ``isEnabledFor`` in ``_cache`` and only ``setLevel``
    # invalidates that cache. A stale entry suppresses the record entirely,
    # which reads exactly like a clean redaction -- a vacuous green.
    previous_level = asgi.logger.level
    asgi.logger.setLevel(logging.INFO)

    app = FastAPI()
    fake_client = MagicMock()
    fake_client.connect = AsyncMock()

    try:
        # Patched where ``_startup_redis`` imports it (function-local import),
        # so no socket is opened; nothing else in this hook touches the network.
        with patch("src.infra.redis_client.RedisClient", return_value=fake_client):
            await asgi._startup_redis(app)
    finally:
        asgi.logger.removeHandler(handler)
        asgi.logger.setLevel(previous_level)

    # The connect path, not the ``Redis unavailable`` fallback.
    assert app.state.redis_client is fake_client

    captured = buffer.getvalue()
    # Positive control FIRST: a line that never emitted would satisfy every
    # negative assertion below for the wrong reason.
    assert "Redis connected" in captured, (
        "the 'Redis connected' line never emitted; the assertions would be vacuous"
    )
    assert _FAKE_PASSWORD not in captured, "Redis password leaked into the startup log"
    assert _FAKE_USER not in captured, "Redis username leaked into the startup log"
    # Still useful, or the next person deletes the log line instead of keeping it.
    assert _FAKE_ENDPOINT in captured
