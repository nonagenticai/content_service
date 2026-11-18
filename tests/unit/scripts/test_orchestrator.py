"""Tests for services.scripts.orchestrator.ScriptOrchestrator."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.job import Job, JobStatus
from src.domain.script import GeneratedScript, ValidationResult
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.storage_service import StorageService
from tests.conftest import TENANT_A, TENANT_C

# process_job() now converts job_id to UUID before the best-effort upload try/except
# so that malformed IDs fail loudly.  All process_job() call-sites in tests must
# use a valid UUID string.
_VALID_JOB_ID = "00000000-0000-0000-0000-000000000001"


def _make_orchestrator(**overrides):
    """Create a ScriptOrchestrator with mocked dependencies."""
    defaults = {
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
        # Must be a valid UUID — process_job() converts job_id to UUID before
        # the best-effort artifact upload so malformed IDs fail loudly.
        "job_id": _VALID_JOB_ID,
        "tenant_id": TENANT_A,
        "part_id": "p1",
        "part_type": "browser_recording",
        "status": JobStatus.PENDING,
        "shot_details": {"id": "p1", "part_type": "browser_recording"},
    }
    defaults.update(overrides)
    return Job(**defaults)


class TestOrchestratorProperties:
    def test_job_repo(self):
        job_repo = AsyncMock()
        orch = _make_orchestrator(job_repo=job_repo)
        assert orch.job_repo is job_repo

    def test_batch_repo(self):
        batch_repo = AsyncMock()
        orch = _make_orchestrator(batch_repo=batch_repo)
        assert orch.batch_repo is batch_repo


class TestCreateJob:
    async def test_creates_job(self):
        job_repo = AsyncMock()
        job_repo.create = AsyncMock(return_value=_make_job(job_id="new-job"))
        orch = _make_orchestrator(job_repo=job_repo)

        job_id = await orch.create_job(
            tenant_id=TENANT_A,
            part={"id": "p1", "part_type": "browser_recording"},
        )
        assert job_id == "new-job"
        job_repo.create.assert_awaited_once()

    async def test_passes_message_to_shot_details(self):
        job_repo = AsyncMock()
        job_repo.create = AsyncMock(return_value=_make_job())
        orch = _make_orchestrator(job_repo=job_repo)

        await orch.create_job(
            tenant_id=TENANT_A,
            part={"id": "p1"},
            message="custom message",
        )
        call_kwargs = job_repo.create.call_args.kwargs
        assert call_kwargs["shot_details"]["user_message"] == "custom message"


class TestProcessJob:
    async def test_full_pipeline(self, tmp_path):
        from uuid import uuid4 as _uuid4

        job_repo = AsyncMock()
        job = _make_job()
        job_repo.get = AsyncMock(return_value=job)

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

        # Real on-disk script so read_bytes() succeeds.
        script_path = tmp_path / "p1.ts"
        script_path.write_text("// generated script")

        storage = MagicMock()
        storage.get_job_dir.return_value = tmp_path
        storage.get_scripts_dir.return_value = tmp_path
        storage.store_script = AsyncMock(return_value=str(script_path))
        storage.store_prompt = AsyncMock(return_value=str(tmp_path / ".prompt.md"))

        # Artifact upload must succeed for process_job to mark completed; any
        # upload failure now propagates (no longer best-effort).
        fake_artifact_id = _uuid4()
        artifact_client = AsyncMock()
        artifact_client.upload_bytes = AsyncMock(
            return_value=MagicMock(artifact_id=fake_artifact_id)
        )

        usage_repo = AsyncMock()

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            usage_repo=usage_repo,
            scaffold_service=AsyncMock(),
            artifact_client=artifact_client,
        )

        await orch.process_job(_VALID_JOB_ID, TENANT_A)
        job_repo.mark_processing.assert_awaited_once_with(_VALID_JOB_ID, tenant_id=TENANT_A)
        # get() is tenant-scoped on every call.
        job_repo.get.assert_awaited_with(_VALID_JOB_ID, tenant_id=TENANT_A)
        artifact_client.upload_bytes.assert_awaited_once()
        job_repo.mark_completed.assert_awaited_once()
        # script_artifact_id from upload must be persisted on the job
        assert job_repo.mark_completed.call_args.kwargs["script_artifact_id"] == fake_artifact_id
        assert job_repo.mark_completed.call_args.kwargs["tenant_id"] == TENANT_A
        usage_repo.record.assert_awaited_once()

    async def test_job_not_found_raises(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)
        orch = _make_orchestrator(job_repo=job_repo)

        with pytest.raises(ValueError, match="Job not found"):
            await orch.process_job("nonexistent", TENANT_A)

    async def test_title_card_skips_scaffold_and_llm(self, tmp_path):
        from uuid import uuid4 as _uuid4

        job = _make_job(
            part_type="title_card",
            shot_details={"id": "p1", "part_type": "title_card", "title": "T"},
        )
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        gen_svc = MagicMock()
        gen_svc.generate_title_card.return_value = GeneratedScript(
            script_source="title code",
            provider_used="hardcoded",
        )

        script_path = tmp_path / "p1.ts"
        script_path.write_text("// title card")

        storage = MagicMock()
        storage.get_job_dir.return_value = tmp_path
        storage.store_script = AsyncMock(return_value=str(script_path))
        storage.store_prompt = AsyncMock()

        artifact_client = AsyncMock()
        artifact_client.upload_bytes = AsyncMock(return_value=MagicMock(artifact_id=_uuid4()))

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            artifact_client=artifact_client,
        )

        await orch.process_job(_VALID_JOB_ID, TENANT_A)
        gen_svc.generate_title_card.assert_called_once()

    async def test_failure_marks_failed(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())
        job_repo.mark_processing = AsyncMock()

        gen_svc = AsyncMock()
        gen_svc.generate = AsyncMock(side_effect=RuntimeError("LLM fail"))

        storage = MagicMock()
        storage.get_job_dir.return_value = Path("/tmp/j")
        storage.get_scripts_dir.return_value = Path("/tmp/j/scripts")

        usage_repo = AsyncMock()

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            usage_repo=usage_repo,
            scaffold_service=AsyncMock(),
        )

        with pytest.raises(RuntimeError):
            await orch.process_job(_VALID_JOB_ID, TENANT_A)
        job_repo.mark_failed.assert_awaited_once()
        assert job_repo.mark_failed.call_args.kwargs == {"tenant_id": TENANT_A}
        usage_repo.record.assert_awaited()

    async def test_non_tuple_error_still_marks_failed_not_stranded(self):
        # httpx.InvalidURL (a bad discovery URL) is NOT a subclass of httpx.HTTPError,
        # and TypeError/KeyError from response parsing are not in the old except tuple.
        # The httpx-wedge contract: a job must NEVER strand in 'processing' — any
        # error must mark it failed. (CLAUDE.md: "catch broadly and fail the job".)
        import httpx

        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())
        job_repo.mark_processing = AsyncMock()
        gen_svc = AsyncMock()
        gen_svc.generate = AsyncMock(side_effect=httpx.InvalidURL("bad worker url"))
        storage = MagicMock()
        storage.get_job_dir.return_value = Path("/tmp/j")
        storage.get_scripts_dir.return_value = Path("/tmp/j/scripts")
        usage_repo = AsyncMock()
        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            usage_repo=usage_repo,
            scaffold_service=AsyncMock(),
        )
        with pytest.raises(httpx.InvalidURL):  # still re-raised (the wrapper logs)
            await orch.process_job(_VALID_JOB_ID, TENANT_A)
        job_repo.mark_failed.assert_awaited_once()  # NOT stranded in 'processing'
        usage_repo.record.assert_awaited()

    async def test_mark_failed_write_error_does_not_mask_original_exception(self):
        # mark_failed() itself does a raw session.execute + commit and can
        # fail (DB blip, RLS/connection error, ...). Guard it the same way
        # _record_usage_best_effort guards the usage write: the ORIGINAL
        # generation exception must still be what propagates, not the
        # bookkeeping failure from trying to record it.
        class _GenerationBoomError(RuntimeError):
            pass

        class _MarkFailedBoomError(RuntimeError):
            pass

        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())
        job_repo.mark_processing = AsyncMock()
        job_repo.mark_failed = AsyncMock(
            side_effect=_MarkFailedBoomError("mark_failed write failed")
        )

        gen_svc = AsyncMock()
        gen_svc.generate = AsyncMock(
            side_effect=_GenerationBoomError("original generation failure")
        )

        storage = MagicMock()
        storage.get_job_dir.return_value = Path("/tmp/j")
        storage.get_scripts_dir.return_value = Path("/tmp/j/scripts")

        usage_repo = AsyncMock()

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            storage=storage,
            usage_repo=usage_repo,
            scaffold_service=AsyncMock(),
        )

        with pytest.raises(_GenerationBoomError, match="original generation failure"):
            await orch.process_job(_VALID_JOB_ID, TENANT_A)

        job_repo.mark_failed.assert_awaited_once()
        # Best-effort usage recording still runs even though mark_failed blew up.
        usage_repo.record.assert_awaited()


class TestValidateJob:
    async def test_job_not_found(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)
        orch = _make_orchestrator(job_repo=job_repo)
        with pytest.raises(ValueError, match="not found"):
            await orch.validate_job("j1", TENANT_A)

    async def test_no_script_source(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job(script_source=None))
        orch = _make_orchestrator(job_repo=job_repo)
        with pytest.raises(ValueError, match="no generated script"):
            await orch.validate_job("j1", TENANT_A)

    async def test_validation_accepted(self, tmp_path):
        job = _make_job(script_source="code")
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        # Real storage rooted at a fresh dir; pre-seed this pod's working copy
        # so validate uses the local fast path (no rebuild needed).
        storage = StorageService(local_path=str(tmp_path))
        seeded = tmp_path / "j1" / "scripts" / "parts" / "p1.demo.spec.ts"
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text("code")

        usage_repo = AsyncMock()

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            usage_repo=usage_repo,
        )

        result = await orch.validate_job("j1", TENANT_A)
        assert result.accepted is True
        usage_repo.record.assert_awaited_once()

    async def test_ignores_malicious_scripts_dir_override(self, tmp_path):
        """A caller-supplied scripts_dir must NOT set the validation cwd."""
        job = _make_job(script_source="code")
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))
        # The storage-derived working dir for job "j1" (storage_key == job_id).
        safe_dir = tmp_path / "j1" / "scripts"
        seeded = safe_dir / "parts" / "p1.demo.spec.ts"
        seeded.parent.mkdir(parents=True, exist_ok=True)
        seeded.write_text("code")

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            usage_repo=AsyncMock(),
        )

        await orch.validate_job("j1", TENANT_A, scripts_dir="/etc/passwd")

        # Validation must run against the storage-derived dir, never the override.
        called_dir = validation_svc.validate.call_args.args[0]
        assert called_dir == safe_dir
        assert Path("/etc/passwd") != called_dir


class TestScaffoldJob:
    async def test_no_scaffold_service(self):
        orch = _make_orchestrator(scaffold_service=None)
        result = await orch.scaffold_job("j1", tenant_id=TENANT_A)
        assert result["success"] is False
        assert "not configured" in result["error"]

    async def test_scaffold_success(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())

        scaffold_svc = AsyncMock()
        scaffold_svc.scaffold = AsyncMock(return_value=Path("/tmp/j/scripts"))

        storage = MagicMock()
        storage.get_job_dir.return_value = Path("/tmp/j")

        orch = _make_orchestrator(job_repo=job_repo, scaffold_service=scaffold_svc, storage=storage)
        result = await orch.scaffold_job("j1", tenant_id=TENANT_A)
        assert result["success"] is True
        job_repo.get.assert_awaited_with("j1", tenant_id=TENANT_A)

    async def test_scaffold_failure(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job())

        scaffold_svc = AsyncMock()
        scaffold_svc.scaffold = AsyncMock(side_effect=RuntimeError("npm fail"))

        storage = MagicMock()
        storage.get_job_dir.return_value = Path("/tmp/j")

        orch = _make_orchestrator(job_repo=job_repo, scaffold_service=scaffold_svc, storage=storage)
        result = await orch.scaffold_job("j1", tenant_id=TENANT_A)
        assert result["success"] is False
        assert "npm fail" in result["error"]

    async def test_scaffold_wrong_tenant_returns_not_found(self):
        """Cross-tenant scaffold returns the same not-found shape as a missing job."""
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)  # repo returns None for wrong tenant

        scaffold_svc = AsyncMock()
        storage = MagicMock()

        orch = _make_orchestrator(job_repo=job_repo, scaffold_service=scaffold_svc, storage=storage)
        result = await orch.scaffold_job("j1", tenant_id=TENANT_C)
        assert result["success"] is False
        assert result["error"] == "Job not found"
        scaffold_svc.scaffold.assert_not_awaited()


class TestCancelJob:
    async def test_job_not_found(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)
        orch = _make_orchestrator(job_repo=job_repo)
        assert await orch.cancel_job("j1", tenant_id=TENANT_A) is False

    async def test_already_completed(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job(status=JobStatus.COMPLETED))
        orch = _make_orchestrator(job_repo=job_repo)
        assert await orch.cancel_job("j1", tenant_id=TENANT_A) is False

    async def test_cancel_pending(self):
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=_make_job(status=JobStatus.PENDING))
        orch = _make_orchestrator(job_repo=job_repo)
        assert await orch.cancel_job("j1", tenant_id=TENANT_A) is True
        job_repo.mark_cancelled.assert_awaited_once_with("j1", tenant_id=TENANT_A)

    async def test_cancel_wrong_tenant_returns_false(self):
        """Repo returns None for wrong-tenant lookup; cancel must be a no-op."""
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)
        orch = _make_orchestrator(job_repo=job_repo)
        assert await orch.cancel_job("j1", tenant_id=TENANT_C) is False
        job_repo.mark_cancelled.assert_not_awaited()


class TestCreateBatch:
    async def test_creates_batch_and_jobs(self):
        job_repo = AsyncMock()
        job_counter = [0]

        async def mock_create(**kwargs):
            job_counter[0] += 1
            return _make_job(job_id=f"job-{job_counter[0]}")

        job_repo.create = mock_create
        batch_repo = AsyncMock()

        orch = _make_orchestrator(job_repo=job_repo, batch_repo=batch_repo)
        batch_id, job_ids = await orch.create_batch(
            tenant_id=TENANT_A,
            parts=[
                {"id": "p1", "part_type": "browser_recording"},
                {"id": "p2", "part_type": "title_card"},
            ],
        )
        assert batch_id is not None
        assert len(job_ids) == 2
        batch_repo.create.assert_awaited_once()


class TestEnsureScaffold:
    async def test_skips_if_marker_exists(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "playwright.recording.config.ts").write_text("cfg")

        storage = MagicMock()
        storage.get_scripts_dir.return_value = scripts_dir

        scaffold_svc = AsyncMock()
        orch = _make_orchestrator(scaffold_service=scaffold_svc, storage=storage)
        await orch._ensure_scaffold("key1", tmp_path, None)
        scaffold_svc.scaffold.assert_not_awaited()
