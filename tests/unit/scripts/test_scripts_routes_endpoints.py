"""Tests for scripts_routes.py endpoint handlers.

Covers: generate_script, generate_batch, generate_campaign_pack,
get_job_status, validate_script, scaffold_project, cancel_job,
get_batch_status.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from src.api.scripts_schemas import (
    GenerateBatchRequest,
    GenerateCampaignPackRequest,
    GenerateRequest,
    LLMConfigOverride,
    PageAnalysisInput,
    ScaffoldRequest,
    ShotDetailInput,
    ThemeInput,
    UserContextInput,
    ValidateRequest,
)
from src.domain.job import Job, JobStatus

# tenant_id is a real uuid (fleet ruling); Job.tenant_id is typed UUID.
TEST_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(*, orchestrator=None, task_manager=None):
    """Build a mock Request with app state for endpoint tests."""
    request = MagicMock()
    request.state.tenant_id = "test-tenant"
    if orchestrator:
        request.app.state.orchestrator = orchestrator
    if task_manager:
        request.app.state.task_manager = task_manager
    return request


def _make_orchestrator():
    """Build a mock ScriptOrchestrator."""
    orch = AsyncMock()
    orch.create_job = AsyncMock(return_value="job-001")
    orch.create_batch = AsyncMock(
        return_value=("batch-001", {"part-1": "job-001", "part-2": "job-002"})
    )
    orch.process_job = AsyncMock()
    orch.validate_job = AsyncMock()
    orch.scaffold_job = AsyncMock()
    orch.cancel_job = AsyncMock(return_value=True)
    orch.job_repo = AsyncMock()
    orch.batch_repo = AsyncMock()
    return orch


def _make_task_manager():
    """Build a mock TaskManager."""
    tm = AsyncMock()

    async def _submit(_job_id, coro):
        # The route builds the _process_job coroutine eagerly and hands it to
        # submit; close it so it doesn't GC as a "never awaited" coroutine while
        # still recording the call for assert_awaited_once / await_count.
        coro.close()

    tm.submit = AsyncMock(side_effect=_submit)
    tm.cancel = AsyncMock(return_value=True)
    return tm


def _make_shot_detail(**overrides: Any) -> ShotDetailInput:
    defaults: dict[str, Any] = {
        "id": "part-001",
        "part_type": "browser_recording",
        "title": "Sample Part",
        "pages": ["page-1"],
        "actions": ["click #btn"],
        "estimated_duration_seconds": 12.0,
    }
    defaults.update(overrides)
    return ShotDetailInput(**defaults)


# ---------------------------------------------------------------------------
# generate_script
# ---------------------------------------------------------------------------


class TestGenerateScript:
    @pytest.mark.asyncio
    async def test_generate_script_basic(self) -> None:
        """generate_script creates a job and submits background task."""
        from src.api.scripts_routes import generate_script

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateRequest(part=_make_shot_detail())

        resp = await generate_script(body, "test-tenant", orch, tm)

        assert resp.job_id == "job-001"
        assert resp.status == "pending"
        assert resp.poll_url == "/api/scripts/jobs/job-001"
        orch.create_job.assert_awaited_once()
        tm.submit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_script_with_all_options(self) -> None:
        """generate_script passes page_analysis, theme, user_context."""
        from src.api.scripts_routes import generate_script

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateRequest(
            part=_make_shot_detail(),
            page_analysis={
                "page-1": PageAnalysisInput(page_id="page-1", url="https://example.com")
            },
            theme=ThemeInput(),
            user_context=UserContextInput(description="Test site"),
            storyline_context="Context text",
            message="Generate something great",
            mode="commercial",
            source_type="mobile",
            auth_enabled=True,
            llm_config=LLMConfigOverride(model="gpt-4"),
        )

        resp = await generate_script(body, "test-tenant", orch, tm)

        assert resp.job_id == "job-001"
        # Verify create_job was called with all the args
        call_kwargs = orch.create_job.call_args
        assert call_kwargs.kwargs["tenant_id"] == "test-tenant"
        assert call_kwargs.kwargs["page_analysis"] is not None
        assert call_kwargs.kwargs["theme"] is not None
        assert call_kwargs.kwargs["user_context"] is not None


# ---------------------------------------------------------------------------
# generate_batch
# ---------------------------------------------------------------------------


class TestGenerateBatch:
    @pytest.mark.asyncio
    async def test_generate_batch(self) -> None:
        """generate_batch creates jobs for multiple parts."""
        from src.api.scripts_routes import generate_batch

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateBatchRequest(
            parts=[_make_shot_detail(id="p1"), _make_shot_detail(id="p2")],
        )

        resp = await generate_batch(body, "test-tenant", orch, tm)

        assert resp.batch_id == "batch-001"
        assert resp.status == "pending"
        assert len(resp.job_ids) == 2
        orch.create_batch.assert_awaited_once()
        # One submit per job
        assert tm.submit.await_count == 2

    @pytest.mark.asyncio
    async def test_generate_batch_with_options(self) -> None:
        """generate_batch passes theme, page_analysis, user_context."""
        from src.api.scripts_routes import generate_batch

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateBatchRequest(
            parts=[_make_shot_detail()],
            page_analysis={
                "page-1": PageAnalysisInput(page_id="page-1", url="https://example.com")
            },
            theme=ThemeInput(),
            user_context=UserContextInput(description="Test"),
            storyline_context="Context",
            llm_config=LLMConfigOverride(model="gpt-4"),
        )

        resp = await generate_batch(body, "test-tenant", orch, tm)
        assert resp.batch_id == "batch-001"


# ---------------------------------------------------------------------------
# generate_campaign_pack
# ---------------------------------------------------------------------------


class TestGenerateCampaignPack:
    @pytest.mark.asyncio
    async def test_generate_campaign_pack(self) -> None:
        """generate_campaign_pack creates batch with expanded parts."""
        from src.api.scripts_routes import generate_campaign_pack

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateCampaignPackRequest(
            part=_make_shot_detail(),
            script_styles=["webapp_commercial"],
            variants_per_style=2,
        )

        resp = await generate_campaign_pack(body, "test-tenant", orch, tm)

        assert resp.batch_id == "batch-001"
        assert resp.status == "pending"
        assert resp.total_variants >= 1

    @pytest.mark.asyncio
    async def test_generate_campaign_pack_too_many_parts(self) -> None:
        """generate_campaign_pack raises 422 when expansion exceeds 50."""
        from fastapi import HTTPException

        from src.api.scripts_routes import generate_campaign_pack

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateCampaignPackRequest(
            part=_make_shot_detail(),
            script_styles=["s1", "s2", "s3", "s4", "s5", "s6"],
            variants_per_style=10,
        )

        with pytest.raises(HTTPException) as exc_info:
            await generate_campaign_pack(body, "test-tenant", orch, tm)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_generate_campaign_pack_with_all_options(self) -> None:
        """generate_campaign_pack passes all optional params."""
        from src.api.scripts_routes import generate_campaign_pack

        orch = _make_orchestrator()
        tm = _make_task_manager()

        body = GenerateCampaignPackRequest(
            part=_make_shot_detail(),
            page_analysis={
                "page-1": PageAnalysisInput(page_id="page-1", url="https://example.com")
            },
            theme=ThemeInput(),
            user_context=UserContextInput(description="Test"),
            script_styles=["webapp_commercial"],
            variants_per_style=1,
            hooks=["Hook 1"],
            ctas=["CTA 1"],
            llm_config=LLMConfigOverride(model="gpt-4"),
        )

        resp = await generate_campaign_pack(body, "test-tenant", orch, tm)
        assert resp.batch_id == "batch-001"


# ---------------------------------------------------------------------------
# get_job_status
# ---------------------------------------------------------------------------


class TestGetJobStatus:
    @pytest.mark.asyncio
    async def test_get_job_status_found(self) -> None:
        """get_job_status returns job details."""
        from src.api.scripts_routes import get_job_status

        now = datetime.now(tz=UTC)
        job = Job(
            job_id="job-001",
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            status=JobStatus.COMPLETED,
            script_source="const x = 1;",
            script_path="/scripts/job-001.ts",
            generation_time_seconds=2.5,
            created_at=now,
            completed_at=now,
        )

        row = MagicMock()
        row.extraction_tokens_used = 100

        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)
        job_repo.get_row = AsyncMock(return_value=row)

        request = MagicMock()
        request.app.state.orchestrator.job_repo = job_repo

        resp = await get_job_status("job-001", request, tenant_id="test-tenant")

        assert resp.job_id == "job-001"
        assert resp.status == "completed"
        assert resp.script_source == "const x = 1;"
        assert resp.token_usage == {"total_tokens": 100}
        job_repo.get.assert_awaited_with("job-001", tenant_id="test-tenant")
        job_repo.get_row.assert_awaited_with("job-001", tenant_id="test-tenant")

    @pytest.mark.asyncio
    async def test_get_job_status_not_found(self) -> None:
        """get_job_status raises 404 when job not found."""
        from fastapi import HTTPException

        from src.api.scripts_routes import get_job_status

        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=None)

        request = MagicMock()
        request.app.state.orchestrator.job_repo = job_repo

        with pytest.raises(HTTPException) as exc_info:
            await get_job_status("missing", request, tenant_id="test-tenant")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_job_status_no_tokens(self) -> None:
        """get_job_status returns None token_usage when no tokens tracked."""
        from src.api.scripts_routes import get_job_status

        job = Job(
            job_id="job-002",
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            status=JobStatus.COMPLETED,
        )

        row = MagicMock()
        row.extraction_tokens_used = None

        job_repo = AsyncMock()
        job_repo.get = AsyncMock(return_value=job)
        job_repo.get_row = AsyncMock(return_value=row)

        request = MagicMock()
        request.app.state.orchestrator.job_repo = job_repo

        resp = await get_job_status("job-002", request, tenant_id="test-tenant")
        assert resp.token_usage is None


# ---------------------------------------------------------------------------
# validate_script
# ---------------------------------------------------------------------------


class TestValidateScript:
    @pytest.mark.asyncio
    async def test_validate_script_success(self) -> None:
        """validate_script returns validation result."""
        from src.api.scripts_routes import validate_script

        job = Job(
            job_id="job-001",
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            status=JobStatus.COMPLETED,
        )

        result = MagicMock()
        result.compiles = True
        result.discoverable = True
        result.runs_headless = True
        result.accepted = True
        result.errors = []
        result.improvement_attempts = 0
        result.attempts_detail = []

        orch = _make_orchestrator()
        orch.job_repo.get = AsyncMock(return_value=job)
        orch.validate_job = AsyncMock(return_value=result)

        body = ValidateRequest()

        resp = await validate_script("job-001", body, "test-tenant", orch)

        assert resp.job_id == "job-001"
        assert resp.accepted is True
        assert resp.compiles is True

    @pytest.mark.asyncio
    async def test_validate_script_not_found(self) -> None:
        """validate_script raises 404 when job missing."""
        from fastapi import HTTPException

        from src.api.scripts_routes import validate_script

        orch = _make_orchestrator()
        orch.job_repo.get = AsyncMock(return_value=None)

        body = ValidateRequest()

        with pytest.raises(HTTPException) as exc_info:
            await validate_script("missing", body, "test-tenant", orch)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_validate_script_not_ready(self) -> None:
        """validate_script raises 409 when job not completed."""
        from fastapi import HTTPException

        from src.api.scripts_routes import validate_script

        job = Job(
            job_id="job-001",
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            status=JobStatus.PROCESSING,
        )

        orch = _make_orchestrator()
        orch.job_repo.get = AsyncMock(return_value=job)

        body = ValidateRequest()

        with pytest.raises(HTTPException) as exc_info:
            await validate_script("job-001", body, "test-tenant", orch)
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_validate_script_with_attempts(self) -> None:
        """validate_script returns improvement attempt details."""
        from src.api.scripts_routes import validate_script

        job = Job(
            job_id="job-001",
            tenant_id=TEST_TENANT_ID,
            part_id="p1",
            status=JobStatus.COMPLETED,
        )

        attempt = MagicMock()
        attempt.attempt = 1
        attempt.errors = ["syntax error"]
        attempt.fix_applied = True
        attempt.accepted = False
        attempt.reason = "Still has errors"

        result = MagicMock()
        result.compiles = False
        result.discoverable = False
        result.runs_headless = False
        result.accepted = False
        result.errors = ["syntax error"]
        result.improvement_attempts = 1
        result.attempts_detail = [attempt]

        orch = _make_orchestrator()
        orch.job_repo.get = AsyncMock(return_value=job)
        orch.validate_job = AsyncMock(return_value=result)

        body = ValidateRequest()

        resp = await validate_script("job-001", body, "test-tenant", orch)

        assert resp.accepted is False
        assert resp.improvement_attempts == 1
        assert len(resp.attempts_detail) == 1
        assert resp.attempts_detail[0].attempt == 1


# ---------------------------------------------------------------------------
# scaffold_project
# ---------------------------------------------------------------------------


class TestScaffoldProject:
    @pytest.mark.asyncio
    async def test_scaffold_project(self) -> None:
        """scaffold_project calls orchestrator and returns result."""
        from src.api.scripts_routes import scaffold_project

        orch = _make_orchestrator()
        orch.scaffold_job = AsyncMock(return_value={"success": True, "scripts_dir": "/tmp/scripts"})

        body = ScaffoldRequest(theme=ThemeInput())

        resp = await scaffold_project("job-001", body, "test-tenant", orch)

        assert resp.success is True
        assert resp.scripts_dir == "/tmp/scripts"
        orch.scaffold_job.assert_awaited_once()
        assert orch.scaffold_job.call_args.kwargs["tenant_id"] == "test-tenant"

    @pytest.mark.asyncio
    async def test_scaffold_project_no_theme(self) -> None:
        """scaffold_project works without theme."""
        from src.api.scripts_routes import scaffold_project

        orch = _make_orchestrator()
        orch.scaffold_job = AsyncMock(return_value={"success": True, "scripts_dir": "/tmp/scripts"})

        body = ScaffoldRequest()

        resp = await scaffold_project("job-002", body, "test-tenant", orch)

        assert resp.success is True
        orch.scaffold_job.assert_awaited_once_with("job-002", tenant_id="test-tenant", theme=None)


# ---------------------------------------------------------------------------
# cancel_job
# ---------------------------------------------------------------------------


class TestCancelJob:
    @pytest.mark.asyncio
    async def test_cancel_job_success(self) -> None:
        """cancel_job returns cancelled status."""
        from src.api.scripts_routes import cancel_job

        orch = _make_orchestrator()
        tm = _make_task_manager()

        resp = await cancel_job("job-001", "test-tenant", orch, tm)

        assert resp == {"job_id": "job-001", "status": "cancelled"}
        tm.cancel.assert_awaited_once_with("job-001")
        orch.cancel_job.assert_awaited_once_with("job-001", tenant_id="test-tenant")

    @pytest.mark.asyncio
    async def test_cancel_job_not_found(self) -> None:
        """cancel_job raises 404 when neither task nor DB found."""
        from fastapi import HTTPException

        from src.api.scripts_routes import cancel_job

        orch = _make_orchestrator()
        orch.cancel_job = AsyncMock(return_value=False)
        tm = _make_task_manager()
        tm.cancel = AsyncMock(return_value=False)

        with pytest.raises(HTTPException) as exc_info:
            await cancel_job("ghost-job", "test-tenant", orch, tm)
        assert exc_info.value.status_code == 404
        # DB rejected → in-process cancel must not run (no cross-tenant kill).
        tm.cancel.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_batch_status
# ---------------------------------------------------------------------------


class TestGetBatchStatus:
    @pytest.mark.asyncio
    async def test_get_batch_status_found(self) -> None:
        """get_batch_status returns batch details with job results."""
        from src.api.scripts_routes import get_batch_status

        job1 = MagicMock()
        job1.job_id = "j1"
        job1.part_id = "p1"
        job1.status = "completed"
        job1.script_source = "code"
        job1.script_path = "/path"
        job1.script_artifact_id = None
        job1.generation_time_seconds = 1.5
        job1.provider_used = "deepseek"
        job1.error_message = None
        job1.extraction_tokens_used = 50

        batch_data = {
            "batch_id": "batch-001",
            "total": 1,
            "completed": 1,
            "failed": 0,
            "status": "completed",
            "jobs": [job1],
        }

        batch_repo = AsyncMock()
        batch_repo.get_status = AsyncMock(return_value=batch_data)

        request = MagicMock()
        request.app.state.orchestrator.batch_repo = batch_repo

        resp = await get_batch_status("batch-001", request, tenant_id="test-tenant")

        assert resp.batch_id == "batch-001"
        assert resp.total == 1
        assert resp.completed == 1
        assert resp.status == "completed"
        assert len(resp.results) == 1
        assert resp.results[0].token_usage == {"total_tokens": 50}
        batch_repo.get_status.assert_awaited_with("batch-001", tenant_id="test-tenant")

    @pytest.mark.asyncio
    async def test_get_batch_status_not_found(self) -> None:
        """get_batch_status raises 404 when batch not found."""
        from fastapi import HTTPException

        from src.api.scripts_routes import get_batch_status

        batch_repo = AsyncMock()
        batch_repo.get_status = AsyncMock(return_value=None)

        request = MagicMock()
        request.app.state.orchestrator.batch_repo = batch_repo

        with pytest.raises(HTTPException) as exc_info:
            await get_batch_status("missing-batch", request, tenant_id="test-tenant")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_batch_status_no_tokens(self) -> None:
        """get_batch_status returns None token_usage when not tracked."""
        from src.api.scripts_routes import get_batch_status

        job1 = MagicMock()
        job1.job_id = "j1"
        job1.part_id = "p1"
        job1.status = "completed"
        job1.script_source = None
        job1.script_path = None
        job1.script_artifact_id = None
        job1.generation_time_seconds = None
        job1.provider_used = None
        job1.error_message = None
        job1.extraction_tokens_used = None

        batch_data = {
            "batch_id": "b1",
            "total": 1,
            "completed": 1,
            "failed": 0,
            "status": "completed",
            "jobs": [job1],
        }

        batch_repo = AsyncMock()
        batch_repo.get_status = AsyncMock(return_value=batch_data)

        request = MagicMock()
        request.app.state.orchestrator.batch_repo = batch_repo

        resp = await get_batch_status("b1", request, tenant_id="test-tenant")
        assert resp.results[0].token_usage is None
