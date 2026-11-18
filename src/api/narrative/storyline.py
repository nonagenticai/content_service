"""Storyline endpoints: generation (async), retrieval, chapter planning, and the
deterministic findings→storyline conversion."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import uuid7

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.narrative_schemas import (
    ChapterPlanRequest,
    ChapterPlanResponse,
    DegradationReport,
    FindingsToStorylineRequest,
    StorylineJobResponse,
    StorylineRequest,
    StorylineResponse,
    StorylineResultResponse,
)
from src.cache.cache_keys import (
    STORYLINE_CACHE_TTL,
    job_status_key,
    job_status_ttl,
)
from src.database.models import StorylineJob
from src.database.session import get_db, get_db_session
from src.logging_config import job_id_var
from src.services.narrative.degradation import storyline_report

from . import _shared
from ._shared import (
    _get_redis,
    _get_storyline_gen,
    _get_tenant_id,
    _storyline_cache_key_for,
    spawn_background_task,
)

if TYPE_CHECKING:
    from src.services.narrative.storyline_generator import StorylineGenerator

logger = logging.getLogger(__name__)

router = APIRouter(tags=["narrative"])


def _is_degraded(result: dict) -> bool:
    """True when the storyline is deterministic boilerplate, not LLM output.

    Mirrors ``narration._has_degraded_parts``. ``StorylineGenerator`` marks its
    fail-open placeholder with ``degraded: True`` and still reports the job
    ``completed``; caching that would multiply one transient WM failure across
    every identical request for the next ``STORYLINE_CACHE_TTL`` seconds, and
    every narration, shot list and video built from them. The job-status key
    (short TTL) is enough for the poll to work.
    """
    return bool(storyline_report(result)["degraded"])


@router.post(
    "/storyline",
    operation_id="start_storyline",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def start_storyline(
    body: StorylineRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StorylineJobResponse:
    """Start storyline generation (async).

    Args:
        body: Storyline generation parameters including analysis and mode.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        StorylineJobResponse with job_id and initial status.
    """
    logger.info(
        "Received storyline request: mode=%s, duration=%.1f min, pages=%d",
        body.mode,
        body.duration_minutes,
        len(body.analysis.pages),
    )
    gen = _get_storyline_gen(request)
    redis = _get_redis(request)
    tenant_id = _get_tenant_id(request)
    job_id = str(uuid7())

    # Check storyline cache (tenant-namespaced — never serve another tenant's body)
    cached = None
    try:
        if redis and _shared._cache_enabled():
            cache_key = _storyline_cache_key_for(body, tenant_id)
            cached = await redis.get(cache_key)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis cache check failed: %s", exc)
    if cached:
        logger.info("Storyline cache hit for job %s", job_id)
        job = StorylineJob(
            job_id=job_id,
            tenant_id=tenant_id,
            status="completed",
            storyline=cached,
        )
        db.add(job)
        await db.flush()
        # COMMIT here, exactly like the "processing" branch below. ``get_db``
        # commits in its teardown, and FastAPI runs a yield-dependency's teardown
        # AFTER the response has been sent — so relying on it would hand the
        # client a "completed" job_id whose row no other connection can see yet,
        # and the caller's immediate GET would 404.
        await db.commit()
        return StorylineJobResponse(job_id=job_id, status="completed")

    # Create processing job in DB
    job = StorylineJob(
        job_id=job_id,
        tenant_id=tenant_id,
        status="processing",
    )
    db.add(job)
    await db.flush()
    await db.commit()

    spawn_background_task(_process_storyline(gen, job_id, body, request.app, tenant_id))

    logger.info("Storyline job %s accepted for processing", job_id)
    return StorylineJobResponse(job_id=job_id, status="processing")


async def _process_storyline(
    gen: StorylineGenerator,
    job_id: str,
    body: StorylineRequest,
    app: FastAPI,
    tenant_id: str,
) -> None:
    """Background task: generate storyline via LLM and persist results."""
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
        logger.info("Storyline job %s: starting LLM generation", job_id)
        reviews_dict: dict[str, dict] | None = (
            {k: v.model_dump() for k, v in body.reviews.items()} if body.reviews else None
        )
        result = await gen.generate(
            analysis=body.analysis,
            reviews=reviews_dict,
            duration_minutes=body.duration_minutes,
            message=body.message,
            user_context=body.user_context,
            mode=body.mode,
            llm_config=body.llm_config,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            "Storyline job %s: LLM generation completed in %.2fs",
            job_id,
            elapsed,
        )

        # Update DB (tenant-filtered defence-in-depth on top of RLS)
        async with get_db_session() as db:
            stmt = select(StorylineJob).where(
                StorylineJob.job_id == job_id,
                StorylineJob.tenant_id == tenant_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                row.status = "completed"
                row.storyline = result

        # Write to Redis cache (fail-open)
        degraded = _is_degraded(result)
        if degraded:
            logger.warning(
                "Storyline job %s produced a degraded (boilerplate) storyline — "
                "skipping the %ds result cache so a transient WM failure is not "
                "replayed to every identical request",
                job_id,
                STORYLINE_CACHE_TTL,
            )
        try:
            if redis and _shared._cache_enabled():
                if not degraded:
                    cache_key = _storyline_cache_key_for(body, tenant_id)
                    await redis.set(cache_key, result, ttl=STORYLINE_CACHE_TTL)
                await redis.set(
                    job_status_key(tenant_id, job_id),
                    {"status": "completed", "storyline": result},
                    ttl=job_status_ttl("completed"),
                )
        except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
            logger.debug("Redis cache write failed: %s", exc)

    except Exception as exc:  # noqa: BLE001 — any failure must mark the job failed,
        # not leave it wedged in ``processing``. CancelledError is BaseException so
        # cooperative cancellation still propagates uncaught.
        logger.error("Storyline job %s failed: %s", job_id, exc)
        try:
            async with get_db_session() as db:
                stmt = select(StorylineJob).where(
                    StorylineJob.job_id == job_id,
                    StorylineJob.tenant_id == tenant_id,
                )
                row = (await db.execute(stmt)).scalar_one_or_none()
                if row:
                    row.status = "failed"
                    row.error = str(exc)
        except Exception:  # best-effort failed-marking
            logger.exception("Failed to mark storyline job %s as failed", job_id)


@router.get(
    "/storyline/{job_id}",
    operation_id="get_storyline_job",
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_storyline(
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> StorylineResultResponse:
    """Get storyline job status and results.

    Args:
        job_id: UUID of the storyline generation job.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        StorylineResultResponse with current status and results.

    Raises:
        HTTPException: 404 if job_id is not found.
    """
    logger.info("Polling storyline job %s", job_id)
    redis = _get_redis(request)
    tenant_id = _get_tenant_id(request)

    # Check Redis cache first (fail-open) — tenant-namespaced key
    try:
        if redis:
            cached = await redis.get(job_status_key(tenant_id, job_id))
            if cached and isinstance(cached, dict):
                storyline = None
                storyline_data = cached.get("storyline")
                token_usage = None
                if isinstance(storyline_data, dict):
                    token_usage = storyline_data.pop("token_usage", None)
                    storyline = StorylineResponse(**storyline_data)
                return StorylineResultResponse(
                    job_id=job_id,
                    status=cached["status"],
                    storyline=storyline,
                    error=cached.get("error"),
                    token_usage=token_usage,
                    # Same aggregate on the cached path as on the DB path — a
                    # signal only one of two branches emits is not a signal.
                    degradation=DegradationReport(**storyline_report(storyline_data)),
                )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis cache read failed: %s", exc)

    # Query DB (tenant-filtered defence-in-depth on top of RLS)
    stmt = select(StorylineJob).where(
        StorylineJob.job_id == job_id,
        StorylineJob.tenant_id == tenant_id,
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")

    storyline = None
    token_usage = None
    if row.storyline:
        storyline_data = row.storyline.copy()
        token_usage = storyline_data.pop("token_usage", None)
        storyline = StorylineResponse(**storyline_data)

    result = StorylineResultResponse(
        job_id=job_id,
        status=row.status,
        storyline=storyline,
        error=row.error,
        token_usage=token_usage,
        # The fabricated-storyline fallback is persisted as ``completed``. This
        # is where the caller learns that without parsing ``row.storyline``.
        degradation=DegradationReport(**storyline_report(row.storyline)),
    )

    # Write back to Redis (fail-open) — tenant-namespaced key
    try:
        if redis:
            await redis.set(
                job_status_key(tenant_id, job_id),
                {
                    "status": row.status,
                    "storyline": row.storyline,
                    "error": row.error,
                },
                ttl=job_status_ttl(row.status),
            )
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis cache writeback failed: %s", exc)

    return result


@router.post(
    "/storyline/plan",
    operation_id="plan_storyline",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def plan_storyline(
    body: ChapterPlanRequest,
    request: Request,
) -> ChapterPlanResponse:
    """Validate and rebalance chapter durations (sync).

    Args:
        body: Storyline dict with target duration and overage threshold.
        request: FastAPI request.

    Returns:
        ChapterPlanResponse with validation warnings and rebalanced storyline.
    """
    chapters = body.storyline.get("chapters")
    logger.info(
        "Received chapter plan request: target=%.1f min, chapters=%d",
        body.target_duration_minutes,
        len(chapters) if isinstance(chapters, list) else 0,
    )
    from src.services.narrative.chapter_planner import ChapterPlanner

    planner = ChapterPlanner(duration_overage_threshold=body.duration_overage_threshold)
    storyline = body.storyline.copy()
    if body.target_duration_minutes:
        storyline["target_duration_minutes"] = body.target_duration_minutes
    # `storyline` is a free-form dict authored outside this codebase; a malformed
    # shape (a non-dict chapter row, a non-numeric duration, `chapters` as a dict)
    # makes the planner raise deep in validate/rebalance. That is a BAD REQUEST →
    # 422, not a raw 500.
    try:
        warnings = planner.validate(storyline)
        rebalanced = planner.rebalance(storyline)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed storyline: {exc}") from exc
    if warnings:
        logger.warning("Chapter plan warnings: %s", warnings)
    logger.info("Chapter plan complete: %d warnings", len(warnings))
    return ChapterPlanResponse(warnings=warnings, storyline=rebalanced)


@router.post(
    "/storyline/from-findings",
    operation_id="convert_findings_to_storyline",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def storyline_from_findings(
    body: FindingsToStorylineRequest,
) -> StorylineResponse:
    """Convert critique/review findings into a Storyline (sync, no LLM).

    Args:
        body: Findings, scores, and conversion type parameters.

    Returns:
        StorylineResponse with deterministically generated storyline.
    """
    logger.info(
        "Received storyline/from-findings request: type=%s, findings=%d",
        body.conversion_type,
        len(body.findings),
    )
    from src.services.narrative.findings_converter import (
        critique_to_storyline,
        reviews_to_storyline,
    )

    # `analysis`/`reviews` are free-form dicts authored outside this codebase; a
    # malformed shape makes the converter raise deep inside. That is a BAD REQUEST
    # → 422, not 500 — same contract as the other narrative routes.
    try:
        if body.conversion_type == "reviews" and body.reviews:
            result = reviews_to_storyline(
                reviews=body.reviews,
                analysis=body.analysis,
                target_minutes=body.target_minutes,
            )
        else:
            findings_dicts = [f.model_dump() for f in body.findings]
            result = critique_to_storyline(
                findings=findings_dicts,
                scores=body.scores,
                analysis=body.analysis,
                target_minutes=body.target_minutes,
            )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed findings/analysis: {exc}") from exc

    logger.info(
        "Storyline from findings complete: %d chapters",
        len(result.get("chapters", [])),
    )
    return StorylineResponse(**result)
