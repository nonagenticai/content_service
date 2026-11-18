"""Narration endpoints: generation (async), retrieval, and per-part narration
for a shot list (async)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid7

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.narrative_schemas import (
    DegradationReport,
    NarrationForShotListRequest,
    NarrationJobResponse,
    NarrationRequest,
    NarrationResultResponse,
    NarrationScriptResponse,
)
from src.cache.cache_keys import (
    NARRATION_CACHE_TTL,
    job_status_key,
    job_status_ttl,
)
from src.database.models import NarrationJob
from src.database.session import get_db, get_db_session
from src.logging_config import job_id_var
from src.services.narrative.degradation import narration_report

from . import _shared
from ._shared import (
    _get_license_token,
    _get_redis,
    _get_tenant_id,
    _narration_cache_key_for,
    spawn_background_task,
)

if TYPE_CHECKING:
    from src.services.narrative.narration_builder import NarrationBuilder

logger = logging.getLogger(__name__)

router = APIRouter(tags=["narrative"])


def _has_degraded_parts(result: dict) -> bool:
    """True when any narration part is deterministic boilerplate, not LLM output.

    Degraded narration must NOT be written to the hour-long result cache. The
    marker exists because a WM timeout silently substitutes placeholder text;
    caching that multiplies one transient failure across every subsequent
    identical request for the next ``NARRATION_CACHE_TTL`` seconds, and the
    job-status key (short TTL) is enough for the poll to work. The job is still
    persisted and still reports ``completed`` — the per-part ``degraded`` flag
    is what tells the caller the content is placeholder.
    """
    return bool(narration_report(result)["degraded"])


@router.post(
    "/narration",
    operation_id="start_narration",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def start_narration(
    body: NarrationRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> NarrationJobResponse:
    """Start narration generation (async).

    Args:
        body: Narration generation parameters including storyline and mode.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        NarrationJobResponse with job_id and initial status.
    """
    logger.info(
        "Received narration request: mode=%s, synthesize_audio=%s",
        body.mode,
        body.synthesize_audio,
    )
    builder = request.app.state.narration_builder
    redis = _get_redis(request)
    tenant_id = _get_tenant_id(request)
    job_id = str(uuid7())

    # Check narration cache (tenant-namespaced — never serve another tenant's body)
    cached = None
    try:
        if redis and _shared._cache_enabled():
            cache_key = _narration_cache_key_for(body, tenant_id)
            cached = await redis.get(cache_key)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis narration cache check failed: %s", exc)
    if cached:
        logger.info("Narration cache hit for job %s", job_id)
        job = NarrationJob(
            job_id=job_id,
            tenant_id=tenant_id,
            status="completed",
            narration=cached,
        )
        db.add(job)
        await db.flush()
        # COMMIT here, exactly like the "processing" branch below. ``get_db``
        # commits in its teardown, and FastAPI runs a yield-dependency's teardown
        # AFTER the response has been sent — so relying on it would hand the
        # client a "completed" job_id whose row no other connection can see yet,
        # and the caller's immediate GET would 404.
        await db.commit()
        return NarrationJobResponse(job_id=job_id, status="completed")

    # Create processing job in DB
    job = NarrationJob(
        job_id=job_id,
        tenant_id=tenant_id,
        status="processing",
    )
    db.add(job)
    await db.flush()
    await db.commit()

    spawn_background_task(
        _process_narration(
            builder,
            job_id,
            body,
            request.app,
            tenant_id,
            license_token=_get_license_token(request),
        )
    )
    logger.info("Narration job %s accepted for processing", job_id)
    return NarrationJobResponse(job_id=job_id, status="processing")


async def _process_narration(
    builder: NarrationBuilder,
    job_id: str,
    body: NarrationRequest,
    app: FastAPI,
    tenant_id: str,
    license_token: str | None = None,
) -> None:
    """Background task: generate narration scripts via LLM and persist results."""
    job_id_var.set(job_id)
    redis = getattr(app.state, "redis_client", None)
    # NO distributed job lock here — deliberately. ``job_id`` is minted by
    # ``uuid7()`` inside the POST handler that is the SOLE caller of this task
    # (see the module note in ``src/cache/job_lock.py``), so a job-id-keyed
    # lock could never be contended and ``acquire_job_lock`` always returned
    # True. The "locked by another pod" branch it guarded was unreachable and
    # advertised a duplicate-processing guard that did not exist. Re-instate it
    # HERE the moment a caller-supplied job_id, a retry, or a re-dispatching
    # reconciler makes the SAME job_id reachable twice.
    t0 = time.monotonic()
    try:
        logger.info("Narration job %s: starting LLM generation", job_id)
        result = await builder.generate(
            storyline=body.storyline,
            analysis=body.analysis,
            user_context=body.user_context,
            message=body.message,
            synthesize_audio=body.synthesize_audio,
            mode=body.mode,
            code_quality_facts=body.code_quality_facts,
            llm_config=body.llm_config,
            license_token=license_token,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Narration job %s: LLM generation completed in %.2fs",
            job_id,
            elapsed,
        )

        # Update DB (tenant-filtered defence-in-depth on top of RLS)
        async with get_db_session() as db:
            stmt = select(NarrationJob).where(
                NarrationJob.job_id == job_id,
                NarrationJob.tenant_id == tenant_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                row.status = "completed"
                row.narration = result

        # Write to Redis cache (fail-open)
        degraded = _has_degraded_parts(result)
        if degraded:
            logger.warning(
                "Narration job %s produced degraded (boilerplate) parts — "
                "skipping the %ds result cache so a transient WM failure is not "
                "replayed to every identical request",
                job_id,
                NARRATION_CACHE_TTL,
            )
        try:
            if redis and _shared._cache_enabled():
                if not degraded:
                    cache_key = _narration_cache_key_for(body, tenant_id)
                    await redis.set(cache_key, result, ttl=NARRATION_CACHE_TTL)
                await redis.set(
                    job_status_key(tenant_id, job_id),
                    {"status": "completed", "narration": result},
                    ttl=job_status_ttl("completed"),
                )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            logger.debug("Redis narration cache write failed: %s", exc)

    except Exception as exc:  # noqa: BLE001 — any failure must mark the job failed,
        # not leave it wedged in ``processing``. CancelledError is BaseException so
        # cooperative cancellation still propagates uncaught.
        logger.error("Narration job %s failed after %.2fs: %s", job_id, time.monotonic() - t0, exc)
        try:
            async with get_db_session() as db:
                stmt = select(NarrationJob).where(
                    NarrationJob.job_id == job_id,
                    NarrationJob.tenant_id == tenant_id,
                )
                row = (await db.execute(stmt)).scalar_one_or_none()
                if row:
                    row.status = "failed"
                    row.error = str(exc)
        except Exception:  # best-effort failed-marking
            logger.exception("Failed to mark narration job %s as failed", job_id)


@router.get(
    "/narration/{job_id}",
    operation_id="get_narration_job",
    responses={
        404: {"description": "Narration job not found"},
    },
)
async def get_narration(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> NarrationResultResponse:
    """Get narration job status and results.

    Args:
        job_id: UUID of the narration generation job.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        NarrationResultResponse with current status and results.

    Raises:
        HTTPException: 404 if job_id is not found.
    """
    logger.info("Polling narration job %s", job_id)
    redis = _get_redis(request)
    tenant_id = _get_tenant_id(request)

    # Check Redis cache first (fail-open) — tenant-namespaced key
    try:
        if redis:
            cached = await redis.get(job_status_key(tenant_id, job_id))
            if cached and isinstance(cached, dict):
                narration = None
                token_usage = None
                narr_data = cached.get("narration")
                if isinstance(narr_data, dict):
                    token_usage = narr_data.pop("token_usage", None)
                    narration = NarrationScriptResponse(**narr_data)
                return NarrationResultResponse(
                    job_id=job_id,
                    status=cached["status"],
                    narration=narration,
                    error=cached.get("error"),
                    token_usage=token_usage,
                    # Same aggregate on the cached path as on the DB path — a
                    # signal only one of two branches emits is not a signal.
                    degradation=DegradationReport(**narration_report(narr_data)),
                )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis narration cache read failed: %s", exc)

    # Query DB (tenant-filtered defence-in-depth on top of RLS)
    stmt = select(NarrationJob).where(
        NarrationJob.job_id == job_id,
        NarrationJob.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Narration job not found")

    narration = None
    token_usage = None
    if row.narration:
        narr_data = row.narration.copy()
        token_usage = narr_data.pop("token_usage", None)
        narration = NarrationScriptResponse(**narr_data)

    result = NarrationResultResponse(
        job_id=job_id,
        status=row.status,
        narration=narration,
        error=row.error,
        token_usage=token_usage,
        # Boilerplate narration is persisted as ``completed``. This is where the
        # caller learns that without iterating ``row.narration["parts"]``.
        degradation=DegradationReport(**narration_report(row.narration)),
    )

    # Write back to Redis (fail-open) — tenant-namespaced key
    try:
        if redis:
            await redis.set(
                job_status_key(tenant_id, job_id),
                {
                    "status": row.status,
                    "narration": row.narration,
                    "error": row.error,
                },
                ttl=job_status_ttl(row.status),
            )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis narration cache writeback failed: %s", exc)

    return result


@router.post(
    "/narration/shotlist",
    operation_id="start_narration_for_shotlist",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def start_narration_shotlist(
    body: NarrationForShotListRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> NarrationJobResponse:
    """Start per-part narration generation for a shot list (async).

    Args:
        body: Shot list and storyline for per-part narration alignment.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        NarrationJobResponse with job_id and initial status.
    """
    logger.info(
        "Received narration/shotlist request: mode=%s, parts=%d",
        body.mode,
        len(body.shot_list.get("parts", [])),
    )
    builder = request.app.state.narration_builder
    tenant_id = _get_tenant_id(request)
    job_id = str(uuid7())

    # Create processing job in DB
    job = NarrationJob(
        job_id=job_id,
        tenant_id=tenant_id,
        status="processing",
    )
    db.add(job)
    await db.flush()
    await db.commit()

    spawn_background_task(
        _process_narration_shotlist(
            builder,
            job_id,
            body,
            request.app,
            tenant_id,
            license_token=_get_license_token(request),
        )
    )
    logger.info("Narration shotlist job %s accepted for processing", job_id)
    return NarrationJobResponse(job_id=job_id, status="processing")


async def _process_narration_shotlist(
    builder: NarrationBuilder,
    job_id: str,
    body: NarrationForShotListRequest,
    app: FastAPI,
    tenant_id: str,
    license_token: str | None = None,
) -> None:
    """Background task: generate per-part narration for a shot list and persist."""
    job_id_var.set(job_id)
    redis = getattr(app.state, "redis_client", None)
    # NO distributed job lock here — deliberately. ``job_id`` is minted by
    # ``uuid7()`` inside the POST handler that is the SOLE caller of this task
    # (see the module note in ``src/cache/job_lock.py``), so a job-id-keyed
    # lock could never be contended and ``acquire_job_lock`` always returned
    # True. The "locked by another pod" branch it guarded was unreachable and
    # advertised a duplicate-processing guard that did not exist. Re-instate it
    # HERE the moment a caller-supplied job_id, a retry, or a re-dispatching
    # reconciler makes the SAME job_id reachable twice.
    t0 = time.monotonic()
    try:
        logger.info("Narration shotlist job %s: starting generation", job_id)
        result = await builder.generate_for_shot_list(
            shot_list=body.shot_list,
            storyline=body.storyline,
            user_context=body.user_context,
            message=body.message,
            synthesize_audio=body.synthesize_audio,
            mode=body.mode,
            code_quality_facts=body.code_quality_facts,
            llm_config=body.llm_config,
            license_token=license_token,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Narration shotlist job %s: completed in %.2fs",
            job_id,
            elapsed,
        )

        # Update DB (tenant-filtered defence-in-depth on top of RLS)
        async with get_db_session() as db:
            stmt = select(NarrationJob).where(
                NarrationJob.job_id == job_id,
                NarrationJob.tenant_id == tenant_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                row.status = "completed"
                row.narration = result

        # Write to Redis cache (fail-open) — tenant-namespaced key
        try:
            if redis:
                await redis.set(
                    job_status_key(tenant_id, job_id),
                    {"status": "completed", "narration": result},
                    ttl=job_status_ttl("completed"),
                )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            logger.debug("Redis narration shotlist cache write failed: %s", exc)

    except Exception as exc:  # noqa: BLE001 — any failure must mark the job failed,
        # not leave it wedged in ``processing``. CancelledError is BaseException so
        # cooperative cancellation still propagates uncaught.
        logger.error(
            "Narration shotlist job %s failed after %.2fs: %s",
            job_id,
            time.monotonic() - t0,
            exc,
        )
        try:
            async with get_db_session() as db:
                stmt = select(NarrationJob).where(
                    NarrationJob.job_id == job_id,
                    NarrationJob.tenant_id == tenant_id,
                )
                row = (await db.execute(stmt)).scalar_one_or_none()
                if row:
                    row.status = "failed"
                    row.error = str(exc)
        except Exception:  # best-effort failed-marking
            logger.exception("Failed to mark narration shotlist job %s as failed", job_id)
