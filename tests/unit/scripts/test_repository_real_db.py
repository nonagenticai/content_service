"""Real-database tests for content_service repositories.

Uses postgres+psycopg via the ``db_session_factory`` fixture from
conftest, which connects to the per-service CI database
(``content_service_ci``).
"""

from __future__ import annotations

import uuid

import pytest

from src.domain.job import JobStatus
from src.repository.batch_repository import BatchRepository
from src.repository.job_repository import JobRepository
from tests.conftest import TENANT_A, TENANT_A_STR, TENANT_B_STR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# src takes the tenant as a uuid STRING (the production shape from
# ``get_tenant_id``); assert on the UUID form where a value comes back typed.
_TENANT = TENANT_A_STR
_OTHER_TENANT = TENANT_B_STR


def _uid() -> str:
    return str(uuid.uuid7())


def _sample_shot_details() -> dict:
    return {
        "id": "part-1",
        "pages": ["page-1"],
        "actions": ["click #btn"],
        "key_elements": [".hero"],
    }


def _sample_page_analysis() -> dict:
    return {
        "page_id": "page-1",
        "url": "https://example.com",
        "page_type": "landing",
        "interactive_elements": [{"selector": "#cta", "type": "button"}],
    }


def _sample_theme() -> dict:
    return {
        "primary_color": "#ff0000",
        "font_family": "Arial",
        "dark_mode": False,
    }


def _sample_user_context() -> dict:
    return {
        "description": "An e-commerce site",
        "goals": ["increase conversions"],
        "site_type": "ecommerce",
    }


