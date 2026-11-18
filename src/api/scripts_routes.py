"""API routes for scripts domain (content_service)."""

from __future__ import annotations

import logging
import time
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from w2v_common.auth import LICENSE_GATE

from src.api.dependencies import get_orchestrator, get_task_manager, get_tenant_id
from src.api.scripts_schemas import (
    AsyncJobResponse,
    BatchJobResponse,
    BatchStatusResponse,
    CampaignPackResponse,
    CampaignVariantMetaResponse,
    GenerateBatchRequest,
    GenerateCampaignPackRequest,
    GenerateRequest,
    GenerationResultResponse,
    ImprovementAttemptResponse,
    JobStatusResponse,
    LLMConfigOverride,
    ScaffoldRequest,
    ScaffoldResponse,
    ValidateRequest,
    ValidationResultResponse,
)
from src.infra.task_manager import TaskManager
from src.repository.batch_repository import BatchRepository
from src.repository.job_repository import JobRepository
from src.services.scripts.campaign_pack_service import CampaignPackService
from src.services.scripts.orchestrator import ScriptOrchestrator

logger = logging.getLogger(__name__)

# ENTITLEMENT GATE (w2v_common.auth.entitlement, >=0.75.0), mounted router-wide.
# /api/scripts is the entire tenant-scoped Playwright-script surface — generate
# (single + batch + campaign pack), job/batch status reads, scaffold, cancel.
# There is no internal-callback or peer-to-peer route under this prefix, so
# nothing here has a reason to be exempt. See narrative_routes.py for the full
# rationale (402/503 contract, dormant-until-armed default); not repeated here.
router = APIRouter(prefix="/api/scripts", tags=["scripts"], dependencies=list(LICENSE_GATE))
campaign_pack_service = CampaignPackService()


# ============================================================================
# Generation Endpoints
# ============================================================================


