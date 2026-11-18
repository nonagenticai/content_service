"""Tests for health check endpoints with mocked database/redis/tts.

Calls the async route handlers directly (no TestClient) to avoid
event-loop conflicts. The new health.py reads state from
request.app.state, so we build a minimal mock request object.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import OperationalError as SQLAlchemyOperationalError


class _FakeAsyncContextManager:
    """Minimal async context manager that yields a mock connection."""

    def __init__(self, conn: AsyncMock | None = None) -> None:
        self._conn = conn or AsyncMock()

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        pass


def _healthy_engine_mock() -> MagicMock:
    """Create a mock engine whose .connect() returns a proper async CM."""
    mock_engine = MagicMock()
    mock_conn = AsyncMock()
    mock_engine.connect.return_value = _FakeAsyncContextManager(mock_conn)
    return mock_engine


def _make_request(
    db_engine: MagicMock | None = None,
    redis_client: MagicMock | None = None,
) -> MagicMock:
    """Build a minimal mock FastAPI Request with app.state attributes."""
    mock_state = MagicMock()
    mock_state.db_engine = db_engine
    mock_state.redis_client = redis_client
    mock_app = MagicMock()
    mock_app.state = mock_state
    mock_request = MagicMock()
    mock_request.app = mock_app
    return mock_request


def _mock_settings() -> MagicMock:
    """Build a mock Settings with narration + storage config.

    Storage path points at the test tmpdir configured in conftest so the
    storage component of the health check resolves to "healthy".
    The TTS URL is sourced from w2v_common.discovery, not from settings.
    """
    settings = MagicMock()
    settings.narration.tts_timeout_seconds = 60.0
    settings.storage.local_path = "/tmp/content_service_test"
    return settings


def _get_health_module():
    """Re-import src.api.health to get the current module."""
    import src.api.health

    importlib.reload(src.api.health)
    return src.api.health


def _patch_tts(mock_tts_instance: AsyncMock):
    """Return a context manager that patches both TTSClient and get_settings."""
    return _CombinedPatch(mock_tts_instance)


class _CombinedPatch:
    """Combined patch for TTSClient constructor and get_settings."""

    def __init__(self, mock_tts_instance: AsyncMock) -> None:
        self._mock_tts = mock_tts_instance
        self._patches: list = []

    def __enter__(self):
        p1 = patch(
            "src.services.narrative.tts_client.TTSClient",
            return_value=self._mock_tts,
        )
        p2 = patch("src.config.get_settings", return_value=_mock_settings())
        self._patches = [p1, p2]
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *args: object):
        for p in reversed(self._patches):
            p.__exit__(*args)


class _CombinedPatchSideEffect:
    """Combined patch where TTSClient itself raises on construction."""

    def __init__(self, side_effect: Exception) -> None:
        self._side_effect = side_effect
        self._patches: list = []

    def __enter__(self):
        p1 = patch(
            "src.services.narrative.tts_client.TTSClient",
            side_effect=self._side_effect,
        )
        p2 = patch("src.config.get_settings", return_value=_mock_settings())
        self._patches = [p1, p2]
        for p in self._patches:
            p.__enter__()
        return self

    def __exit__(self, *args: object):
        for p in reversed(self._patches):
            p.__exit__(*args)


class TestCheckStorage:
    """Regression tests for ``_check_storage`` — catches the exact class of
    deployment bug where STORAGE_LOCAL_PATH points at a read-only parent."""

    def test_writable_path_returns_healthy(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("STORAGE_LOCAL_PATH", str(tmp_path))
        from src.config import clear_settings_cache

        clear_settings_cache()
        mod = _get_health_module()
        assert mod._check_storage() == "healthy"

    def test_missing_path_returns_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing = tmp_path / "does-not-exist"
        monkeypatch.setenv("STORAGE_LOCAL_PATH", str(missing))
        from src.config import clear_settings_cache

        clear_settings_cache()
        mod = _get_health_module()
        assert mod._check_storage() == "path not found"

    def test_unwritable_path_returns_not_writable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        locked = tmp_path / "locked"
        locked.mkdir()
        locked.chmod(0o555)
        monkeypatch.setenv("STORAGE_LOCAL_PATH", str(locked))
        from src.config import clear_settings_cache

        clear_settings_cache()
        mod = _get_health_module()
        try:
            assert mod._check_storage() == "not writable"
        finally:
            locked.chmod(0o755)  # allow tmp_path cleanup


class TestHealthLive:
    """Liveness probe is unconditional."""

    @pytest.mark.asyncio
    async def test_returns_ok(self) -> None:
        """Liveness probe always returns status ok."""
        mod = _get_health_module()
        result = await mod.health_live()
        assert result == {"status": "ok"}


class TestHealthCheck:
    """Combined health check covering DB, Redis, and TTS components."""

    @pytest.mark.asyncio
    async def test_all_healthy(self) -> None:
        """When all components are healthy, status is 'healthy'."""
        mock_engine = _healthy_engine_mock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "healthy", "service": "tts_service"}
        )

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "healthy"
        assert result["components"]["database"] == "healthy"
        assert result["components"]["redis"] == "healthy"
        assert result["components"]["tts"] == "healthy"
        assert result["components"]["api"] == "healthy"

    @pytest.mark.asyncio
    async def test_database_unhealthy(self) -> None:
        """When database fails, overall status is 'degraded'."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = RuntimeError("DB down")
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "healthy", "service": "tts_service"}
        )
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "degraded"
        assert "unhealthy" in result["components"]["database"]

    @pytest.mark.asyncio
    async def test_redis_unhealthy(self) -> None:
        """When redis fails, overall status is 'degraded'."""
        mock_engine = _healthy_engine_mock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RuntimeError("Redis down"))

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "healthy", "service": "tts_service"}
        )
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "degraded"
        assert "unhealthy" in result["components"]["redis"]

    @pytest.mark.asyncio
    async def test_redis_not_configured(self) -> None:
        """When redis_client is None on app.state, status shows 'not configured'."""
        mock_engine = _healthy_engine_mock()

        mock_request = _make_request(db_engine=mock_engine, redis_client=None)

        mod = _get_health_module()
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "healthy", "service": "tts_service"}
        )
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["components"]["redis"] == "not configured"

    @pytest.mark.asyncio
    async def test_tts_unavailable(self) -> None:
        """When TTS health_check returns unhealthy status, tts status is 'unavailable'."""
        mock_engine = _healthy_engine_mock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "unhealthy", "service": "tts_service", "error": "down"}
        )

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "healthy"
        assert result["components"]["tts"] == "unavailable"

    @pytest.mark.asyncio
    async def test_tts_error(self) -> None:
        """When TTS raises an exception, tts status shows error."""
        mock_engine = _healthy_engine_mock()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        with _CombinedPatchSideEffect(RuntimeError("TTS init failed")):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "healthy"
        assert "error" in result["components"]["tts"]

    @pytest.mark.asyncio
    async def test_both_db_and_redis_degraded(self) -> None:
        """When both database and redis fail, status is 'degraded' with both unhealthy."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = RuntimeError("DB err")
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(side_effect=RuntimeError("Redis err"))

        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "unhealthy", "service": "tts_service", "error": "down"}
        )
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict)
        assert result["status"] == "degraded"
        assert "unhealthy" in result["components"]["database"]
        assert "unhealthy" in result["components"]["redis"]
        assert result["components"]["tts"] == "unavailable"


class TestHealthReady:
    """Readiness probe checks database connectivity."""

    @pytest.mark.asyncio
    async def test_ready_when_db_healthy(self) -> None:
        """Returns ready=True when database is reachable."""
        mock_engine = _healthy_engine_mock()
        mock_request = _make_request(db_engine=mock_engine)

        mod = _get_health_module()
        result = await mod.health_ready(mock_request)

        assert isinstance(result, dict)
        assert result["ready"] is True
        assert result["status"] == "healthy"
        assert result["checks"]["database"]["status"] == "up"

    @pytest.mark.asyncio
    async def test_not_ready_when_db_fails(self) -> None:
        """Returns 503 JSONResponse when database is unreachable."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = RuntimeError("DB unreachable")
        mock_request = _make_request(db_engine=mock_engine)

        mod = _get_health_module()
        result = await mod.health_ready(mock_request)

        from fastapi.responses import JSONResponse

        assert isinstance(result, JSONResponse)
        assert result.status_code == 503