# ---------------------------------------------------------------------------
# JobRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestJobRepository:
    """Tests for JobRepository against a real postgres database."""

    async def test_create_and_get_returns_domain_job(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        part_id = _uid()

        job = await repo.create(
            tenant_id=_TENANT,
            part_id=part_id,
            part_type="browser_recording",
            page_url="https://example.com",
            page_type="landing",
            shot_details=_sample_shot_details(),
            page_analysis=_sample_page_analysis(),
            theme=_sample_theme(),
            storyline_context="Chapter 1 intro",
            user_context=_sample_user_context(),
            mode="full",
            source_type="url",
            auth_enabled=True,
            batch_id=None,
        )

        assert job.job_id
        assert job.tenant_id == TENANT_A
        assert job.part_id == part_id
        assert job.status == JobStatus.PENDING

        fetched = await repo.get(job.job_id, tenant_id=_TENANT)
        assert fetched is not None
        assert fetched.job_id == job.job_id
        assert fetched.tenant_id == TENANT_A
        assert fetched.part_id == part_id
        assert fetched.part_type == "browser_recording"
        assert fetched.status == JobStatus.PENDING
        assert fetched.storyline_context == "Chapter 1 intro"
        assert fetched.mode == "full"
        assert fetched.source_type == "url"
        assert fetched.auth_enabled is True

    async def test_get_returns_none_for_missing_job(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        result = await repo.get(_uid(), tenant_id=_TENANT)
        assert result is None

    async def test_get_returns_none_for_wrong_tenant(self, db_session_factory):
        """Cross-tenant lookup must return None — same shape as a true miss."""
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())

        # Owning tenant sees it.
        assert await repo.get(job.job_id, tenant_id=_TENANT) is not None
        # Other tenant must not.
        assert await repo.get(job.job_id, tenant_id=_OTHER_TENANT) is None
        assert await repo.get_row(job.job_id, tenant_id=_OTHER_TENANT) is None

    async def test_get_row_returns_orm_object(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        row = await repo.get_row(job.job_id, tenant_id=_TENANT)
        assert row is not None
        assert row.job_id == job.job_id
        assert row.status == "pending"

    async def test_get_row_returns_none_for_missing(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        assert await repo.get_row(_uid(), tenant_id=_TENANT) is None

    # -- JSONB round-trip ---------------------------------------------------

    async def test_shot_details_roundtrip(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        details = _sample_shot_details()
        job = await repo.create(tenant_id=_TENANT, part_id=_uid(), shot_details=details)
        fetched = await repo.get(job.job_id, tenant_id=_TENANT)
        assert fetched is not None
        assert fetched.shot_details == details

    async def test_page_analysis_roundtrip(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        analysis = _sample_page_analysis()
        job = await repo.create(tenant_id=_TENANT, part_id=_uid(), page_analysis=analysis)
        fetched = await repo.get(job.job_id, tenant_id=_TENANT)
        assert fetched is not None
        assert fetched.page_analysis == analysis

    async def test_theme_roundtrip(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        theme = _sample_theme()
        job = await repo.create(tenant_id=_TENANT, part_id=_uid(), theme=theme)
        fetched = await repo.get(job.job_id, tenant_id=_TENANT)
        assert fetched is not None
        assert fetched.theme == theme

    async def test_user_context_roundtrip(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        ctx = _sample_user_context()
        job = await repo.create(tenant_id=_TENANT, part_id=_uid(), user_context=ctx)
        fetched = await repo.get(job.job_id, tenant_id=_TENANT)
        assert fetched is not None
        assert fetched.user_context == ctx

    # -- State transitions --------------------------------------------------

    async def test_mark_processing_sets_status_and_started_at(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())

        await repo.mark_processing(job.job_id, tenant_id=_TENANT)

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.PROCESSING
        assert updated.started_at is not None

    async def test_mark_completed_sets_status_and_output_fields(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)

        await repo.mark_completed(
            job.job_id,
            tenant_id=_TENANT,
            script_path="/scripts/out.ts",
            script_source="import { test } from '@playwright/test';",
            prompt_used="Generate a Playwright script",
            generation_time_seconds=4.2,
            provider_used="deepseek",
            extraction_tokens_used=1500,
        )

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.COMPLETED
        assert updated.script_path == "/scripts/out.ts"
        assert updated.script_source == "import { test } from '@playwright/test';"
        assert updated.prompt_used == "Generate a Playwright script"
        assert updated.generation_time_seconds == pytest.approx(4.2)
        assert updated.completed_at is not None

    async def test_mark_failed_sets_status_and_error(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)

        await repo.mark_failed(job.job_id, "LLM timed out", tenant_id=_TENANT)

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "LLM timed out"
        assert updated.completed_at is not None

    async def test_mark_cancelled_sets_status(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())

        await repo.mark_cancelled(job.job_id, tenant_id=_TENANT)

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED

    async def test_mark_validated_stores_validation_results(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)
        await repo.mark_completed(job.job_id, tenant_id=_TENANT, script_source="// ok")

        validation_errors = ["Element not found", "Timeout on page load"]
        await repo.mark_validated(
            job.job_id,
            tenant_id=_TENANT,
            compiles=True,
            discoverable=True,
            runs_headless=False,
            accepted=False,
            validation_errors=validation_errors,
            improvement_attempts=2,
        )

        row = await repo.get_row(job.job_id, tenant_id=_TENANT)
        assert row is not None
        assert row.compiles == 1
        assert row.discoverable == 1
        assert row.runs_headless == 0
        assert row.accepted == 0
        assert row.validation_errors == validation_errors
        assert row.improvement_attempts == 2

    async def test_mark_validated_validation_errors_roundtrip(self, db_session_factory):
        """Verify the JSONB validation_errors column round-trips correctly."""
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())

        errors = ["selector #missing not found", "navigation timeout"]
        await repo.mark_validated(
            job.job_id, tenant_id=_TENANT, validation_errors=errors, compiles=False
        )

        row = await repo.get_row(job.job_id, tenant_id=_TENANT)
        assert row is not None
        assert row.validation_errors == errors

    async def test_mark_processing_wrong_tenant_is_noop(self, db_session_factory):
        """Tenant-scoped UPDATE must affect zero rows for the wrong tenant."""
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())

        # Wrong-tenant update must not change anything.
        await repo.mark_processing(job.job_id, tenant_id=_OTHER_TENANT)
        owner_view = await repo.get(job.job_id, tenant_id=_TENANT)
        assert owner_view is not None
        assert owner_view.status == JobStatus.PENDING  # unchanged


# ---------------------------------------------------------------------------
# BatchRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestBatchRepository:
    """Tests for BatchRepository against a real postgres database."""

    async def test_create_and_get_status_pending(self, db_session_factory):
        batch_repo = BatchRepository(db_session_factory)
        batch_id = _uid()

        await batch_repo.create(batch_id=batch_id, tenant_id=_TENANT, total_parts=3)

        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["batch_id"] == batch_id
        assert status["total"] == 3
        assert status["completed"] == 0
        assert status["failed"] == 0
        assert status["status"] == "pending"
        assert status["jobs"] == []

    async def test_get_status_returns_none_for_missing_batch(self, db_session_factory):
        batch_repo = BatchRepository(db_session_factory)
        assert await batch_repo.get_status(_uid(), tenant_id=_TENANT) is None

    async def test_get_status_returns_none_for_wrong_tenant(self, db_session_factory):
        """Cross-tenant batch lookup returns None."""
        batch_repo = BatchRepository(db_session_factory)
        batch_id = _uid()
        await batch_repo.create(batch_id=batch_id, tenant_id=_TENANT, total_parts=1)

        assert await batch_repo.get_status(batch_id, tenant_id=_TENANT) is not None
        assert await batch_repo.get_status(batch_id, tenant_id=_OTHER_TENANT) is None

    async def test_get_status_reflects_job_states(self, db_session_factory):
        batch_repo = BatchRepository(db_session_factory)
        job_repo = JobRepository(db_session_factory)
        batch_id = _uid()

        await batch_repo.create(batch_id=batch_id, tenant_id=_TENANT, total_parts=3)

        # Create 3 jobs in this batch
        j1 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)
        j2 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)
        j3 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)

        # All pending -> batch pending
        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["status"] == "pending"
        assert len(status["jobs"]) == 3

        # Mark one processing -> in_progress
        await job_repo.mark_processing(j1.job_id, tenant_id=_TENANT)
        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["status"] == "in_progress"

        # Complete two, fail one -> partial
        await job_repo.mark_completed(j1.job_id, tenant_id=_TENANT, script_source="ok")
        await job_repo.mark_completed(j2.job_id, tenant_id=_TENANT, script_source="ok")
        await job_repo.mark_failed(j3.job_id, "error", tenant_id=_TENANT)

        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["completed"] == 2
        assert status["failed"] == 1
        assert status["status"] == "partial"

    async def test_get_status_all_completed(self, db_session_factory):
        batch_repo = BatchRepository(db_session_factory)
        job_repo = JobRepository(db_session_factory)
        batch_id = _uid()

        await batch_repo.create(batch_id=batch_id, tenant_id=_TENANT, total_parts=2)

        j1 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)
        j2 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)
        await job_repo.mark_completed(j1.job_id, tenant_id=_TENANT, script_source="ok")
        await job_repo.mark_completed(j2.job_id, tenant_id=_TENANT, script_source="ok")

        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["completed"] == 2
        assert status["failed"] == 0
        assert status["status"] == "completed"

    async def test_update_status_recomputes_batch(self, db_session_factory):
        batch_repo = BatchRepository(db_session_factory)
        job_repo = JobRepository(db_session_factory)
        batch_id = _uid()

        await batch_repo.create(batch_id=batch_id, tenant_id=_TENANT, total_parts=1)

        j1 = await job_repo.create(tenant_id=_TENANT, part_id=_uid(), batch_id=batch_id)

        # Initially pending
        await batch_repo.update_status(batch_id, tenant_id=_TENANT)
        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["status"] == "pending"

        # Complete the single job
        await job_repo.mark_completed(j1.job_id, tenant_id=_TENANT, script_source="done")
        await batch_repo.update_status(batch_id, tenant_id=_TENANT)

        status = await batch_repo.get_status(batch_id, tenant_id=_TENANT)
        assert status is not None
        assert status["status"] == "completed"

    async def test_update_status_noop_for_missing_batch(self, db_session_factory):
        """update_status should silently return for a non-existent batch."""
        batch_repo = BatchRepository(db_session_factory)
        await batch_repo.update_status(_uid(), tenant_id=_TENANT)  # should not raise


