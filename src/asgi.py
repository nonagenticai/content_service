"""ASGI application for content_service.

Unified FastAPI REST API merging narrative_service and script_generator_service.
Built on the shared ``w2v_common.asgi.create_app`` factory: it wires the canonical
middleware stack (SecurityHeaders → Auth → RequestId), the license-token
BearerAuth/TenantIdHeader OpenAPI customization, and the fleet error-envelope
handlers. Auth is gated by ``settings.auth.enabled`` via the factory's
``auth_enabled`` fallback.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from redis.exceptions import RedisError
from w2v_common.asgi import create_app
from w2v_common.observability import redis_endpoint

from src.config import get_settings
from src.logging_config import configure_logging

load_dotenv()

settings = get_settings()
configure_logging(json_logs=settings.logging.json_logs, level=settings.logging.level)
logger = logging.getLogger(__name__)


# ============================================================================
# Health Check Logging Filter
# ============================================================================


class HealthCheckLoggingFilter(logging.Filter):
    """Suppress noisy health-check requests from uvicorn access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for health-check paths to suppress their log lines."""
        message = record.getMessage()
        return not any(
            path in message
            for path in ["/health", "/health/ready", "/health/live", "/readyz", "/healthz"]
        )


uvicorn_access_logger = logging.getLogger("uvicorn.access")
uvicorn_access_logger.addFilter(HealthCheckLoggingFilter())


# ============================================================================
# LLM Provider Factory (for script generation)
# ============================================================================

# WM worker for script generation + improvement. One worker per distinct LLM
# purpose; system prompts are passed per-call via LLMRequest, so generation
# and improvement reuse the same worker without bleeding state (each call
# also defaults to disable_state_storage=True in WMClient).
_SCRIPT_LLM_WORKER = "content-script-llm"

_SCRIPT_LLM_DEFAULT_SYSTEM_PROMPT = (
    "You are a senior Playwright test automation engineer. You write and fix "
    "clean, well-structured TypeScript Playwright test scripts for website "
    "demo recordings."
)


def _create_script_llm_provider() -> Any:
    """Build the WM-backed LLM provider used by script generation + improvement."""
    from w2v_common.clients import WMClient

    from src.llm.wm_provider import WMLLMProvider

    # One shared WMClient for the script-domain provider — its underlying
    # httpx.AsyncClient is connection-pooled and the identity event hook
    # reads request-scoped ContextVars, so concurrent end-user requests
    # are correctly attributed.
    wm_client = WMClient(timeout=settings.script_llm_timeout)
    return WMLLMProvider(
        worker_name=_SCRIPT_LLM_WORKER,
        system_prompt=_SCRIPT_LLM_DEFAULT_SYSTEM_PROMPT,
        wm_client=wm_client,
        provider_name="wm-script-llm",
    )


# ============================================================================
# Lifespan hooks (passed to create_app as on_startup / on_shutdown)
# ============================================================================


async def _drain_narrative_background_tasks(*, timeout: float) -> None:
    """Drain the narrative routes' module-global fire-and-forget task set.

    These ``asyncio.Task`` objects (storyline / narration generation) are kept
    alive only by ``src.api.narrative_routes._background_tasks`` and were never
    awaited on shutdown, so a rollout mid-job used to wedge them. Give them a
    grace window to finish, then cancel and reap any stragglers.
    """
    from src.api.narrative_routes import _background_tasks

    pending = {t for t in _background_tasks if not t.done()}
    if not pending:
        return
    logger.info("Draining %d narrative background task(s)", len(pending))
    _done, still_running = await asyncio.wait(pending, timeout=timeout)
    if still_running:
        logger.warning(
            "Cancelling %d narrative task(s) that did not finish in time",
            len(still_running),
        )
        for task in still_running:
            task.cancel()
        await asyncio.gather(*still_running, return_exceptions=True)


async def _startup_redis(app: FastAPI) -> None:
    """Connect the result/job-status cache. Failures are logged but non-fatal.

    Extracted from ``_startup`` so the connect path -- and in particular the
    log line below -- is drivable from a test without standing up the whole
    lifespan. ``app.state.redis_client`` is left ``None`` when Redis is
    unavailable; every consumer degrades to a no-op on that.
    """
    from src.infra.redis_client import RedisClient

    try:
        redis_client = RedisClient(
            settings.redis.url,
            max_connections=settings.redis.max_connections,
            socket_timeout=settings.redis.socket_timeout,
            socket_connect_timeout=settings.redis.socket_connect_timeout,
            decode_responses=settings.redis.decode_responses,
        )
        await redis_client.connect()
        app.state.redis_client = redis_client
        # NEVER log ``settings.redis.url`` -- a Redis URL is
        # redis://[user]:<password>@host:port/db, so the raw value writes the
        # live password to stdout on every pod start. ``redis_endpoint``
        # rebuilds host:port from urlsplit components that structurally cannot
        # carry userinfo, and fails closed with ``<unparseable>``.
        logger.info("Redis connected: %s", redis_endpoint(settings.redis.url))
    except (ImportError, OSError, RuntimeError, ValueError, RedisError) as e:
        logger.warning("Redis unavailable: %s -- continuing without caching", e)
        app.state.redis_client = None


async def _startup(app: FastAPI) -> None:
    """Init DB, RLS re-bind, reconciler, Redis, and all narrative/script services."""
    logger.info("=" * 70)
    logger.info("Starting Content Service v%s", settings.app_version)
    logger.info("=" * 70)

    # ---- Database ----
    from src.database.connection import create_engine_with_retry, create_session_factory

    engine = await create_engine_with_retry(
        settings.database.url,
        pool_size=settings.database.pool_size,
        max_overflow=settings.database.max_overflow,
        pool_timeout=settings.database.pool_timeout,
        pool_recycle=settings.database.pool_recycle,
        echo=settings.database.echo,
    )
    app.state.db_engine = engine

    # Schema is owned by alembic -- the gitops Deployment runs
    # ``alembic upgrade head`` in an initContainer before this app
    # starts (see gitops-production
    # clusters/strato/services/w2v/deployments.yaml). Do NOT call
    # ``Base.metadata.create_all`` here: it bypasses migration history
    # and can drift the live schema away from what alembic expects.
    logger.info("Database connected (schema managed by alembic)")

    session_factory = create_session_factory(engine)

    # Phase D RLS: re-bind app.current_tenant on every transaction from the
    # identity ContextVar (defense in depth) so a future multi-commit reuse of
    # a single background session keeps the tenant GUC under enforce. Inert
    # unless TENANT_RLS_MODE=enforce. See src/database/rls_events.py.
    from src.database.rls_events import install_rls_tenant_rebind

    install_rls_tenant_rebind()

    # ---- Stale-job reconciler (crash backstop) ----
    # Periodic sweep that fails out rows wedged at status='processing' by a
    # worker crash/OOM (the in-band httpx hardening only covers transient
    # errors). Uses the global env-driven get_db_session (same connection the
    # narrative routes use) and a SECURITY DEFINER function so it can reconcile
    # across tenants under RLS enforce. See src/services/reconciler.py.
    from src.services.reconciler import StaleJobReconciler

    if settings.reconciler_enabled:
        reconciler = StaleJobReconciler(
            interval_seconds=settings.reconciler_interval_seconds,
            stale_after_seconds=settings.reconciler_stale_after_seconds,
            pending_stale_after_seconds=settings.reconciler_pending_stale_after_seconds,
        )
        reconciler.start()
        app.state.reconciler = reconciler
        logger.info(
            "Stale-job reconciler enabled (every %ds, processing stale after %ds, "
            "pending stale after %ds)",
            settings.reconciler_interval_seconds,
            settings.reconciler_stale_after_seconds,
            settings.reconciler_pending_stale_after_seconds,
        )
    else:
        app.state.reconciler = None
        logger.info("Stale-job reconciler disabled")

    # ---- Redis ----
    await _startup_redis(app)

    # ---- Narrative-domain services ----
    from src.services.narrative.narration_builder import NarrationBuilder
    from src.services.narrative.narrative_extractor import NarrativeExtractor
    from src.services.narrative.shotlist_builder import ShotListBuilder
    from src.services.narrative.shotlist_enhancer import ShotListEnhancer
    from src.services.narrative.storyline_generator import StorylineGenerator

    app.state.storyline_generator = StorylineGenerator(settings)
    app.state.shotlist_builder = ShotListBuilder(
        chapter_title_card_duration=settings.storyline.chapter_title_card_duration,
        min_part_duration=settings.shotlist.min_part_duration_seconds,
        min_title_card_duration=settings.shotlist.min_title_card_duration_seconds,
    )
    app.state.narrative_extractor = NarrativeExtractor()
    app.state.narration_builder = NarrationBuilder(settings)
    app.state.shotlist_enhancer = ShotListEnhancer(settings)
    logger.info(
        "Narrative services initialized: StorylineGenerator, ShotListBuilder, "
        "NarrativeExtractor, NarrationBuilder, ShotListEnhancer"
    )

    # ---- Script-domain LLM provider ----
    script_llm = _create_script_llm_provider()
    logger.info("Script LLM provider ready: %s", script_llm.name)

    # ---- Script-domain repositories ----
    from src.repository.batch_repository import BatchRepository
    from src.repository.job_repository import JobRepository
    from src.repository.usage_repository import UsageRepository

    job_repo = JobRepository(session_factory)
    batch_repo = BatchRepository(session_factory)
    usage_repo = UsageRepository(session_factory)

    # ---- Script-domain services ----
    from w2v_common.clients import ArtifactClient

    from src.infra.task_manager import TaskManager
    from src.services.scripts.generation_service import GenerationService
    from src.services.scripts.improvement_service import ImprovementService
    from src.services.scripts.orchestrator import ScriptOrchestrator
    from src.services.scripts.prompt_builder import PromptBuilder
    from src.services.scripts.scaffold_service import ScaffoldService
    from src.services.scripts.storage_service import StorageService
    from src.services.scripts.validation_service import ValidationService

    artifact_client = ArtifactClient()
    app.state.artifact_client = artifact_client
    logger.info("ArtifactClient initialized: base_url=%s", artifact_client.base_url)

    storage = StorageService(local_path=settings.storage.local_path)
    await storage.initialize()
    app.state.storage = storage

    prompt_builder = PromptBuilder(
        templates_dir=Path(settings.generator.templates_dir)
        if settings.generator.templates_dir
        else None,
        auth_enabled=settings.generator.auth_enabled,
        mode=settings.generator.mode,
        source_type=settings.generator.source_type,
    )

    generation_service = GenerationService(script_llm, prompt_builder)
    validation_service = ValidationService(
        ts_compile_timeout=settings.validation.ts_compile_timeout,
        discover_timeout=settings.validation.discover_timeout,
        dry_run_timeout=settings.validation.dry_run_timeout,
        error_truncation_limit=settings.validation.error_truncation_limit,
    )
    improvement_service = ImprovementService(
        script_llm,
        max_attempts=settings.validation.max_improvement_attempts,
    )

    scaffold_service = (
        ScaffoldService(
            npm_install_enabled=settings.scaffold.npm_install,
            npm_install_timeout=settings.scaffold.npm_install_timeout,
        )
        if settings.scaffold.enabled
        else None
    )

    # Cross-replica cooperative cancel flags reuse the bespoke Redis client
    # (app.state.redis_client is None when Redis is unavailable -> CancelFlags
    # degrades to a no-op and the single-replica in-process cancel still works).
    from src.infra.cancel_flags import CancelFlags, job_row_cancel_fallback

    # The DB row is the authority when Redis cannot answer: the DELETE route
    # writes status='cancelled' durably BEFORE it sets the flag, so the row is
    # never behind it. Without this, a cross-replica cancel is invisible during
    # a Redis outage and the job runs to completion after the user cancelled.
    cancel_flags = CancelFlags(
        app.state.redis_client,
        db_fallback=job_row_cancel_fallback(job_repo),
    )

    orchestrator = ScriptOrchestrator(
        job_repo=job_repo,
        batch_repo=batch_repo,
        usage_repo=usage_repo,
        generation_service=generation_service,
        validation_service=validation_service,
        improvement_service=improvement_service,
        scaffold_service=scaffold_service,
        storage=storage,
        artifact_client=artifact_client,
        cancel_flags=cancel_flags,
    )
    app.state.orchestrator = orchestrator

    task_manager = TaskManager(max_concurrent=settings.generator.parallel_workers)
    app.state.task_manager = task_manager
    logger.info(
        "Script services initialized: ScriptOrchestrator, GenerationService, "
        "ValidationService, ImprovementService, ScaffoldService, StorageService"
    )

    # Warn about unconfigured MinIO storage
    if settings.storage.backend == "minio" and (
        not settings.storage.minio_access_key or not settings.storage.minio_secret_key
    ):
        logger.warning(
            "STORAGE_BACKEND=minio but MinIO credentials are empty. "
            "Set STORAGE_MINIO_ACCESS_KEY and STORAGE_MINIO_SECRET_KEY "
            "or switch to STORAGE_BACKEND=local."
        )

    logger.info("=" * 70)
    logger.info("Content Service started successfully")
    logger.info(
        "Environment: %s | API: %s:%d", settings.environment, settings.api.host, settings.api.port
    )
    logger.info("=" * 70)


async def _shutdown(app: FastAPI) -> None:
    """Graceful teardown: stop reconciler, drain background work, close clients + DB."""
    # Order matters: stop scheduling new work, drain in-flight background work,
    # close HTTP clients, then dispose DB engines last (so draining tasks can
    # still flush their final DB writes).
    logger.info("Shutting down Content Service")

    # 1. Stop the reconciler loop (no new sweeps).
    reconciler = getattr(app.state, "reconciler", None)
    if reconciler is not None:
        try:
            await reconciler.stop()
        except (OSError, RuntimeError) as e:
            logger.error("Error stopping reconciler: %s", e)

    # 2. Drain the narrative-domain fire-and-forget tasks (see helper).
    await _drain_narrative_background_tasks(timeout=30.0)

    # 3. Drain the script-domain TaskManager.
    task_manager = getattr(app.state, "task_manager", None)
    if task_manager is not None:
        await task_manager.shutdown(timeout=30.0)

    # 4. Close HTTP clients.
    artifact_client = getattr(app.state, "artifact_client", None)
    if artifact_client is not None:
        try:
            await artifact_client.aclose()
        except (OSError, RuntimeError) as e:
            logger.error("Error closing ArtifactClient: %s", e)

    redis_client = getattr(app.state, "redis_client", None)
    if redis_client:
        try:
            await redis_client.disconnect()
        except (ImportError, OSError, RuntimeError, ValueError) as e:
            logger.error("Error closing Redis: %s", e)

    # 5. Dispose DB engines LAST. Two distinct engines are in play: the
    #    lifespan-owned app.state.db_engine, and w2v_common's global env-driven
    #    engine/session-maker used by get_db_session (narrative routes +
    #    reconciler). Both must be torn down.
    engine = getattr(app.state, "db_engine", None)
    if engine is not None:
        await engine.dispose()
    from src.database.connection import close_engine
    from src.database.session import close_session_maker

    try:
        await close_session_maker()
        # close_session_maker only drops the session-maker reference; the global
        # env-driven engine's connection pool is disposed by close_engine.
        await close_engine()
    except (OSError, RuntimeError) as e:
        logger.error("Error disposing global DB engine/session maker: %s", e)

    logger.info("Content Service shutdown complete")


# ============================================================================
# Application
# ============================================================================

# Mount routers
from src.api.health import router as health_router
from src.api.narrative_routes import router as narrative_router
from src.api.scripts_routes import router as scripts_router

app = create_app(
    settings,
    routers=[health_router, narrative_router, scripts_router],
    title=settings.app_name,
    description=(
        "Unified content generation microservice: storyline, narration, "
        "shot list, and Playwright scripts"
    ),
    version=settings.app_version,
    on_startup=[_startup],
    on_shutdown=[_shutdown],
    redirect_slashes=False,
    swagger_ui_parameters={"persistAuthorization": True},
    # Fallback unique-id generator. Every route still sets `operation_id=`
    # explicitly per OPERATION_ID_PLAN.md; this exists so a future route
    # that forgets the kwarg degrades to `<tag>_<func>` instead of FastAPI's
    # default `<func>_<path>_<method>` (garbage in generated clients).
    generate_unique_id_function=lambda route: (
        f"{route.tags[0] if route.tags else 'default'}_{route.name}"
    ),
    openapi_tags=[
        {"name": "health", "description": "Liveness, readiness, dependency probes."},
        {
            "name": "narrative",
            "description": (
                "Narrative domain: storyline generation/planning, shot lists, "
                "narration scripts, and findings-to-storyline conversion."
            ),
        },
        {
            "name": "scripts",
            "description": (
                "Playwright script domain: generation, batch + campaign-pack jobs, "
                "validation, scaffolding, and job/batch status."
            ),
        },
    ],
)


@app.get(
    "/",
    operation_id="service_root",
    tags=["health"],
    summary="Service banner + endpoint index",
)
async def root() -> dict[str, Any]:
    """Return service info and available endpoint listing."""
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "description": "Unified content generation microservice: storyline, narration, shot list, and Playwright scripts",
        "endpoints": {
            "health": "/health",
            "docs": "/docs",
            "storyline": "/api/storyline",
            "storyline_plan": "/api/storyline/plan",
            "shotlist": "/api/shotlist",
            "shotlist_enhance": "/api/shotlist/enhance",
            "narrative_extract": "/api/narrative/extract",
            "narration": "/api/narration",
            "generate": "/api/scripts/generate",
            "generate_batch": "/api/scripts/generate/batch",
            "generate_campaign_pack": "/api/scripts/generate/campaign-pack",
            "jobs": "/api/scripts/jobs/{job_id}",
            "batches": "/api/scripts/batches/{batch_id}",
        },
        "status": "running",
        "environment": settings.environment,
    }


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point for running the ASGI application."""
    import uvicorn

    uvicorn.run(
        "src.asgi:app",
        host=settings.api.host,
        port=settings.api.port,
        log_level=settings.logging.level.lower(),
        reload=settings.api.reload,
    )


if __name__ == "__main__":
    main()
