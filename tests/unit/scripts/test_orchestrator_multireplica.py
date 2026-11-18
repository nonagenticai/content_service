"""Multi-replica-safety tests for ScriptOrchestrator.

Two failure modes at >1 replica are covered:

1. **Pod-local disk read across requests.** A part generated on pod A writes
   its script to pod A's ``/app/data``; a ``validate``/``improve`` request for
   the same part can land on pod B with an empty disk. The orchestrator must
   rebuild the working copy from durable state (artifact_service, else the
   persisted ``script_source``) instead of validating a phantom empty file.

2. **Pod-affine cancel.** ``TaskManager.cancel`` only reaches the local pod's
   in-process task dict. A cross-replica cancel flag (Redis) set by pod B must
   stop a running generation task on pod A.

These use a REAL :class:`StorageService` rooted at ``tmp_path`` (a fresh,
empty "pod disk") so the rebuild path logic is exercised for real; the DB
repository is mocked (no SQLite — DB-backed coverage lives in the
``*_real_db`` suites).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.domain.job import Job, JobStatus
from src.domain.script import ValidationResult
from src.infra.cancel_flags import CancelFlags
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.storage_service import StorageService
from tests.conftest import TENANT_A, TENANT_A_STR, TENANT_C_STR

_VALID_JOB_ID = "00000000-0000-0000-0000-000000000001"


class _FakeRedis:
    """Minimal in-memory stand-in for the bespoke RedisClient used by CancelFlags."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: object, ttl: int | None = None) -> bool:
        self._store[key] = str(value)
        return True

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None


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
        "status": JobStatus.COMPLETED,
        "shot_details": {"id": "p1", "part_type": "browser_recording"},
        "script_source": "// durable source\nimport { test } from '@playwright/test';",
    }
    defaults.update(overrides)
    return Job(**defaults)


class TestValidateRebuildsFromDurableState:
    async def test_fresh_pod_rebuilds_from_artifact_download(self, tmp_path: Path) -> None:
        """(a) Fresh pod: only DB source + a mocked artifact download -> rebuild + validate."""
        artifact_id = uuid4()
        job = _make_job(script_artifact_id=artifact_id)
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        async def _fake_download(*, project_id: Any, artifact_id: Any, dest: Path) -> Path:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text("// downloaded from artifact_service", encoding="utf-8")
            return dest

        artifact_client = AsyncMock()
        artifact_client.download_to_path = AsyncMock(side_effect=_fake_download)

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))  # empty "pod disk"

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            artifact_client=artifact_client,
            usage_repo=AsyncMock(),
        )

        result = await orch.validate_job(_VALID_JOB_ID, TENANT_A_STR)

        assert result.accepted is True
        # The artifact download must have been used (preferred over source).
        artifact_client.download_to_path.assert_awaited_once()
        assert artifact_client.download_to_path.call_args.kwargs["artifact_id"] == artifact_id
        # The rebuilt file must exist where validation reads it.
        rebuilt = tmp_path / _VALID_JOB_ID / "scripts" / "parts" / "p1.demo.spec.ts"
        assert rebuilt.exists()
        assert "downloaded from artifact_service" in rebuilt.read_text(encoding="utf-8")

    async def test_fresh_pod_falls_back_to_script_source(self, tmp_path: Path) -> None:
        """(a) Fresh pod with no artifact id -> rebuild from persisted script_source."""
        job = _make_job(script_artifact_id=None)
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        artifact_client = AsyncMock()

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            artifact_client=artifact_client,
            usage_repo=AsyncMock(),
        )

        result = await orch.validate_job(_VALID_JOB_ID, TENANT_A_STR)

        assert result.accepted is True
        artifact_client.download_to_path.assert_not_awaited()
        rebuilt = tmp_path / _VALID_JOB_ID / "scripts" / "parts" / "p1.demo.spec.ts"
        assert rebuilt.exists()
        assert "durable source" in rebuilt.read_text(encoding="utf-8")

    async def test_artifact_download_failure_falls_back_to_source(self, tmp_path: Path) -> None:
        """Artifact download error must not fail the request -- fall back to source."""
        import httpx

        job = _make_job(script_artifact_id=uuid4())
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        artifact_client = AsyncMock()
        artifact_client.download_to_path = AsyncMock(
            side_effect=httpx.ConnectError("artifact_service down")
        )

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            artifact_client=artifact_client,
            usage_repo=AsyncMock(),
        )

        result = await orch.validate_job(_VALID_JOB_ID, TENANT_A_STR)

        assert result.accepted is True
        artifact_client.download_to_path.assert_awaited_once()
        rebuilt = tmp_path / _VALID_JOB_ID / "scripts" / "parts" / "p1.demo.spec.ts"
        assert rebuilt.exists()
        assert "durable source" in rebuilt.read_text(encoding="utf-8")

    async def test_campaign_pack_part_written_elsewhere_is_rebuilt(self, tmp_path: Path) -> None:
        """(b) Campaign-pack part generated on another pod (shared batch dir) is rebuilt."""
        batch_id = "11111111-1111-1111-1111-111111111111"
        job = _make_job(
            part_id="p2",
            batch_id=batch_id,
            script_artifact_id=None,
            script_source="// campaign variant p2 source\nimport { test } from '@playwright/test';",
            shot_details={"id": "p2", "part_type": "browser_recording"},
        )
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            artifact_client=AsyncMock(),
            usage_repo=AsyncMock(),
        )

        result = await orch.validate_job("p2-job", TENANT_A_STR)

        assert result.accepted is True
        # storage_key is the BATCH id for campaign parts.
        rebuilt = tmp_path / batch_id / "scripts" / "parts" / "p2.demo.spec.ts"
        assert rebuilt.exists()
        assert "campaign variant p2" in rebuilt.read_text(encoding="utf-8")

    async def test_local_fast_path_when_file_present(self, tmp_path: Path) -> None:
        """(d) When this pod already has the file, do NOT download or overwrite."""
        job = _make_job(script_artifact_id=uuid4())
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        # Pre-seed the working file as if THIS pod generated it.
        existing = tmp_path / _VALID_JOB_ID / "scripts" / "parts" / "p1.demo.spec.ts"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text("// locally generated -- must be preserved", encoding="utf-8")

        artifact_client = AsyncMock()

        validation_svc = AsyncMock()
        validation_svc.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        storage = StorageService(local_path=str(tmp_path))

        orch = _make_orchestrator(
            job_repo=job_repo,
            validation_service=validation_svc,
            storage=storage,
            artifact_client=artifact_client,
            usage_repo=AsyncMock(),
        )

        await orch.validate_job(_VALID_JOB_ID, TENANT_A_STR)

        artifact_client.download_to_path.assert_not_awaited()
        assert "locally generated" in existing.read_text(encoding="utf-8")


