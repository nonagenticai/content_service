"""Thin coordinator: scaffold -> generate -> store -> validate."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import NamedTuple
from uuid import UUID

import httpx
from w2v_common.clients import ArtifactClient

from src.api.scripts_schemas import LLMConfigOverride
from src.domain.job import Job
from src.domain.script import ValidationResult
from src.infra.cancel_flags import CancelFlags, JobCancelledError
from src.repository.batch_repository import BatchRepository
from src.repository.job_repository import JobRepository
from src.repository.usage_repository import UsageRepository
from src.services.scripts.generation_service import GenerationService
from src.services.scripts.improvement_service import ImprovementService
from src.services.scripts.scaffold_service import ScaffoldService
from src.services.scripts.storage_service import StorageService
from src.services.scripts.validation_service import ValidationService

logger = logging.getLogger(__name__)


class _ImprovedScript(NamedTuple):
    """Outcome of the auto-improve loop.

    ``path``/``artifact_id``/``source`` are populated ONLY when the fix loop
    produced a newly accepted script; they are then written back to the row so
    the durable artifact stops being the un-improved version. ``None`` means
    "leave the generation-time values alone".
    """

    result: ValidationResult
    path: str | None = None
    artifact_id: UUID | None = None
    source: str | None = None


class ScriptOrchestrator:
    """Orchestrates the script generation pipeline."""

    def __init__(
        self,
        *,
        job_repo: JobRepository,
        batch_repo: BatchRepository,
        usage_repo: UsageRepository,
        generation_service: GenerationService,
        validation_service: ValidationService,
        improvement_service: ImprovementService,
        scaffold_service: ScaffoldService | None,
        storage: StorageService,
        artifact_client: ArtifactClient | None = None,
        cancel_flags: CancelFlags | None = None,
    ) -> None:
        self._job_repo = job_repo
        self._batch_repo = batch_repo
        self._usage_repo = usage_repo
        self._generation = generation_service
        self._validation = validation_service
        self._improvement = improvement_service
        self._scaffold = scaffold_service
        self._storage = storage
        self._artifact_client = artifact_client or ArtifactClient()
        # Cross-replica cooperative cancel. Defaults to a no-op (Redis-less)
        # instance so unit tests and single-replica dev keep working.
        self._cancel_flags = cancel_flags or CancelFlags(None)
        self._scaffold_locks: dict[str, asyncio.Lock] = {}

    @property
    def cancel_flags(self) -> CancelFlags:
        """Public access to the cross-replica cancel-flag helper."""
        return self._cancel_flags

    async def request_remote_cancel(self, job_id: str, *, tenant_id: str) -> None:
        """Set the cross-replica cancel flag so a task on any pod stops.

        Complements the in-process ``TaskManager.cancel`` fast-path: the flag
        reaches a task running on a different replica, which the local task
        dict cannot. Tenant-scoped so it cannot target another tenant's job.
        """
        await self._cancel_flags.request_cancel(job_id, tenant_id=tenant_id)

    @property
    def job_repo(self) -> JobRepository:
        """Public access to the job repository."""
        return self._job_repo

    @property
    def batch_repo(self) -> BatchRepository:
        """Public access to the batch repository."""
        return self._batch_repo

    async def create_job(
        self,
        *,
        tenant_id: str,
        part: dict,
        page_analysis: dict | None = None,
        storyline_context: str | None = None,
        theme: dict | None = None,
        message: str | None = None,
        mode: str | None = None,
        source_type: str | None = None,
        user_context: dict | None = None,
        auth_enabled: bool = False,
        batch_id: str | None = None,
    ) -> str:
        """Create a job in the database, return job_id."""
        shot_details = dict(part or {})
        if message:
            shot_details["user_message"] = message

        job = await self._job_repo.create(
            tenant_id=tenant_id,
            part_id=shot_details.get("id", "unknown"),
            part_type=shot_details.get("part_type", "browser_recording"),
            page_url=None,
            page_type=shot_details.get("page_type"),
            shot_details=shot_details,
            page_analysis=page_analysis,
            theme=theme,
            storyline_context=storyline_context,
            user_context=user_context,
            mode=mode,
            source_type=source_type,
            auth_enabled=auth_enabled,
            batch_id=batch_id,
        )
        return job.job_id

    async def _record_usage_best_effort(
        self,
        *,
        tenant_id: str,
        scripts_generated: int = 0,
        scripts_accepted: int = 0,
        llm_tokens_used: int = 0,
        is_error: bool = False,
    ) -> None:
        """Write daily usage counters without ever failing the caller's job.

        Usage/token accounting is non-critical bookkeeping: it must never turn
        an otherwise-successful, already-``mark_completed``ed (and
        artifact-uploaded) generation into a ``failed`` job. Previously the
        success-path ``record`` sat inside ``process_job``'s try, so a
        transient DB error on the counter upsert fell into the ``except`` that
        calls ``mark_failed`` — discarding paid LLM output and re-charging the
        tenant on the retry. Errors are logged and swallowed; the row is
        reconstructible from ``script_jobs``.
        """
        try:
            await self._usage_repo.record(
                tenant_id=tenant_id,
                scripts_generated=scripts_generated,
                scripts_accepted=scripts_accepted,
                llm_tokens_used=llm_tokens_used,
                is_error=is_error,
            )
        except Exception:
            # Deliberately broad: no accounting failure of any shape
            # (SQLAlchemyError, OSError, TimeoutError, ...) may reach the
            # job-failure path. CancelledError is a BaseException and still
            # propagates.
            logger.exception(
                "Usage accounting write failed for tenant=%s "
                "(generated=%d accepted=%d tokens=%d error=%s) — job outcome unaffected",
                tenant_id,
                scripts_generated,
                scripts_accepted,
                llm_tokens_used,
                is_error,
            )

    async def _mark_failed_best_effort(self, job_id: str, error: str, *, tenant_id: str) -> None:
        """Write the terminal 'failed' status without masking the real cause.

        ``mark_failed`` runs a raw ``session.execute`` + ``commit``. Its own
        write can fail (DB blip, RLS/connection error, etc.) — and this call
        lives inside ``process_job``'s ``except Exception as exc`` handler, so
        an unguarded failure here would propagate the bookkeeping error and
        bury ``exc``, the actual reason the job failed. The row is left at
        'processing' until the ``StaleJobReconciler`` backstop (default 1800s)
        reconciles it — worse latency on the status, but not silently wrong.
        Mirrors ``_record_usage_best_effort``: log and swallow; ``raise`` in
        the caller's ``except`` block still re-raises the original exception.
        """
        try:
            await self._job_repo.mark_failed(job_id, error, tenant_id=tenant_id)
        except Exception:
            # Deliberately broad, same rationale as _record_usage_best_effort:
            # no bookkeeping-write failure of any shape may mask the original
            # job error. CancelledError is a BaseException and still propagates.
            logger.exception(
                "mark_failed write failed for job %s tenant=%s — original error was: %s",
                job_id,
                tenant_id,
                error,
            )

    async def process_job(
        self,
        job_id: str,
        tenant_id: str,
        llm_config: LLMConfigOverride | None = None,
    ) -> None:
        """Full pipeline: scaffold -> generate -> store -> record usage."""
        t0 = time.monotonic()
        logger.info("Processing job %s for tenant=%s", job_id, tenant_id)
        job = await self._job_repo.get(job_id, tenant_id=tenant_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")

        await self._job_repo.mark_processing(job_id, tenant_id=tenant_id)

        async def _cancel_check() -> bool:
            """Poll the cross-replica cancel flag from inside the generate loop."""
            return await self._cancel_flags.is_cancelled(job_id, tenant_id=tenant_id)

        try:
            part = job.shot_details or {}
            part_type = part.get("part_type", job.part_type)
            storage_key = job.batch_id or job_id

            # Cooperative cancel checkpoint before any expensive work.
            await self._cancel_flags.raise_if_cancelled(job_id, tenant_id=tenant_id)

            # 1. Scaffold
            job_dir = await asyncio.to_thread(self._storage.get_job_dir, storage_key)
            if self._scaffold is not None and part_type != "title_card":
                theme_dict = job.theme if isinstance(job.theme, dict) else None
                await self._ensure_scaffold(storage_key, job_dir, theme_dict)

            await self._cancel_flags.raise_if_cancelled(job_id, tenant_id=tenant_id)

            # 2. Generate
            if part_type == "title_card":
                script = self._generation.generate_title_card(part)
            else:
                script = await self._generation.generate(
                    part=part,
                    page_analysis=job.page_analysis,
                    storyline_context=job.storyline_context,
                    theme=job.theme,
                    user_context=job.user_context,
                    mode=job.mode,
                    source_type=job.source_type,
                    auth_enabled=job.auth_enabled,
                    message=part.get("user_message"),
                    llm_config=llm_config,
                    cancel_check=_cancel_check,
                )
            logger.info("Generation completed for job %s", job_id)

            # 3. Store
            script_path = await self._storage.store_script(
                storage_key,
                script.script_source,
                job.part_id,
                part_type=part_type,
                source_type=job.source_type,
            )
            if script.prompt_used:
                await self._storage.store_prompt(storage_key, script.prompt_used, job.part_id)

            # 3b. Upload script bytes to artifact_service.
            # Required: vps's record stage downloads scripts via artifact_id, so
            # a missing script_artifact_id breaks the downstream pipeline. Any
            # upload failure must propagate so the job is marked failed and the
            # pipeline halts at a clear cause, instead of surfacing later as a
            # mysterious "no scripts_dir" record-stage error.
            #
            # Use the BATCH id as artifact-service project_id so all parts in
            # a single generate batch share one project scope. The orchestrator
            # passes that same batch_id as project_id when calling vps's
            # /api/record/batch_from_artifacts; mismatch (e.g. pipeline_job_id
            # ≠ per-part script_job_id) causes 404s downstream.
            upload_project_id = UUID(job.batch_id or job_id)
            script_bytes = await asyncio.to_thread(Path(script_path).read_bytes)
            filename = Path(script_path).name
            upload = await self._artifact_client.upload_bytes(
                project_id=upload_project_id,
                filename=filename,
                content=script_bytes,
                content_type="text/plain",
                kind="other",
                step=job.part_id,
                tags=["w2v-script", f"w2v-pipeline-{job_id}"],
            )
            script_artifact_id: UUID = upload.artifact_id
            logger.info(
                "Uploaded script for job %s to artifact_service: artifact_id=%s",
                job_id,
                script_artifact_id,
            )

            # 4. Mark completed + record usage
            await self._job_repo.mark_completed(
                job_id,
                tenant_id=tenant_id,
                script_path=script_path,
                script_artifact_id=script_artifact_id,
                script_source=script.script_source,
                prompt_used=script.prompt_used,
                generation_time_seconds=script.generation_time_seconds,
                provider_used=script.provider_used,
                extraction_tokens_used=script.llm_tokens_used,
            )
            await self._record_usage_best_effort(
                tenant_id=tenant_id,
                scripts_generated=1,
                llm_tokens_used=script.llm_tokens_used,
            )

        except JobCancelledError:
            # A cross-replica cancel flag fired mid-run. The DELETE route already
            # marked the row cancelled (cross-pod DB write); mark again to be
            # idempotent in case the flag was set by another path, clear the
            # flag, and stop WITHOUT recording an error (this is not a failure).
            logger.info("Job %s cancelled cooperatively via flag", job_id)
            await self._job_repo.mark_cancelled(job_id, tenant_id=tenant_id)
            await self._cancel_flags.clear(job_id, tenant_id=tenant_id)
            return
        except Exception as exc:
            # Catch broadly and fail the job (CLAUDE.md contract). The old narrow
            # tuple let httpx.InvalidURL (NOT an httpx.HTTPError) and TypeError/KeyError
            # from response parsing escape → the row sat 'processing' until the 1800s
            # reconciler backstop. asyncio.CancelledError is a BaseException and still
            # propagates uncaught; JobCancelledError is handled above.
            logger.exception("Job %s failed after %.2fs: %s", job_id, time.monotonic() - t0, exc)
            await self._mark_failed_best_effort(job_id, str(exc), tenant_id=tenant_id)
            await self._record_usage_best_effort(tenant_id=tenant_id, is_error=True)
            raise

        # Clear any stale cancel flag on clean completion so a later job id
        # collision (should not happen with uuid7) can never inherit it.
        await self._cancel_flags.clear(job_id, tenant_id=tenant_id)
        logger.info("Job %s fully processed in %.2fs", job_id, time.monotonic() - t0)

    async def validate_job(
        self,
        job_id: str,
        tenant_id: str,
        scripts_dir: str | None = None,
    ) -> ValidationResult:
        """Validate -> auto-improve -> store result.

        ``scripts_dir`` is accepted for wire compatibility but is **ignored**:
        the validation working directory is derived solely from the job's
        storage key. Honouring an attacker-controlled absolute path here would
        let a request set the cwd of ``npx tsc`` / ``npx playwright test`` to an
        arbitrary location (RCE-capable). The path is always confined to the
        storage root via ``storage_key``.
        """
        job = await self._job_repo.get(job_id, tenant_id=tenant_id)
        if job is None:
            raise ValueError(f"Job not found: {job_id}")
        if not job.script_source:
            raise ValueError(f"Job {job_id} has no generated script to validate")

        if scripts_dir:
            logger.warning(
                "Ignoring caller-supplied scripts_dir override for job %s (security)", job_id
            )

        storage_key = job.batch_id or job_id
        sdir = await asyncio.to_thread(
            lambda: self._storage.get_scripts_dir(self._storage.get_job_dir(storage_key))
        )

        # Multi-replica safety: the pod handling this validate/improve request
        # may NOT be the pod that generated the script. get_scripts_dir just
        # created an empty working dir on THIS pod's disk; rebuild the part's
        # script file from the job's durable state (artifact_service, else the
        # persisted script_source) so validation reads real content instead of
        # a phantom empty file. No-op fast path when the file is already here.
        await self._ensure_local_script(job, sdir)

        async def _cancel_check() -> bool:
            return await self._cancel_flags.is_cancelled(job_id, tenant_id=tenant_id)

        result = await self._validation.validate(sdir, job.part_id)

        improved = _ImprovedScript(result=result)
        if not result.accepted and result.errors:
            improved = await self._auto_improve(
                job,
                job_id,
                tenant_id=tenant_id,
                storage_key=storage_key,
                sdir=sdir,
                result=result,
                cancel_check=_cancel_check,
            )
            result = improved.result

        newly_accepted = await self._job_repo.mark_validated(
            job_id,
            tenant_id=tenant_id,
            script_path=improved.path,
            script_artifact_id=improved.artifact_id,
            script_source=improved.source,
            compiles=result.compiles,
            discoverable=result.discoverable,
            runs_headless=result.runs_headless,
            accepted=result.accepted,
            validation_errors=result.errors,
            improvement_attempts=result.improvement_attempts,
        )

        # Bill the acceptance ONCE per script, on the not-accepted -> accepted
        # transition claimed by ``mark_validated``. Validation is re-runnable
        # (the route gates only on status == 'completed', which mark_validated
        # leaves untouched) and cross-replica, so billing on every call charged
        # one script as N accepted scripts.
        if newly_accepted:
            await self._record_usage_best_effort(
                tenant_id=tenant_id,
                scripts_accepted=1,
            )

        return result

    async def _auto_improve(
        self,
        job: Job,
        job_id: str,
        *,
        tenant_id: str,
        storage_key: str,
        sdir: Path,
        result: ValidationResult,
        cancel_check: Callable[[], Awaitable[bool]],
    ) -> _ImprovedScript:
        """Run the fix loop and, on acceptance, refresh BOTH durable copies.

        Two halves used to be missing after the fix loop succeeded:

        * ``store_script`` was called WITHOUT ``part_type``/``source_type``, so
          it took the else-branch and wrote ``<job_dir>/<part_id>.demo.spec.ts``
          — the job ROOT — while validation reads ``<job_dir>/scripts/parts/``.
          The improved bytes landed in a file nothing ever reads.
        * The artifact was never re-uploaded, so ``script_artifact_id`` still
          pointed at the un-improved, failing script that the record stage
          downloads. ``accepted=true`` sat on top of a broken artifact.

        The upload deliberately propagates on failure: raising leaves the row at
        its previous (not-accepted) validation state, which is honest, rather
        than reporting accepted against a stale artifact.
        """
        assert result.errors is not None  # noqa: S101 — guarded by the caller
        part_type = (job.shot_details or {}).get("part_type", job.part_type)
        script_path = self._storage.part_script_path(
            sdir, job.part_id, part_type=part_type, source_type=job.source_type
        )
        if not await asyncio.to_thread(script_path.exists):
            for candidate in (
                sdir / "parts" / f"{job.part_id}.demo.spec.ts",
                sdir / "parts" / f"{job.part_id}.ts",
            ):
                if await asyncio.to_thread(candidate.exists):
                    script_path = candidate
                    break

        try:
            # ``result`` is the pre-improvement measurement of the file that is
            # on disk right now. Hand it over as the baseline: if every fix
            # attempt is exhausted the loop restores that exact file, and this
            # is the only honest compile/discover/dry-run reading for it.
            baseline = result
            result = await self._improvement.improve(
                sdir,
                script_path,
                baseline.errors or [],
                self._validation,
                cancel_check=cancel_check,
                baseline=baseline,
            )
        except JobCancelledError:
            # A cross-replica cancel fired during the fix loop. Keep the
            # pre-improvement result and stop improving; the DELETE route
            # has already marked the row cancelled.
            logger.info("Validation improvement for job %s cancelled via flag", job_id)
            await self._cancel_flags.clear(job_id, tenant_id=tenant_id)

        if not result.accepted:
            return _ImprovedScript(result=result)

        source = await asyncio.to_thread(script_path.read_text, encoding="utf-8")
        stored_path = await self._storage.store_script(
            storage_key,
            source,
            job.part_id,
            part_type=part_type,
            source_type=job.source_type,
        )
        artifact_id = await self._reupload_script(job, job_id, Path(stored_path), source)
        return _ImprovedScript(
            result=result, path=stored_path, artifact_id=artifact_id, source=source
        )

    async def _reupload_script(self, job: Job, job_id: str, script_path: Path, source: str) -> UUID:
        """Upload the improved script bytes to artifact_service and return the new id.

        Mirrors the generation-time upload (same project_id/tags contract) so
        the record stage downloads the ACCEPTED script, not the one that failed
        validation. ``source`` is written already; the bytes are re-read from
        disk so the artifact is byte-identical to the local file.
        """
        upload_project_id = UUID(job.batch_id or job_id)
        script_bytes = await asyncio.to_thread(script_path.read_bytes)
        upload = await self._artifact_client.upload_bytes(
            project_id=upload_project_id,
            filename=script_path.name,
            content=script_bytes,
            content_type="text/plain",
            kind="other",
            step=job.part_id,
            tags=["w2v-script", f"w2v-pipeline-{job_id}"],
        )
        logger.info(
            "Re-uploaded improved script for job %s to artifact_service: artifact_id=%s",
            job_id,
            upload.artifact_id,
        )
        return upload.artifact_id

    async def _ensure_local_script(self, job: Job, sdir: Path) -> None:
        """Rebuild this pod's working copy of the part script from durable state.

        Multi-replica safety: validate/improve may land on a pod that never
        generated the script, where ``get_scripts_dir`` only created an empty
        dir. Materialise the part file from the authoritative artifact
        (``script_artifact_id``), falling back to the persisted
        ``script_source``. Never assumes this pod wrote the dir; keeps the
        local file untouched when it is already present (fast path).
        """
        part_type = (job.shot_details or {}).get("part_type", job.part_type)
        target = self._storage.part_script_path(
            sdir, job.part_id, part_type=part_type, source_type=job.source_type
        )

        def _present() -> bool:
            return target.exists() and target.stat().st_size > 0

        if await asyncio.to_thread(_present):
            return  # this pod already holds the script -- no rebuild needed

        await asyncio.to_thread(lambda: target.parent.mkdir(parents=True, exist_ok=True))

        # Prefer the durable artifact -- the authoritative bytes uploaded on
        # completion, identical across replicas.
        if job.script_artifact_id is not None:
            try:
                project_id = UUID(job.batch_id or job.job_id)
                await self._artifact_client.download_to_path(
                    project_id=project_id,
                    artifact_id=job.script_artifact_id,
                    dest=target,
                )
                if await asyncio.to_thread(_present):
                    logger.info(
                        "Rebuilt script for job %s from artifact %s on this replica",
                        job.job_id,
                        job.script_artifact_id,
                    )
                    return
            except (httpx.HTTPError, OSError, ValueError) as exc:
                logger.warning(
                    "Artifact download failed for job %s (%s); using persisted script_source",
                    job.job_id,
                    exc,
                )

        # Fallback: the persisted script_source. validate_job already guaranteed
        # it is non-empty before reaching here.
        if job.script_source:
            await asyncio.to_thread(target.write_text, job.script_source, encoding="utf-8")
            logger.info("Rebuilt script for job %s from persisted script_source", job.job_id)
            return

        raise ValueError(
            f"Cannot rebuild working dir for job {job.job_id}: no durable script state"
        )

    async def _ensure_scaffold(self, storage_key: str, job_dir: Path, theme: dict | None) -> None:
        """Ensure scaffold exists once per shared storage directory."""
        scripts_dir = self._storage.get_scripts_dir(job_dir)
        marker = scripts_dir / "playwright.recording.config.ts"
        if marker.exists():
            return

        # get-or-create is synchronous (no await between get and set), so it is
        # race-free under asyncio's single-threaded scheduling.
        lock = self._scaffold_locks.get(storage_key)
        if lock is None:
            lock = asyncio.Lock()
            self._scaffold_locks[storage_key] = lock

        try:
            async with lock:
                if marker.exists():
                    return
                if self._scaffold is None:
                    raise RuntimeError("Scaffold service not configured")
                await self._scaffold.scaffold(job_dir, theme=theme)
                logger.info("Scaffold completed for storage key %s", storage_key)
        finally:
            # Evict so the dict does not grow unbounded across jobs. Late
            # arrivals already holding this same lock object still serialise
            # correctly; a brand-new arrival finds the marker file and returns
            # before it would ever need a lock.
            self._scaffold_locks.pop(storage_key, None)

    async def scaffold_job(
        self,
        job_id: str,
        *,
        tenant_id: str,
        theme: dict | None = None,
    ) -> dict:
        """Scaffold a TypeScript project for a job.

        The caller's ``tenant_id`` must match the job's tenant_id; a mismatch
        returns the same not-found shape as a missing job so the route maps
        both to 404 without leaking existence of another tenant's job.
        """
        if self._scaffold is None:
            return {
                "success": False,
                "scripts_dir": None,
                "error": "Scaffold service not configured",
            }

        job = await self._job_repo.get(job_id, tenant_id=tenant_id)
        if job is None:
            return {"success": False, "scripts_dir": None, "error": "Job not found"}

        try:
            job_dir = self._storage.get_job_dir(job_id)
            scripts_dir = await self._scaffold.scaffold(job_dir, theme=theme)
            return {"success": True, "scripts_dir": str(scripts_dir), "error": None}
        except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
            logger.error("Scaffold failed for job %s: %s", job_id, exc)
            return {"success": False, "scripts_dir": None, "error": str(exc)}

    async def cancel_job(self, job_id: str, *, tenant_id: str) -> bool:
        """Cancel a running job. Tenant-scoped — wrong tenant returns False."""
        job = await self._job_repo.get(job_id, tenant_id=tenant_id)
        if job is None:
            return False
        if job.status.value in ("completed", "failed", "cancelled"):
            return False
        await self._job_repo.mark_cancelled(job_id, tenant_id=tenant_id)
        return True

    async def create_batch(
        self,
        *,
        tenant_id: str,
        parts: list[dict],
        page_analysis: dict | None = None,
        storyline_context: str | None = None,
        theme: dict | None = None,
        message: str | None = None,
        mode: str | None = None,
        source_type: str | None = None,
        user_context: dict | None = None,
        auth_enabled: bool = False,
    ) -> tuple[str, dict[str, str]]:
        """Create batch + individual jobs. Return (batch_id, {part_id: job_id})."""
        batch_id = str(uuid.uuid7())
        await self._batch_repo.create(
            batch_id=batch_id,
            tenant_id=tenant_id,
            total_parts=len(parts),
        )

        job_ids: dict[str, str] = {}
        for part in parts:
            part_mode = (
                str(part.get("generation_mode") or mode)
                if (part.get("generation_mode") or mode)
                else None
            )
            part_source_type = (
                str(part.get("source_type") or source_type)
                if (part.get("source_type") or source_type)
                else None
            )
            part_auth_enabled = bool(part.get("auth_enabled", auth_enabled))
            job_id = await self.create_job(
                tenant_id=tenant_id,
                part=part,
                page_analysis=page_analysis,
                storyline_context=storyline_context,
                theme=theme,
                message=message,
                mode=part_mode,
                source_type=part_source_type,
                user_context=user_context,
                auth_enabled=part_auth_enabled,
                batch_id=batch_id,
            )
            job_ids[part.get("id", "unknown")] = job_id

        return batch_id, job_ids
