"""ScriptOrchestrator must upload each generated script to artifact_service
and stamp the returned artifact_id on the per-job record (domain Job /
mark_completed call).

The real orchestrator has no process_part() helper — scripts are stored
inside process_job().  This test drives process_job() end-to-end with all
deps mocked, verifying that:

  1. upload_bytes is called exactly once after the script is written to disk.
  2. The upload kwargs match the contract: project_id == job_id (as UUID),
     step == part_id, filename ends with ".demo.spec.ts", kind == "other",
     tags include "w2v-script".
  3. mark_completed is called with script_artifact_id == the UUID returned
     by the fake client.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from w2v_common.clients import ArtifactUploadResult

from src.domain.job import Job, JobStatus
from src.domain.script import GeneratedScript
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.storage_service import StorageService

# tenant_id is a real uuid (fleet ruling); Job.tenant_id is typed UUID.
TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_artifact_client(returned_id: UUID) -> AsyncMock:
    """Return an AsyncMock that mimics ArtifactClient.upload_bytes."""
    client = AsyncMock()
    client.upload_bytes = AsyncMock(
        return_value=ArtifactUploadResult(
            artifact_id=returned_id,
            version=1,
            storage_key="projects/test/step/part-001/x/v1/part-001.demo.spec.ts",
            size=42,
            sha256="0" * 64,
        )
    )
    return client


def _make_job(job_id: str, part_id: str = "part-001") -> Job:
    return Job(
        job_id=job_id,
        tenant_id=TEST_TENANT_ID,
        part_id=part_id,
        part_type="browser_recording",
        status=JobStatus.PENDING,
        shot_details={"id": part_id, "part_type": "browser_recording"},
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_process_job_uploads_script_and_stamps_artifact_id(tmp_path: Path) -> None:
    """After scaffold writes the .ts to disk, upload_bytes must be called and
    the returned artifact_id forwarded to mark_completed."""
    job_id = str(uuid4())
    part_id = "part-001"
    artifact_id = uuid4()

    job = _make_job(job_id, part_id)

    job_repo = AsyncMock()
    job_repo.get = AsyncMock(return_value=job)

    script_source = "import { test } from '@playwright/test';\ntest('demo', async () => {});"
    generated = GeneratedScript(
        script_source=script_source,
        prompt_used=None,
        generation_time_seconds=1.0,
        provider_used="test-provider",
        llm_tokens_used=100,
    )
    generation_service = AsyncMock()
    generation_service.generate = AsyncMock(return_value=generated)

    storage = StorageService(local_path=str(tmp_path))
    fake_client = _fake_artifact_client(artifact_id)

    orch = ScriptOrchestrator(
        job_repo=job_repo,
        batch_repo=AsyncMock(),
        usage_repo=AsyncMock(),
        generation_service=generation_service,
        validation_service=AsyncMock(),
        improvement_service=AsyncMock(),
        scaffold_service=None,
        storage=storage,
        artifact_client=fake_client,
    )

    await orch.process_job(job_id, "tenant-x")

    # upload_bytes called exactly once
    fake_client.upload_bytes.assert_awaited_once()
    call_kwargs = fake_client.upload_bytes.await_args.kwargs

    # project_id == job_id as UUID
    assert call_kwargs["project_id"] == UUID(job_id)

    # step == part_id
    assert call_kwargs["step"] == part_id

    # filename ends with .demo.spec.ts
    assert call_kwargs["filename"].endswith(".demo.spec.ts"), (
        f"Expected filename ending with .demo.spec.ts, got {call_kwargs['filename']!r}"
    )

    # kind == "other"
    assert call_kwargs["kind"] == "other"

    # content_type must be text/plain (scripts are UTF-8 TypeScript source)
    assert call_kwargs["content_type"] == "text/plain"

    # tags include "w2v-script" and the per-pipeline tag
    assert "w2v-script" in (call_kwargs["tags"] or []), (
        f"Expected 'w2v-script' in tags, got {call_kwargs['tags']!r}"
    )
    assert f"w2v-pipeline-{job_id}" in (call_kwargs["tags"] or []), (
        f"Expected 'w2v-pipeline-{job_id}' in tags, got {call_kwargs['tags']!r}"
    )

    # mark_completed called with script_artifact_id == the returned UUID
    job_repo.mark_completed.assert_awaited_once()
    completed_kwargs = job_repo.mark_completed.await_args.kwargs
    assert completed_kwargs.get("script_artifact_id") == artifact_id, (
        f"script_artifact_id not forwarded to mark_completed: {completed_kwargs!r}"
    )


async def test_process_job_upload_failure_marks_failed(tmp_path: Path) -> None:
    """Artifact upload is required, not best-effort: failure must mark the job failed.

    A missing script_artifact_id silently breaks the downstream record stage
    in vps (no scripts_dir, no artifact_id). Failing loudly here surfaces the
    real cause (artifact_service connectivity) instead of letting it manifest
    later as a confusing record-stage error.
    """
    job_id = str(uuid4())
    part_id = "part-001"

    job = _make_job(job_id, part_id)

    job_repo = AsyncMock()
    job_repo.get = AsyncMock(return_value=job)

    script_source = "import { test } from '@playwright/test';\ntest('demo', async () => {});"
    generated = GeneratedScript(
        script_source=script_source,
        prompt_used=None,
        generation_time_seconds=1.0,
        provider_used="test-provider",
        llm_tokens_used=100,
    )
    generation_service = AsyncMock()
    generation_service.generate = AsyncMock(return_value=generated)

    storage = StorageService(local_path=str(tmp_path))

    failing_client = AsyncMock()
    failing_client.upload_bytes = AsyncMock(side_effect=httpx.HTTPError("network error"))

    orch = ScriptOrchestrator(
        job_repo=job_repo,
        batch_repo=AsyncMock(),
        usage_repo=AsyncMock(),
        generation_service=generation_service,
        validation_service=AsyncMock(),
        improvement_service=AsyncMock(),
        scaffold_service=None,
        storage=storage,
        artifact_client=failing_client,
    )

    with pytest.raises(httpx.HTTPError):
        await orch.process_job(job_id, "tenant-x")

    job_repo.mark_failed.assert_awaited_once()
    job_repo.mark_completed.assert_not_awaited()