class TestCancelledIsTerminal:
    """A cancelled job must not be resurrected by a late terminal write.

    The cross-replica cancel is cooperative and therefore best-effort: the
    DELETE route writes ``cancelled`` durably, but the task on the other pod
    only stops at its next checkpoint -- and with Redis degraded it may never
    observe the flag at all. If ``mark_completed`` then overwrites the row, the
    terminal state flips back to completed AFTER the user cancelled and the API
    has already told them otherwise. Guarding the write is the backstop that
    holds even when every cancel-observation path misses.
    """

    async def test_mark_completed_does_not_resurrect_a_cancelled_job(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)
        await repo.mark_cancelled(job.job_id, tenant_id=_TENANT)

        await repo.mark_completed(job.job_id, tenant_id=_TENANT, script_source="// late")

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED, (
            "a late completion flipped a cancelled job back to completed"
        )

    async def test_mark_failed_does_not_resurrect_a_cancelled_job(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)
        await repo.mark_cancelled(job.job_id, tenant_id=_TENANT)

        await repo.mark_failed(job.job_id, "boom", tenant_id=_TENANT)

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.CANCELLED

    async def test_a_running_job_still_completes_normally(self, db_session_factory):
        """The guard must not block the ordinary transition it sits on."""
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)

        await repo.mark_completed(job.job_id, tenant_id=_TENANT, script_source="// ok")

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.COMPLETED

    async def test_a_running_job_still_fails_normally(self, db_session_factory):
        repo = JobRepository(db_session_factory)
        job = await repo.create(tenant_id=_TENANT, part_id=_uid())
        await repo.mark_processing(job.job_id, tenant_id=_TENANT)

        await repo.mark_failed(job.job_id, "boom", tenant_id=_TENANT)

        updated = await repo.get(job.job_id, tenant_id=_TENANT)
        assert updated is not None
        assert updated.status == JobStatus.FAILED
        assert updated.error_message == "boom"
