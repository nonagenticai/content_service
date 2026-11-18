"""Shot-list endpoints: deterministic build (sync), AI enhancement, and the
deterministic findings→shotlist conversion."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.narrative_schemas import (
    DegradationReport,
    FindingsToShotListRequest,
    ShotListEnhanceRequest,
    ShotListEnhanceResponse,
    ShotListRequest,
    ShotListResponse,
)
from src.cache.cache_keys import SHOTLIST_CACHE_TTL, shotlist_cache_key
from src.config import get_settings
from src.database.models import StorylineJob
from src.database.session import get_db
from src.services.narrative.degradation import shotlist_report

from . import _shared
from ._shared import _get_redis, _get_shotlist_builder, _get_tenant_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["narrative"])

# The shot_list backfill below is a best-effort side-effect: a database fault
# must NOT cost the caller the shot list it asked for. That only holds if the
# guard catches what the database actually raises. ``SQLAlchemyError`` inherits
# from NONE of the builtins whose names it shadows::
#
#     issubclass(sqlalchemy.exc.OperationalError, OSError | RuntimeError) -> False
#
# so the builtins-only ``except (OSError, ValueError, RuntimeError, TimeoutError)``
# this replaces was DEAD on the one exception class the block exists to absorb:
# a psycopg/pgbouncer blip surfaced as ``sqlalchemy.exc.OperationalError``,
# escaped the guard, and FastAPI answered 500 on a route that had already built
# a perfectly good shot list. Same defect, and same fix, as ``_DB_PROBE_ERRORS``
# in ``src/api/health.py`` — see the note there. The builtins stay in the tuple
# because a socket-level error can still surface as one.
_BACKFILL_ERRORS = (OSError, ValueError, RuntimeError, TimeoutError, SQLAlchemyError)


def _with_degradation(result: dict) -> ShotListResponse:
    """Attach the top-level aggregate to a shot list built (or cached) as a dict.

    ``ShotListBuilder`` marks an unrecordable part (a chapter that declared
    ``pages: []``, so there is no URL) with ``degraded``/``degraded_reason``.
    Rolling those up HERE is what lets a caller read one boolean off the response
    instead of walking ``parts[*]`` — and it is computed on the cached path too,
    because a signal only one branch emits is not a signal. The stored dict never
    carries ``degradation``; it is always derived, so a cache entry written
    before this field existed still reports correctly.
    """
    payload = {k: v for k, v in result.items() if k != "degradation"}
    return ShotListResponse(**payload, degradation=DegradationReport(**shotlist_report(result)))


@router.post(
    "/shotlist",
    operation_id="build_shotlist",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def build_shotlist(
    body: ShotListRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> ShotListResponse:
    """Build shot list from storyline (sync).

    Args:
        body: Storyline dict and analysis input.
        request: FastAPI request with app state.
        db: Async database session (injected).

    Returns:
        ShotListResponse with parts, recording order, and duration.
    """
    logger.info("Received shotlist build request: pages=%d", len(body.analysis.pages))
    redis = _get_redis(request)
    tenant_id = _get_tenant_id(request)

    # Check cache (tenant-namespaced)
    try:
        if redis and _shared._cache_enabled():
            cache_key = shotlist_cache_key(tenant_id, body.storyline, body.analysis.model_dump())
            cached = await redis.get(cache_key)
            if cached and isinstance(cached, dict):
                return _with_degradation(cached)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis shotlist cache check failed: %s", exc)

    builder = _get_shotlist_builder(request)
    t0 = time.monotonic()
    # `storyline` is a free-form dict authored outside this codebase; a malformed
    # shape makes the builder raise deep inside. That is a BAD REQUEST → 422, not 500.
    try:
        result = builder.build(body.storyline, body.analysis)
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed storyline: {exc}") from exc
    elapsed = time.monotonic() - t0
    logger.info(
        "Shotlist built: %d parts, %.1fs total duration, took %.3fs",
        result.get("total_parts", 0),
        result.get("estimated_total_duration_seconds", 0),
        elapsed,
    )

    # Write to cache (fail-open)
    try:
        if redis and _shared._cache_enabled():
            cache_key = shotlist_cache_key(tenant_id, body.storyline, body.analysis.model_dump())
            await redis.set(cache_key, result, ttl=SHOTLIST_CACHE_TTL)
    except (OSError, ValueError, RuntimeError, TimeoutError) as exc:
        logger.debug("Redis shotlist cache write failed: %s", exc)

    # Backfill shot_list on StorylineJob if job_id provided (tenant-filtered)
    if body.job_id:
        try:
            stmt = select(StorylineJob).where(
                StorylineJob.job_id == body.job_id,
                StorylineJob.tenant_id == tenant_id,
            )
            row = (await db.execute(stmt)).scalar_one_or_none()
            if row:
                row.shot_list = result
        except _BACKFILL_ERRORS as exc:
            # Roll the failed transaction back explicitly. Measured on PG 18:
            # this is NOT load-bearing today -- SQLAlchemy 2.x deactivates the
            # transaction on a DBAPI error, so ``get_db``'s teardown ``commit()``
            # silently rolls back and returns rather than raising. It is kept
            # because the alternative is relying on that implicit behaviour to
            # clean up an aborted session after a 200 is already on the wire.
            # Do not read the assertion on it in the tests as proof of a crash
            # it prevents; it pins the explicit intent, not a live failure mode.
            await db.rollback()
            logger.warning("Failed to backfill shot_list on job %s: %s", body.job_id, exc)

    return _with_degradation(result)


@router.post(
    "/shotlist/enhance",
    operation_id="enhance_shotlist",
    responses={
        422: {"description": "Request body validation error"},
        501: {"description": "AI creative enhancement is disabled"},
    },
)
async def enhance_shotlist(
    body: ShotListEnhanceRequest,
    request: Request,
) -> ShotListEnhanceResponse:
    """AI-enhance shot list actions.

    Args:
        body: Shot list, storyline, and analysis for enhancement.
        request: FastAPI request with app state.

    Returns:
        ShotListEnhanceResponse with AI-enhanced actions.

    Raises:
        HTTPException: 501 if AI creative enhancement is disabled.
    """
    logger.info("Received shotlist enhance request")
    settings = get_settings()
    if not settings.shotlist.ai_creative:
        logger.warning("Shotlist enhance rejected: AI creative enhancement is disabled")
        raise HTTPException(
            status_code=501,
            detail="AI creative enhancement is disabled. Set SHOTLIST_AI_CREATIVE=true to enable.",
        )
    enhancer = request.app.state.shotlist_enhancer
    t0 = time.monotonic()
    result = await enhancer.enhance(
        body.shot_list, body.storyline, body.analysis, llm_config=body.llm_config
    )
    elapsed = time.monotonic() - t0
    logger.info("Shotlist enhancement completed in %.2fs", elapsed)
    return ShotListEnhanceResponse(shot_list=result)


@router.post(
    "/shotlist/from-findings",
    operation_id="convert_findings_to_shotlist",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def shotlist_from_findings(
    body: FindingsToShotListRequest,
) -> ShotListResponse:
    """Convert a Storyline + findings/reviews into a ShotList (sync, no LLM).

    Args:
        body: Storyline, findings, and conversion type parameters.

    Returns:
        ShotListResponse with deterministically generated shot list.
    """
    logger.info(
        "Received shotlist/from-findings request: type=%s, findings=%d",
        body.conversion_type,
        len(body.findings),
    )
    from src.services.narrative.findings_converter import critique_to_shotlist, reviews_to_shotlist

    # `storyline` is a free-form dict authored outside this codebase; a malformed
    # shape makes the converter raise deep inside. That is a BAD REQUEST → 422, not
    # 500 — same contract as the build_shotlist route above.
    try:
        if body.conversion_type == "reviews" and body.reviews:
            result = reviews_to_shotlist(
                storyline=body.storyline,
                reviews=body.reviews,
            )
        else:
            findings_dicts = [f.model_dump() for f in body.findings]
            result = critique_to_shotlist(
                storyline=body.storyline,
                findings=findings_dicts,
                scores=body.scores,
            )
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"malformed storyline/findings: {exc}") from exc

    logger.info(
        "Shotlist from findings complete: %d parts",
        result.get("total_parts", 0),
    )
    return _with_degradation(result)
