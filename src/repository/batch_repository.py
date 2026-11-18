"""Batch repository -- CRUD for script generation batches.

All read/write methods that look up a row by ``batch_id`` REQUIRE a
``tenant_id`` keyword argument. Both ``ScriptBatch`` and the joined
``ScriptJob`` rows are filtered on ``tenant_id`` so a wrong-tenant lookup
returns None and an unauthorised update is a zero-row no-op.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from w2v_common.database.rls import bind_session_tenant

from src.repository.models import ScriptBatch, ScriptJob
from src.tenant import require_tenant

logger = logging.getLogger(__name__)


class BatchRepository:
    """Manages ScriptBatch rows in the database."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create(
        self,
        *,
        batch_id: str,
        tenant_id: str,
        total_parts: int,
    ) -> None:
        """Create a new ScriptBatch row."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            batch = ScriptBatch(
                batch_id=batch_id,
                tenant_id=tenant_id,
                total_parts=total_parts,
                status="pending",
            )
            session.add(batch)
            await session.commit()
            logger.info("Created script batch %s (%d parts)", batch_id, total_parts)

    async def get_status(self, batch_id: str, *, tenant_id: str) -> dict | None:
        """Return batch status dict with per-job details, or None.

        Both the batch and its constituent jobs are filtered on ``tenant_id``;
        a wrong-tenant lookup returns None even if the batch_id exists.
        """
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            result = await session.execute(
                select(ScriptBatch).where(
                    ScriptBatch.batch_id == batch_id,
                    ScriptBatch.tenant_id == tenant_id,
                )
            )
            batch = result.scalar_one_or_none()
            if batch is None:
                return None

            jobs_result = await session.execute(
                select(ScriptJob).where(
                    ScriptJob.batch_id == batch_id,
                    ScriptJob.tenant_id == tenant_id,
                )
            )
            jobs = list(jobs_result.scalars().all())

            completed = sum(1 for j in jobs if j.status == "completed")
            failed = sum(1 for j in jobs if j.status == "failed")
            total = batch.total_parts

            if completed + failed >= total:
                status = "completed" if failed == 0 else "partial"
            elif completed > 0 or any(j.status == "processing" for j in jobs):
                status = "in_progress"
            else:
                status = "pending"

            return {
                "batch_id": batch.batch_id,
                "total": total,
                "completed": completed,
                "failed": failed,
                "status": status,
                "jobs": jobs,
            }

    async def update_status(self, batch_id: str, *, tenant_id: str) -> None:
        """Recompute batch status from job statuses."""
        async with self._session_factory() as session:
            await bind_session_tenant(session, require_tenant(tenant_id))
            result = await session.execute(
                select(ScriptBatch).where(
                    ScriptBatch.batch_id == batch_id,
                    ScriptBatch.tenant_id == tenant_id,
                )
            )
            batch = result.scalar_one_or_none()
            if batch is None:
                return

            jobs_result = await session.execute(
                select(ScriptJob).where(
                    ScriptJob.batch_id == batch_id,
                    ScriptJob.tenant_id == tenant_id,
                )
            )
            jobs = list(jobs_result.scalars().all())

            completed = sum(1 for j in jobs if j.status == "completed")
            failed = sum(1 for j in jobs if j.status == "failed")

            if completed + failed >= batch.total_parts:
                new_status = "completed" if failed == 0 else "partial"
            elif completed > 0 or any(j.status == "processing" for j in jobs):
                new_status = "in_progress"
            else:
                new_status = "pending"

            await session.execute(
                update(ScriptBatch)
                .where(
                    ScriptBatch.batch_id == batch_id,
                    ScriptBatch.tenant_id == tenant_id,
                )
                .values(
                    status=new_status,
                    completed_at=datetime.now(tz=UTC)
                    if new_status in ("completed", "partial")
                    else None,
                )
            )
            await session.commit()
