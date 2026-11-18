"""Job repository -- CRUD for script generation jobs.

All read/write methods that look up a row by ``job_id`` REQUIRE a ``tenant_id``
keyword argument. The tenant filter is enforced inside the SQL WHERE clause —
this is the security boundary for the service, not a route-level check. If a
caller forgets to pass ``tenant_id``, the call does not type-check; if a caller
passes the wrong tenant_id, the SELECT returns None / the UPDATE affects zero
rows (which the route layer maps to 404, same shape as a true miss).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from w2v_common.database.rls import bind_session_tenant

from src.domain.job import Job, JobStatus
from src.repository.models import ScriptJob
from src.tenant import require_tenant

logger = logging.getLogger(__name__)


class JobRepository:
    """Manages ScriptJob rows in the database."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        tenant_id: str,
        part_id: str,
        part_type: str = "browser_recording",
        page_url: str | None = None,
        page_type: str | None = None,
        shot_details: dict | None = None,
        page_analysis: dict | None = None,
        theme: dict | None = None,
        storyline_context: str | None = None,
        user_context: dict | None = None,
        mode: str | None = None,
        source_type: str | None = None,
        auth_enabled: bool = False,
        batch_id: str | None = None,
    ) -> Job:
        """Insert a new ScriptJob row in 'pending' status."""
        job_id = str(uuid.uuid7())
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            row = ScriptJob(
                job_id=job_id,
                tenant_id=tenant_id,
                part_id=part_id,
                part_type=part_type,
                page_url=page_url,
                page_type=page_type,
                shot_details=shot_details,
                page_analysis=page_analysis,
                theme=theme,
                storyline_context=storyline_context,
                user_context=user_context,
                mode=mode,
                source_type=source_type,
                auth_enabled=auth_enabled,
                batch_id=batch_id,
                status="pending",
            )
            session.add(row)
            await session.commit()
            logger.info("Created script job %s (part=%s)", job_id, part_id)
            return self._to_domain(row)

    async def get(self, job_id: str, *, tenant_id: str) -> Job | None:
        """Fetch a job by job_id, scoped to the caller's tenant."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            result = await session.execute(
                select(ScriptJob).where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                )
            )
            row = result.scalar_one_or_none()
            return self._to_domain(row) if row else None

    async def get_row(self, job_id: str, *, tenant_id: str) -> ScriptJob | None:
        """Fetch the raw ORM row (used by batch status), scoped to tenant."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            result = await session.execute(
                select(ScriptJob).where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                )
            )
            return result.scalar_one_or_none()

    async def mark_processing(self, job_id: str, *, tenant_id: str) -> None:
        """Transition a job to 'processing'."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            await session.execute(
                update(ScriptJob)
                .where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                )
                .values(status="processing", started_at=datetime.now(tz=UTC))
            )
            await session.commit()

    async def mark_completed(
        self,
        job_id: str,
        *,
        tenant_id: str,
        script_path: str | None = None,
        script_artifact_id: UUID | None = None,
        script_source: str | None = None,
        prompt_used: str | None = None,
        generation_time_seconds: float | None = None,
        provider_used: str | None = None,
        extraction_tokens_used: int | None = None,
    ) -> None:
        """Transition a job to 'completed' with generated script data.

        ``cancelled`` is TERMINAL and is never overwritten here. The
        cross-replica cancel is cooperative and best-effort: ``DELETE
        /api/scripts/jobs/{id}`` writes the row durably, but the task on
        another pod only stops at its next checkpoint -- and if Redis is
        degraded it may never observe the flag at all. Without this predicate a
        late completion flips the terminal state back to ``completed`` after
        the user cancelled and the API already answered ``cancelled``.
        """
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            await session.execute(
                update(ScriptJob)
                .where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                    ScriptJob.status != "cancelled",
                )
                .values(
                    status="completed",
                    script_path=script_path,
                    script_artifact_id=str(script_artifact_id) if script_artifact_id else None,
                    script_source=script_source,
                    prompt_used=prompt_used,
                    generation_time_seconds=generation_time_seconds,
                    provider_used=provider_used,
                    extraction_tokens_used=extraction_tokens_used,
                    completed_at=datetime.now(tz=UTC),
                )
            )
            await session.commit()

    async def mark_validated(
        self,
        job_id: str,
        *,
        tenant_id: str,
        compiles: bool = False,
        discoverable: bool = False,
        runs_headless: bool = False,
        accepted: bool = False,
        validation_errors: list[str] | None = None,
        improvement_attempts: int = 0,
        script_path: str | None = None,
        script_artifact_id: UUID | None = None,
        script_source: str | None = None,
    ) -> bool:
        """Store validation results for a job.

        ``script_path`` / ``script_artifact_id`` / ``script_source`` are written
        only when supplied (i.e. the auto-improve loop produced a new accepted
        script). Otherwise the generation-time values are left untouched, so a
        plain re-validation never blanks them.

        Returns ``True`` only when THIS call transitioned ``accepted`` from
        not-accepted to accepted. Validation is a re-runnable, cross-replica
        operation (the route's only guard is ``status == 'completed'``, and
        this method deliberately leaves the status there), so the caller must
        not bill ``scripts_accepted`` on every call — it bills on the
        transition. The prior value is read under ``FOR UPDATE`` in the same
        transaction as the write, so two replicas validating the same job
        cannot both observe "not yet accepted" and double-bill.
        """
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            previously_accepted = (
                await session.execute(
                    select(ScriptJob.accepted)
                    .where(
                        ScriptJob.job_id == job_id,
                        ScriptJob.tenant_id == tenant_id,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            # Chained ``.values()`` merges, so the optional script columns are
            # added only when supplied — a plain re-validation must not blank
            # the generation-time script_path / artifact id / source.
            stmt = (
                update(ScriptJob)
                .where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                )
                .values(
                    compiles=1 if compiles else 0,
                    discoverable=1 if discoverable else 0,
                    runs_headless=1 if runs_headless else 0,
                    accepted=1 if accepted else 0,
                    validation_errors=validation_errors,
                    improvement_attempts=improvement_attempts,
                )
            )
            if script_path is not None:
                stmt = stmt.values(script_path=script_path)
            if script_artifact_id is not None:
                stmt = stmt.values(script_artifact_id=script_artifact_id)
            if script_source is not None:
                stmt = stmt.values(script_source=script_source)
            await session.execute(stmt)
            await session.commit()
            return accepted and not previously_accepted

    async def mark_failed(self, job_id: str, error: str, *, tenant_id: str) -> None:
        """Transition a job to 'failed'.

        Same terminal-state rule as :meth:`mark_completed`: a job the user
        cancelled stays ``cancelled``. The orchestrator's broad ``except``
        reaches this method on the way out of an already-cancelled run.
        """
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            await session.execute(
                update(ScriptJob)
                .where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                    ScriptJob.status != "cancelled",
                )
                .values(
                    status="failed",
                    error_message=error,
                    completed_at=datetime.now(tz=UTC),
                )
            )
            await session.commit()

    async def mark_cancelled(self, job_id: str, *, tenant_id: str) -> None:
        """Transition a job to 'cancelled'."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            await session.execute(
                update(ScriptJob)
                .where(
                    ScriptJob.job_id == job_id,
                    ScriptJob.tenant_id == tenant_id,
                )
                .values(
                    status="cancelled",
                    cancelled_at=datetime.now(tz=UTC),
                )
            )
            await session.commit()

    @staticmethod
    def _to_domain(row: ScriptJob) -> Job:
        """Map an ORM row to a domain Job."""
        return Job(
            job_id=row.job_id,
            tenant_id=row.tenant_id,
            batch_id=row.batch_id,
            part_id=row.part_id,
            part_type=row.part_type or "browser_recording",
            status=JobStatus(row.status),
            shot_details=row.shot_details or {},
            page_analysis=row.page_analysis,
            theme=row.theme,
            storyline_context=row.storyline_context,
            user_context=row.user_context,
            mode=row.mode,
            source_type=row.source_type,
            auth_enabled=bool(row.auth_enabled),
            script_source=row.script_source,
            script_path=row.script_path,
            script_artifact_id=UUID(row.script_artifact_id) if row.script_artifact_id else None,
            prompt_used=row.prompt_used,
            generation_time_seconds=row.generation_time_seconds,
            error_message=row.error_message,
            created_at=row.created_at,
            started_at=row.started_at,
            completed_at=row.completed_at,
        )
