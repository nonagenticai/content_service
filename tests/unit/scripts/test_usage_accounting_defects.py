"""Usage/billing accounting defects in ScriptOrchestrator.

Two independent money-surface bugs:

1. ``validate_job`` bills ``scripts_accepted=1`` on EVERY call. The validate
   route's only guard is ``status == "completed"`` and ``mark_validated`` does
   NOT move the status off ``completed``, so re-validating one script (a
   supported, cross-replica-safe operation) bills it as N accepted scripts.
2. ``process_job`` records usage INSIDE the try that owns ``mark_failed``. A
   failure of the (explicitly non-critical) accounting write therefore
   downgrades an already-completed, artifact-uploaded job to ``failed``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import select

from src.database.models import ScriptUsage
from src.domain.job import Job, JobStatus
from src.domain.script import GeneratedScript, ValidationResult
from src.repository.job_repository import JobRepository
from src.repository.usage_repository import UsageRepository
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.storage_service import StorageService
from tests.conftest import TENANT_A, TENANT_A_STR, TENANT_B_STR

_VALID_JOB_ID = "00000000-0000-0000-0000-000000000001"


async def _billed_accepted(session, tenant_id: str) -> int:
    """Sum of ``scripts_accepted`` actually persisted for *tenant_id*."""
    rows = (
        await session.execute(
            select(ScriptUsage.scripts_accepted).where(ScriptUsage.tenant_id == tenant_id)
        )
    ).scalars()
    return sum(rows)


def _make_orchestrator(**overrides: Any) -> ScriptOrchestrator:
    defaults: dict[str, Any] = {
        "job_repo": AsyncMock(),
        "batch_repo": AsyncMock(),
        "usage_repo": AsyncMock(),
        "generation_service": AsyncMock(),
        "validation_service": AsyncMock(),
        "improvement_service": AsyncMock(),
        "scaffold_service": AsyncMock(),
        "storage": MagicMock(),
    }
    defaults.update(overrides)
    return ScriptOrchestrator(**defaults)


def _make_job(**overrides: Any) -> Job:
    defaults: dict[str, Any] = {
        "job_id": _VALID_JOB_ID,
        "tenant_id": TENANT_A,
        "part_id": "p1",
        "part_type": "browser_recording",
        "status": JobStatus.PENDING,
        "shot_details": {"id": "p1", "part_type": "browser_recording"},
    }
    defaults.update(overrides)
    return Job(**defaults)


class TestRevalidateDoubleBills:
    """Real repositories + real postgres — the billed row is the assertion."""

    async def test_second_validate_of_same_script_must_not_bill_again(
        self, tmp_path, db_session_factory, db_session
    ):
        """One accepted script must be billed as ONE accepted script.

        The validate endpoint gates only on ``status == "completed"``, and
        ``mark_validated`` leaves the status at ``completed`` — so a client
        (or the orchestrator's own retry, or a second replica) can POST
        ``/api/scripts/jobs/{id}/validate`` again and the tenant is charged a
        second ``scripts_accepted`` for the same artifact.
        """
        tenant = TENANT_B_STR
        job_repo = JobRepository(db_session_factory)
        usage_repo = UsageRepository(db_session_factory)

        created = await job_repo.create(
            tenant_id=tenant,
            part_id="p1",
            part_type="browser_recording",
            shot_details={"id": "p1", "part_type": "browser_recording"},
        )
        job_id = created.job_id
        await job_repo.mark_completed(
            job_id, tenant_id=tenant, script_source="code", script_path="p1.ts"
        )

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))
        seeded = tmp_path / job_id / "scripts" / "parts" / "p1.demo.spec.ts"
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text("code")

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            usage_repo=usage_repo,
        )

        first = await orch.validate_job(job_id, tenant)

        # PRECONDITION: the first validate really did accept and really did bill.
        assert first.accepted is True
        assert await _billed_accepted(db_session, tenant) == 1, (
            "precondition: first validate bills exactly one accepted script"
        )

        second = await orch.validate_job(job_id, tenant)
        assert second.accepted is True

        billed = await _billed_accepted(db_session, tenant)
        assert billed == 1, (
            f"one script validated twice was billed as {billed} accepted scripts (double-billing)"
        )


class TestUsageWriteFailureDowngradesCompletedJob:
    async def test_usage_record_failure_must_not_fail_a_completed_job(self, tmp_path):
        """Token accounting is non-critical; it must not un-complete a paid job.

        ``process_job`` calls ``usage_repo.record`` inside the try block whose
        ``except`` calls ``mark_failed``. A transient DB error on the accounting
        write therefore flips a job that already has a stored script AND an
        uploaded artifact to ``failed`` — discarding paid LLM output and
        re-charging the tenant on the retry.
        """
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())

        gen_svc = AsyncMock()
        gen_svc.generate = AsyncMock(
            return_value=GeneratedScript(
                script_source="code",
                prompt_used="prompt",
                generation_time_seconds=1.0,
                llm_tokens_used=100,
                provider_used="test",
            )
        )

        script_path = tmp_path / "p1.ts"
        script_path.write_text("// generated script")

        storage = MagicMock()
        storage.get_job_dir.return_value = tmp_path
        storage.get_scripts_dir.return_value = tmp_path
        storage.store_script = AsyncMock(return_value=str(script_path))
        storage.store_prompt = AsyncMock(return_value=str(tmp_path / ".prompt.md"))

        artifact_client = AsyncMock()
        artifact_client.upload_bytes = AsyncMock(return_value=MagicMock(artifact_id=uuid4()))

        usage_repo = AsyncMock()
        usage_repo.record = AsyncMock(side_effect=OSError("usage DB write failed"))

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            usage_repo=usage_repo,
            scaffold_service=AsyncMock(),
            artifact_client=artifact_client,
        )

        try:
            await orch.process_job(_VALID_JOB_ID, TENANT_A_STR)
        except OSError:
            pytest.fail("a failed usage write must not propagate out of process_job")

        # PRECONDITION: the job really did complete before the accounting write.
        job_repo.mark_completed.assert_awaited_once()
        assert usage_repo.record.await_count >= 1, "precondition: accounting write was attempted"

        assert job_repo.mark_failed.await_count == 0, (
            "a failed usage/accounting write downgraded an already-completed, "
            "artifact-uploaded job to 'failed'"
        )