class TestHealthReadyEngineMissing:
    """An unstarted app must read as NOT READY, never as a server error.

    ``db_engine`` is wired onto ``app.state`` by the lifespan. The handler used
    to read ``request.app.state.db_engine`` directly, so when startup never ran
    the attribute was ABSENT and the ``AttributeError`` -- which is not a member
    of ``_DB_PROBE_ERRORS`` -- escaped to the ASGI layer as a **500**.

    ``_make_request`` cannot express this case: its ``app.state`` is a
    ``MagicMock``, which fabricates any attribute asked of it, so an absent
    engine is indistinguishable from a present one. These tests use a real
    object for ``state`` so the attribute genuinely does not exist.
    """

    @staticmethod
    def _request_with_state(state: object) -> MagicMock:
        mock_app = MagicMock()
        mock_app.state = state
        mock_request = MagicMock()
        mock_request.app = mock_app
        return mock_request

    @pytest.mark.asyncio
    async def test_absent_engine_attribute_is_503_not_500(self) -> None:
        """The pre-fix code raised AttributeError here instead of answering."""
        request = self._request_with_state(SimpleNamespace())

        mod = _get_health_module()
        result = await mod.health_ready(request)

        from fastapi.responses import JSONResponse

        assert isinstance(result, JSONResponse), "an absent db_engine must be answered, not raised"
        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_engine_set_to_none_is_503(self) -> None:
        """A lifespan that cleared the engine is equally not-ready."""
        request = self._request_with_state(SimpleNamespace(db_engine=None))

        mod = _get_health_module()
        result = await mod.health_ready(request)

        from fastapi.responses import JSONResponse

        assert isinstance(result, JSONResponse)
        assert result.status_code == 503

    @pytest.mark.asyncio
    async def test_combined_health_degrades_instead_of_raising(self) -> None:
        """/health must report the database unhealthy, not blow up."""
        request = self._request_with_state(SimpleNamespace(redis_client=None))

        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(
            return_value={"status": "healthy", "service": "tts_service"}
        )

        mod = _get_health_module()
        with _patch_tts(mock_tts):
            result = await mod.health_check(request)

        assert isinstance(result, dict)
        assert "unhealthy" in result["components"]["database"]