class TestCrossReplicaCancel:
    async def test_flag_set_by_other_pod_stops_running_job(self) -> None:
        """(c) A cancel flag set on another pod stops process_job and marks it cancelled."""
        job = _make_job(status=JobStatus.PENDING)
        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)

        gen_svc = AsyncMock()

        cancel_flags = CancelFlags(_FakeRedis())
        # Simulate the DELETE handler on ANOTHER pod having set the flag.
        await cancel_flags.request_cancel(_VALID_JOB_ID, tenant_id=TENANT_A_STR)

        orch = _make_orchestrator(
            job_repo=job_repo,
            generation_service=gen_svc,
            cancel_flags=cancel_flags,
        )

        # Must NOT raise out of process_job -- a cooperative cancel is not a failure.
        await orch.process_job(_VALID_JOB_ID, TENANT_A_STR)

        gen_svc.generate.assert_not_awaited()
        job_repo.mark_cancelled.assert_awaited_once_with(_VALID_JOB_ID, tenant_id=TENANT_A_STR)
        job_repo.mark_completed.assert_not_awaited()
        job_repo.mark_failed.assert_not_awaited()
        # Flag cleared after observing it.
        assert await cancel_flags.is_cancelled(_VALID_JOB_ID, tenant_id=TENANT_A_STR) is False

    async def test_request_remote_cancel_sets_flag(self) -> None:
        """The orchestrator helper the route calls actually sets a pollable flag."""
        cancel_flags = CancelFlags(_FakeRedis())
        orch = _make_orchestrator(cancel_flags=cancel_flags)

        await orch.request_remote_cancel(_VALID_JOB_ID, tenant_id=TENANT_A_STR)

        assert await cancel_flags.is_cancelled(_VALID_JOB_ID, tenant_id=TENANT_A_STR) is True
        # Tenant-scoped: a different tenant's flag is independent.
        assert await cancel_flags.is_cancelled(_VALID_JOB_ID, tenant_id=TENANT_C_STR) is False


@pytest.mark.parametrize("part_type", ["browser_recording", "title_card"])
async def test_generation_loop_honours_cancel_check(part_type: str) -> None:
    """generate()/improve() poll cancel_check and raise before doing LLM work."""
    from src.infra.cancel_flags import JobCancelledError
    from src.services.scripts.generation_service import GenerationService

    llm = AsyncMock()
    prompt_builder = MagicMock()
    prompt_builder.with_overrides.return_value = prompt_builder
    prompt_builder.build.return_value = "prompt"

    svc = GenerationService(llm, prompt_builder)

    async def _always_cancelled() -> bool:
        return True

    with pytest.raises(JobCancelledError):
        await svc.generate(
            part={"id": "p1", "part_type": part_type}, cancel_check=_always_cancelled
        )

    # LLM must never have been called once cancel was observed.
    llm.complete.assert_not_awaited()
