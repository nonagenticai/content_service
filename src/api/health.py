"""Health check endpoints for content_service."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# The library exception trees below are NOT optional and must never be
# "simplified" away. None of these inherit from the builtins whose names they
# shadow::
#
#     sqlalchemy.exc.OperationalError.__mro__
#         -> DatabaseError -> DBAPIError -> StatementError -> SQLAlchemyError
#            -> HasDescriptionCode -> Exception
#     issubclass(sqlalchemy.exc.OperationalError, OSError | RuntimeError) -> False
#     issubclass(redis.exceptions.RedisError,     OSError)                -> False
#
# A ``psycopg.OperationalError`` from a Postgres/pgbouncer blip surfaces as
# ``sqlalchemy.exc.OperationalError``, so a builtins-only ``except (OSError,
# ConnectionError, TimeoutError, RuntimeError)`` let every REAL database fault
# escape these probes and FastAPI turned it into a 500. That is exactly what
# happened on 2026-08-15 during a ~45s pgbouncer blip: ``/health/ready`` logged
# "unhandled exception in GET /health/ready" and answered 500 with no body.
#
# On /health/ready (and its /readyz alias) that breaks the Kubernetes readiness
# contract: a transient DB fault must be a 503 ("not ready"), never a 500 ("the
# app is broken").
#
# Probe failure = the dependency is down, not the process. The builtins stay in
# the tuples because a socket-level error can still surface as one.
_DB_PROBE_ERRORS = (OSError, ConnectionError, TimeoutError, RuntimeError, SQLAlchemyError)
_REDIS_PROBE_ERRORS = (OSError, ConnectionError, TimeoutError, RuntimeError, RedisError)
_TTS_PROBE_ERRORS = (
    httpx.TransportError,
    httpx.HTTPStatusError,
    OSError,
    ConnectionError,
    TimeoutError,
    RuntimeError,
)


def _check_storage() -> str:
    """Verify the configured local storage path exists and is writable."""
    try:
        from src.config import get_settings

        storage_path = Path(get_settings().storage.local_path)
        if not storage_path.exists():
            return "path not found"
        if not os.access(str(storage_path), os.W_OK):
            return "not writable"
        return "healthy"
    except (OSError, PermissionError) as e:
        return f"unhealthy: {e}"


@router.get("/health", response_model=None, operation_id="health_check")
async def health_check(request: Request) -> dict[str, Any] | JSONResponse:
    """Combined health check for all components."""
    try:
        db_status = "healthy"
        engine = getattr(request.app.state, "db_engine", None)
        if engine is None:
            db_status = "unhealthy: database engine not initialized"
        else:
            try:
                async with engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            except _DB_PROBE_ERRORS as e:
                db_status = f"unhealthy: {e}"

        redis_status = "healthy"
        try:
            redis_client = getattr(request.app.state, "redis_client", None)
            if redis_client:
                await redis_client.ping()
            else:
                redis_status = "not configured"
        except _REDIS_PROBE_ERRORS as e:
            redis_status = f"unhealthy: {e}"

        storage_status = _check_storage()

        tts_status = "not configured"
        try:
            from w2v_common.discovery import service_url

            from src.config import get_settings
            from src.services.narrative.tts_client import TTSClient

            settings = get_settings()
            tts_client = TTSClient(
                base_url=service_url("tts"),
                timeout=settings.narration.tts_timeout_seconds,
            )
            result = await tts_client.health_check()
            tts_status = "healthy" if result.get("status") == "healthy" else "unavailable"
        except _TTS_PROBE_ERRORS as e:
            tts_status = f"error: {e}"

        # TTS is optional and does not affect overall health.
        overall = (
            "healthy"
            if all(s == "healthy" for s in (db_status, redis_status, storage_status))
            else "degraded"
        )

        return {
            "status": overall,
            "components": {
                "database": db_status,
                "redis": redis_status,
                "storage": storage_status,
                "tts": tts_status,
                "api": "healthy",
            },
        }
    except (*_DB_PROBE_ERRORS, *_REDIS_PROBE_ERRORS, *_TTS_PROBE_ERRORS) as e:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)},
        )


@router.get("/health/live", response_model=None, operation_id="health_live")
async def health_live() -> dict[str, str]:
    """Kubernetes liveness probe."""
    return {"status": "ok"}


@router.get("/healthz", operation_id="health.live")
async def healthz() -> dict[str, str]:
    """Modern K8s liveness probe path (alias of /health/live)."""
    return {"status": "ok"}


@router.get("/health/ready", response_model=None, operation_id="health_ready")
async def health_ready(request: Request) -> dict[str, Any] | JSONResponse:
    """Kubernetes readiness probe -- checks database connectivity.

    ``db_engine`` is set by the lifespan startup. If startup never ran (or
    failed before wiring the engine) the attribute is ABSENT, and a bare
    ``request.app.state.db_engine`` raises ``AttributeError`` -- which is not a
    member of ``_DB_PROBE_ERRORS`` and so escaped to the ASGI layer as a **500**.
    A readiness probe must answer 503 ("not ready yet"), never 500. Resolve the
    engine defensively and short-circuit BEFORE any query.
    """
    engine = getattr(request.app.state, "db_engine", None)
    if engine is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "ready": False,
                "checks": {
                    "database": {"status": "down", "error": "database engine not initialized"}
                },
            },
        )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return {"status": "healthy", "ready": True, "checks": {"database": {"status": "up"}}}
    except _DB_PROBE_ERRORS as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "ready": False,
                "checks": {"database": {"status": "down", "error": str(e)}},
            },
        )


@router.get("/readyz", response_model=None, operation_id="health.ready")
async def readyz(request: Request) -> dict[str, Any] | JSONResponse:
    """Modern K8s readiness probe path (alias of /health/ready)."""
    return await health_ready(request)