def _sqlalchemy_operational_error() -> SQLAlchemyOperationalError:
    """The exact exception a Postgres/pgbouncer blip raises through SQLAlchemy.

    ``psycopg.OperationalError`` is wrapped by SQLAlchemy into
    ``sqlalchemy.exc.OperationalError``, whose MRO stops at ``SQLAlchemyError ->
    Exception``. It is NOT an ``OSError`` and NOT a ``RuntimeError``.
    """
    return SQLAlchemyOperationalError(
        "SELECT 1",
        {},
        Exception("server closed the connection unexpectedly"),
    )


class TestDbProbeErrorTuple:
    """Regression tests for the 2026-08-15 pgbouncer blip.

    The probes caught only builtins, so a ``sqlalchemy.exc.OperationalError``
    escaped to the ASGI layer and the readiness endpoint answered **500 with no
    body** instead of a clean 503.
    """

    def test_sqlalchemy_operational_error_is_not_a_builtin(self) -> None:
        """The premise of the bug: the narrow tuple could never have fired."""
        assert not issubclass(SQLAlchemyOperationalError, OSError)
        assert not issubclass(SQLAlchemyOperationalError, RuntimeError)
        assert not issubclass(SQLAlchemyOperationalError, ConnectionError)
        assert not issubclass(SQLAlchemyOperationalError, TimeoutError)

    @pytest.mark.asyncio
    async def test_ready_returns_503_not_500_on_sqlalchemy_operational_error(self) -> None:
        """Readiness must degrade to 503, never raise into the ASGI layer."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = _sqlalchemy_operational_error()
        mock_request = _make_request(db_engine=mock_engine)

        mod = _get_health_module()
        result = await mod.health_ready(mock_request)

        from fastapi.responses import JSONResponse

        assert isinstance(result, JSONResponse), (
            "the exception escaped the handler -- ASGI would turn this into a 500"
        )
        assert result.status_code == 503
        assert result.status_code != 500

    @pytest.mark.asyncio
    async def test_ready_503_body_names_the_failing_check(self) -> None:
        """The 503 payload must say WHICH dependency is down."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = _sqlalchemy_operational_error()
        mock_request = _make_request(db_engine=mock_engine)

        mod = _get_health_module()
        result = await mod.health_ready(mock_request)

        body = json.loads(bytes(result.body))
        assert body["ready"] is False
        assert body["status"] == "unhealthy"
        assert body["checks"]["database"]["status"] == "down"
        assert "server closed the connection unexpectedly" in body["checks"]["database"]["error"]

    @pytest.mark.asyncio
    async def test_ready_still_returns_200_on_the_happy_path(self) -> None:
        """The widened tuple must not change the healthy response."""
        mock_request = _make_request(db_engine=_healthy_engine_mock())

        mod = _get_health_module()
        result = await mod.health_ready(mock_request)

        assert isinstance(result, dict), "happy path must stay a plain 200 dict"
        assert result["ready"] is True
        assert result["checks"]["database"]["status"] == "up"

    @pytest.mark.asyncio
    async def test_health_aggregate_attributes_a_sqlalchemy_fault_to_the_db(self) -> None:
        """The /health per-component DB probe had the identical narrow tuple."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = _sqlalchemy_operational_error()
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_request = _make_request(db_engine=mock_engine, redis_client=mock_redis)

        mod = _get_health_module()
        mock_tts = AsyncMock()
        mock_tts.health_check = AsyncMock(return_value={"status": "healthy"})
        with _patch_tts(mock_tts):
            result = await mod.health_check(mock_request)

        assert isinstance(result, dict), "the exception escaped -- ASGI would 500"
        assert result["status"] == "degraded"
        assert "unhealthy" in result["components"]["database"]

    @pytest.mark.asyncio
    async def test_health_aggregate_returns_503_not_500_on_escaping_sqlalchemy_error(
        self,
    ) -> None:
        """The /health OUTER handler had the same too-narrow tuple.

        Anything raising a SQLAlchemy error outside the per-component blocks
        (here: the storage check) must land on the 503 fallback, not the ASGI
        500 path.
        """
        mock_request = _make_request(db_engine=_healthy_engine_mock())

        mod = _get_health_module()
        with patch.object(mod, "_check_storage", side_effect=_sqlalchemy_operational_error()):
            result = await mod.health_check(mock_request)

        from fastapi.responses import JSONResponse

        assert isinstance(result, JSONResponse), (
            "the exception escaped the aggregate handler -- ASGI would 500"
        )
        assert result.status_code == 503
        assert result.status_code != 500
        body = json.loads(bytes(result.body))
        assert body["status"] == "unhealthy"
        assert "server closed the connection unexpectedly" in body["error"]