@router.post(
    "/generate",
    operation_id="generate_script",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def generate_script(
    body: GenerateRequest,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
) -> AsyncJobResponse:
    """Generate a Playwright script for a shot list part (async)."""
    logger.info("POST /generate: tenant=%s part_id=%s", tenant_id, body.part.id)

    page_analysis = (
        {k: v.model_dump() for k, v in body.page_analysis.items()} if body.page_analysis else None
    )
    theme = body.theme.model_dump(by_alias=True) if body.theme else None
    user_context = body.user_context.model_dump() if body.user_context else None

    job_id = await orchestrator.create_job(
        tenant_id=tenant_id,
        part=body.part.model_dump(),
        page_analysis=page_analysis,
        storyline_context=body.storyline_context,
        theme=theme,
        message=body.message,
        mode=body.mode,
        source_type=body.source_type,
        user_context=user_context,
        auth_enabled=body.auth_enabled,
    )

    await task_manager.submit(
        job_id, _process_job(orchestrator, job_id, tenant_id, llm_config=body.llm_config)
    )

    return AsyncJobResponse(
        job_id=job_id,
        status="pending",
        poll_url=f"/api/scripts/jobs/{job_id}",
    )


@router.post(
    "/generate/batch",
    operation_id="generate_script_batch",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def generate_batch(
    body: GenerateBatchRequest,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
) -> BatchJobResponse:
    """Generate scripts for multiple parts (always async)."""
    logger.info("POST /generate/batch: tenant=%s parts=%d", tenant_id, len(body.parts))

    page_analysis = (
        {k: v.model_dump() for k, v in body.page_analysis.items()} if body.page_analysis else None
    )
    theme = body.theme.model_dump(by_alias=True) if body.theme else None
    user_context = body.user_context.model_dump() if body.user_context else None

    batch_id, job_ids = await orchestrator.create_batch(
        tenant_id=tenant_id,
        parts=[p.model_dump() for p in body.parts],
        page_analysis=page_analysis,
        storyline_context=body.storyline_context,
        theme=theme,
        message=body.message,
        mode=body.mode,
        source_type=body.source_type,
        user_context=user_context,
        auth_enabled=body.auth_enabled,
    )

    for jid in job_ids.values():
        await task_manager.submit(
            jid,
            _process_job(
                orchestrator, jid, tenant_id, batch_id=batch_id, llm_config=body.llm_config
            ),
        )

    return BatchJobResponse(batch_id=batch_id, job_ids=job_ids, status="pending")


@router.post(
    "/generate/campaign-pack",
    response_model=CampaignPackResponse,
    operation_id="generate_campaign_pack",
    responses={
        422: {
            "description": "Campaign pack expands to too many parts or request body validation error"
        },
    },
)
async def generate_campaign_pack(
    body: GenerateCampaignPackRequest,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
) -> CampaignPackResponse:
    """Generate a template-driven multi-style campaign pack from one analysis payload."""
    logger.info(
        "POST /generate/campaign-pack: tenant=%s styles=%d", tenant_id, len(body.script_styles)
    )

    page_analysis = (
        {k: v.model_dump() for k, v in body.page_analysis.items()} if body.page_analysis else None
    )
    theme = body.theme.model_dump(by_alias=True) if body.theme else None
    user_context = body.user_context.model_dump() if body.user_context else None

    expanded_parts, variant_meta = campaign_pack_service.build_parts(
        seed_part=body.part.model_dump() if body.part else None,
        page_analysis=page_analysis,
        script_styles=body.script_styles,
        variants_per_style=body.variants_per_style,
        variant_duration_seconds=body.variant_duration_seconds,
        hooks=body.hooks,
        ctas=body.ctas,
    )

    if len(expanded_parts) > 50:
        raise HTTPException(
            status_code=422,
            detail=f"Campaign pack expands to {len(expanded_parts)} parts (max 50). "
            "Reduce styles or variants_per_style.",
        )

    batch_id, job_ids = await orchestrator.create_batch(
        tenant_id=tenant_id,
        parts=expanded_parts,
        page_analysis=page_analysis,
        storyline_context=body.storyline_context,
        theme=theme,
        message=body.message,
        mode="webapp_commercial",
        source_type=body.source_type,
        user_context=user_context,
        auth_enabled=body.auth_enabled,
    )

    for jid in job_ids.values():
        await task_manager.submit(
            jid,
            _process_job(
                orchestrator, jid, tenant_id, batch_id=batch_id, llm_config=body.llm_config
            ),
        )

    return CampaignPackResponse(
        batch_id=batch_id,
        job_ids=job_ids,
        status="pending",
        total_variants=len(expanded_parts),
        variants=[CampaignVariantMetaResponse.model_validate(item) for item in variant_meta],
    )


async def _process_job(
    orchestrator: ScriptOrchestrator,
    job_id: str,
    tenant_id: str,
    batch_id: str | None = None,
    llm_config: LLMConfigOverride | None = None,
) -> None:
    """Background task that processes a script generation job."""
    t0 = time.monotonic()
    logger.info("Background job %s started for tenant=%s", job_id, tenant_id)
    try:
        await orchestrator.process_job(job_id, tenant_id, llm_config=llm_config)
        logger.info("Background job %s completed in %.2fs", job_id, time.monotonic() - t0)
    except Exception:
        logger.exception("Background job %s failed", job_id)

    if batch_id:
        try:
            await orchestrator.batch_repo.update_status(batch_id, tenant_id=tenant_id)
        except Exception:
            logger.exception("Failed to update batch status for job %s", job_id)


# ============================================================================
# Job Status Endpoints
# ============================================================================


@router.get(
    "/jobs/{job_id}",
    operation_id="get_script_job_status",
    responses={
        404: {"description": "Job not found"},
    },
)
async def get_job_status(
    job_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> JobStatusResponse:
    """Return current status + result for a generation job (tenant-scoped)."""
    job_repo: JobRepository = request.app.state.orchestrator.job_repo
    job = await job_repo.get(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    token_usage = None
    row = await job_repo.get_row(job_id, tenant_id=tenant_id)
    if row and row.extraction_tokens_used:
        token_usage = {"total_tokens": row.extraction_tokens_used}

    return JobStatusResponse(
        job_id=job.job_id,
        status=job.status.value,
        part_id=job.part_id,
        created_at=job.created_at.isoformat() if job.created_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error=job.error_message,
        script_source=job.script_source,
        script_path=job.script_path,
        # job is the domain Job (UUID-typed); _to_domain already converted from DB string.
        script_artifact_id=job.script_artifact_id,
        generation_time_seconds=job.generation_time_seconds,
        token_usage=token_usage,
    )


# ============================================================================
# Validation Endpoints
# ============================================================================


@router.post(
    "/jobs/{job_id}/validate",
    operation_id="validate_script",
    responses={
        404: {"description": "Job not found"},
        409: {"description": "Job not ready for validation"},
        422: {"description": "Request body validation error"},
    },
)
async def validate_script(
    job_id: str,
    body: ValidateRequest,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
) -> ValidationResultResponse:
    """Validate a generated script (TypeScript, Playwright checks, auto-improvement)."""
    logger.info("POST /jobs/%s/validate: tenant=%s", job_id, tenant_id)
    t0 = time.monotonic()

    job_repo: JobRepository = orchestrator.job_repo
    job = await job_repo.get(job_id, tenant_id=tenant_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    if job.status.value != "completed":
        raise HTTPException(
            status_code=409,
            detail=f"Job not ready (status: {job.status.value})",
            headers={"Retry-After": "30"},
        )

    result = await orchestrator.validate_job(job_id, tenant_id, body.scripts_dir)
    logger.info(
        "POST /jobs/%s/validate: accepted=%s in %.2fs",
        job_id,
        result.accepted,
        time.monotonic() - t0,
    )

    return ValidationResultResponse(
        job_id=job_id,
        part_id=job.part_id,
        compiles=result.compiles,
        discoverable=result.discoverable,
        runs_headless=result.runs_headless,
        accepted=result.accepted,
        errors=result.errors,
        improvement_attempts=result.improvement_attempts,
        attempts_detail=[
            ImprovementAttemptResponse(
                attempt=a.attempt,
                errors=a.errors,
                fix_applied=a.fix_applied,
                accepted=a.accepted,
                reason=a.reason,
            )
            for a in result.attempts_detail
        ],
    )


# ============================================================================
# Scaffold Endpoints
# ============================================================================


@router.post(
    "/jobs/{job_id}/scaffold",
    response_model=ScaffoldResponse,
    operation_id="scaffold_script_project",
    responses={
        422: {"description": "Request body validation error"},
    },
)
async def scaffold_project(
    job_id: str,
    body: ScaffoldRequest,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
) -> ScaffoldResponse:
    """Scaffold a TypeScript project for a job (tenant-scoped)."""
    logger.info("POST /jobs/%s/scaffold: tenant=%s", job_id, tenant_id)
    theme = body.theme.model_dump(by_alias=True) if body.theme else None
    result = await orchestrator.scaffold_job(job_id, tenant_id=tenant_id, theme=theme)
    return ScaffoldResponse(**result)


# ============================================================================
# Job Cancellation
# ============================================================================


@router.delete(
    "/jobs/{job_id}",
    operation_id="cancel_script_job",
    responses={
        404: {"description": "Job not found or already finished"},
    },
)
async def cancel_job(
    job_id: str,
    tenant_id: str = Depends(get_tenant_id),
    orchestrator: ScriptOrchestrator = Depends(get_orchestrator),
    task_manager: TaskManager = Depends(get_task_manager),
) -> dict:
    """Cancel a running job (tenant-scoped).

    The DB-side cancel is gated on tenant_id matching; the in-process task
    cancel runs only if the DB cancel succeeded, so a wrong-tenant call
    cannot kill another tenant's in-flight task even if the job_id is known.
    """
    db_cancelled = await orchestrator.cancel_job(job_id, tenant_id=tenant_id)
    if db_cancelled:
        # Cross-replica cancel: set a Redis flag the running task polls, so a
        # task on ANOTHER pod stops too. The in-process task_manager.cancel
        # below only reaches a task on THIS pod.
        await orchestrator.request_remote_cancel(job_id, tenant_id=tenant_id)
    task_cancelled = await task_manager.cancel(job_id) if db_cancelled else False

    if not task_cancelled and not db_cancelled:
        raise HTTPException(status_code=404, detail=f"Job not found or already finished: {job_id}")

    return {"job_id": job_id, "status": "cancelled"}


# ============================================================================
# Batch Status Endpoints
# ============================================================================


@router.get(
    "/batches/{batch_id}",
    response_model=BatchStatusResponse,
    operation_id="get_script_batch_status",
    responses={
        404: {"description": "Batch not found"},
    },
)
async def get_batch_status(
    batch_id: str,
    request: Request,
    tenant_id: str = Depends(get_tenant_id),
) -> BatchStatusResponse:
    """Get batch generation status (tenant-scoped)."""
    batch_repo: BatchRepository = request.app.state.orchestrator.batch_repo
    data = await batch_repo.get_status(batch_id, tenant_id=tenant_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Batch not found: {batch_id}")

    results = [
        GenerationResultResponse(
            job_id=job.job_id,
            part_id=job.part_id,
            status=job.status,
            script_source=job.script_source,
            script_path=job.script_path,
            # These are raw ORM rows from batch_repo.get_status — script_artifact_id is the DB string.
            script_artifact_id=UUID(job.script_artifact_id) if job.script_artifact_id else None,
            generation_time_seconds=job.generation_time_seconds,
            provider_used=job.provider_used,
            error=job.error_message,
            token_usage={"total_tokens": job.extraction_tokens_used}
            if job.extraction_tokens_used
            else None,
        )
        for job in data["jobs"]
    ]

    return BatchStatusResponse(
        batch_id=data["batch_id"],
        total=data["total"],
        completed=data["completed"],
        failed=data["failed"],
        status=data["status"],
        results=results,
    )
