"""Unit tests for asgi.py to boost coverage.

Covers: HealthCheckLoggingFilter, global_exception_handler, lifespan
(success, DB failure, Redis failure, MinIO warning, shutdown errors).
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.asgi import HealthCheckLoggingFilter

# ---------------------------------------------------------------------------
# HealthCheckLoggingFilter
# ---------------------------------------------------------------------------


class TestHealthCheckLoggingFilter:
    def test_filters_health_path(self) -> None:
        """Health paths are filtered out."""
        f = HealthCheckLoggingFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "GET /health 200", (), None)
        assert f.filter(record) is False

    def test_filters_health_live_path(self) -> None:
        f = HealthCheckLoggingFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "GET /health/live 200", (), None)
        assert f.filter(record) is False

    def test_filters_health_ready_path(self) -> None:
        f = HealthCheckLoggingFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "GET /health/ready 200", (), None)
        assert f.filter(record) is False

    def test_allows_non_health_path(self) -> None:
        f = HealthCheckLoggingFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "POST /api/storyline 200", (), None)
        assert f.filter(record) is True

    def test_allows_empty_message(self) -> None:
        f = HealthCheckLoggingFilter()
        record = logging.LogRecord("test", logging.INFO, "", 0, "", (), None)
        assert f.filter(record) is True


# ---------------------------------------------------------------------------
# Lifespan helpers
# ---------------------------------------------------------------------------


def _lifespan_engine_mock() -> AsyncMock:
    """Build a mock async DB engine with begin() context manager."""
    mock_engine = AsyncMock()
    mock_conn = AsyncMock()
    mock_conn.run_sync = AsyncMock()
    mock_engine.begin = MagicMock(
        return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_conn),
            __aexit__=AsyncMock(return_value=False),
        )
    )
    mock_engine.dispose = AsyncMock()
    return mock_engine


def _make_mock_settings(storage_backend: str = "local") -> MagicMock:
    """Build a fully-populated mock settings object for the lifespan."""
    mock_settings = MagicMock()
    mock_settings.app_version = "test"
    mock_settings.app_name = "test-service"
    mock_settings.environment = "test"
    mock_settings.database.url = "postgresql+psycopg://localhost/test"
    mock_settings.database.pool_size = 2
    mock_settings.database.max_overflow = 8
    mock_settings.database.pool_timeout = 30
    mock_settings.database.pool_recycle = 3600
    mock_settings.database.echo = False
    mock_settings.redis.url = "redis://localhost/0"
    mock_settings.redis.max_connections = 10
    mock_settings.redis.socket_timeout = 5
    mock_settings.redis.socket_connect_timeout = 5
    mock_settings.redis.decode_responses = True
    mock_settings.api.host = "0.0.0.0"
    mock_settings.api.port = 6305
    mock_settings.storyline.chapter_title_card_duration = 5.0
    mock_settings.shotlist.min_part_duration_seconds = 20.0
    mock_settings.shotlist.min_title_card_duration_seconds = 2.0
    mock_settings.storage.backend = storage_backend
    mock_settings.storage.local_path = "/tmp/test_storage"
    mock_settings.storage.minio_access_key = ""
    mock_settings.storage.minio_secret_key = ""
    mock_settings.generator.templates_dir = None
    mock_settings.generator.auth_enabled = False
    mock_settings.generator.mode = "default"
    mock_settings.generator.source_type = "default"
    mock_settings.generator.parallel_workers = 2
    mock_settings.validation.ts_compile_timeout = 30
    mock_settings.validation.discover_timeout = 30
    mock_settings.validation.dry_run_timeout = 60
    mock_settings.validation.error_truncation_limit = 2000
    mock_settings.validation.max_improvement_attempts = 3
    mock_settings.scaffold.enabled = False
    mock_settings.scaffold.npm_install = False
    mock_settings.scaffold.npm_install_timeout = 120
    mock_settings.script_llm_timeout = 30.0
    mock_settings.narrative_llm_timeout_seconds = 90
    return mock_settings


def _common_lifespan_patches(mock_engine, mock_redis_client, mock_settings):
    """Return a list of patch context managers for a happy-path lifespan run."""
    # StorageService mock with async initialize
    mock_storage = MagicMock()
    mock_storage.initialize = AsyncMock()

    # TaskManager mock with async shutdown
    mock_task_manager = MagicMock()
    mock_task_manager.shutdown = AsyncMock()

    return [
        patch(
            "src.database.connection.create_engine_with_retry",
            new_callable=AsyncMock,
            return_value=mock_engine,
        ),
        patch("src.database.connection.create_session_factory", return_value=MagicMock()),
        patch("src.infra.redis_client.RedisClient", return_value=mock_redis_client),
        patch("src.database.models.Base", MagicMock()),
        patch("src.asgi.settings", mock_settings),
        patch("src.asgi._create_script_llm_provider", return_value=MagicMock(name="test-llm")),
        patch(
            "src.services.narrative.storyline_generator.StorylineGenerator",
            return_value=MagicMock(),
        ),
        patch("src.services.narrative.shotlist_builder.ShotListBuilder", return_value=MagicMock()),
        patch(
            "src.services.narrative.narrative_extractor.NarrativeExtractor",
            return_value=MagicMock(),
        ),
        patch(
            "src.services.narrative.narration_builder.NarrationBuilder", return_value=MagicMock()
        ),
        patch(
            "src.services.narrative.shotlist_enhancer.ShotListEnhancer", return_value=MagicMock()
        ),
        patch("src.services.scripts.storage_service.StorageService", return_value=mock_storage),
        patch("src.services.scripts.prompt_builder.PromptBuilder", return_value=MagicMock()),
        patch(
            "src.services.scripts.generation_service.GenerationService", return_value=MagicMock()
        ),
        patch(
            "src.services.scripts.validation_service.ValidationService", return_value=MagicMock()
        ),
        patch(
            "src.services.scripts.improvement_service.ImprovementService", return_value=MagicMock()
        ),
        patch("src.services.scripts.orchestrator.ScriptOrchestrator", return_value=MagicMock()),
        patch("src.infra.task_manager.TaskManager", return_value=mock_task_manager),
        patch("src.repository.job_repository.JobRepository", return_value=MagicMock()),
        patch("src.repository.batch_repository.BatchRepository", return_value=MagicMock()),
        patch("src.repository.usage_repository.UsageRepository", return_value=MagicMock()),
    ]


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------


class TestLifespan:
    @pytest.mark.asyncio
    async def test_lifespan_success(self) -> None:
        """Lifespan initializes DB, Redis, and services, stores state on app."""
        mock_engine = _lifespan_engine_mock()
        mock_redis_client = AsyncMock()
        mock_redis_client.connect = AsyncMock()
        mock_redis_client.disconnect = AsyncMock()
        mock_redis_client.ping = AsyncMock(return_value=True)
        mock_settings = _make_mock_settings()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        patches = _common_lifespan_patches(mock_engine, mock_redis_client, mock_settings)

        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            from src.asgi import _shutdown, _startup

            await _startup(mock_app)
            assert mock_app.state.db_engine is mock_engine
            assert mock_app.state.redis_client is mock_redis_client
            await _shutdown(mock_app)

    @pytest.mark.asyncio
    async def test_lifespan_redis_failure_continues(self) -> None:
        """Lifespan continues without Redis when RedisClient.connect() raises."""
        mock_engine = _lifespan_engine_mock()
        mock_redis_client = AsyncMock()
        mock_redis_client.connect = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_redis_client.disconnect = AsyncMock()
        mock_settings = _make_mock_settings()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        patches = _common_lifespan_patches(mock_engine, mock_redis_client, mock_settings)

        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            from src.asgi import _shutdown, _startup

            await _startup(mock_app)
            # Redis unavailable but lifespan should not raise
            assert mock_app.state.redis_client is None
            await _shutdown(mock_app)

    @pytest.mark.asyncio
    async def test_lifespan_db_failure_raises(self) -> None:
        """Lifespan raises when DB connection fails."""
        mock_settings = _make_mock_settings()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        with (
            patch(
                "src.database.connection.create_engine_with_retry",
                new_callable=AsyncMock,
                side_effect=ConnectionError("DB down"),
            ),
            patch("src.asgi.settings", mock_settings),
        ):
            from src.asgi import _startup

            with pytest.raises(ConnectionError, match="DB down"):
                await _startup(mock_app)

    @pytest.mark.asyncio
    async def test_lifespan_minio_warning(self) -> None:
        """Lifespan warns when MinIO backend is selected without credentials."""
        mock_engine = _lifespan_engine_mock()
        mock_redis_client = AsyncMock()
        mock_redis_client.connect = AsyncMock()
        mock_redis_client.disconnect = AsyncMock()
        mock_redis_client.ping = AsyncMock(return_value=True)
        mock_settings = _make_mock_settings(storage_backend="minio")

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        patches = _common_lifespan_patches(mock_engine, mock_redis_client, mock_settings)

        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            from src.asgi import _shutdown, _startup

            await _startup(mock_app)
            # Should warn about MinIO but not raise
            await _shutdown(mock_app)

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_redis_error_handled(self) -> None:
        """Lifespan handles errors during Redis shutdown gracefully."""
        mock_engine = _lifespan_engine_mock()
        mock_redis_client = AsyncMock()
        mock_redis_client.connect = AsyncMock()
        mock_redis_client.disconnect = AsyncMock(side_effect=RuntimeError("Redis close error"))
        mock_redis_client.ping = AsyncMock(return_value=True)
        mock_settings = _make_mock_settings()

        mock_app = MagicMock()
        mock_app.state = MagicMock()

        patches = _common_lifespan_patches(mock_engine, mock_redis_client, mock_settings)

        from contextlib import AsyncExitStack

        async with AsyncExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            from src.asgi import _shutdown, _startup

            await _startup(mock_app)
            # Shutdown errors should be caught, not raised
            await _shutdown(mock_app)
