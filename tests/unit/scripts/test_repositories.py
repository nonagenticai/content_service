"""Tests for batch_repository.py and job_repository.py.

Uses mock session factories to test repository CRUD operations without a real database.
All read/write methods require a ``tenant_id`` keyword argument; the tests assert
the SQL WHERE clauses carry it and that cross-tenant lookups behave as not-found.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domain.job import Job, JobStatus
from src.repository.batch_repository import BatchRepository
from src.repository.job_repository import JobRepository
from tests.conftest import TENANT_A, TENANT_A_STR


def _assert_bound_then_queried(session: AsyncMock, *, queries: int = 1) -> None:
    """The repository binds the tenant GUC, THEN issues its query.

    These sites asserted `session.execute.assert_awaited_once()`. That was only
    ever true because `bind_session_tenant` was a no-op under the old
    `TENANT_RLS_MODE` default of `"off"` — it is now `"enforce"` (w2v_common
    #113), so the bind runs and there are two awaits.

    Asserting the COUNT again would just re-encode the same brittleness. Assert
    the property that matters instead: isolation was established before any row
    was touched. A repository that queried first and bound second would pass a
    count check and fail this one.
    """
    assert session.execute.await_count == queries + 1, (
        f"expected a tenant bind followed by {queries} query/queries, got "
        f"{session.execute.await_count} execute(s)"
    )
    first = str(session.execute.await_args_list[0].args[0]).lower()
    assert "set_config" in first or "current_tenant" in first, (
        f"the FIRST execute must be the tenant GUC bind, not a data query: {first!r}"
    )


def _executes(*results: object) -> list[object]:
    """Mocked ``session.execute`` returns, with the RLS bind accounted for.

    Every repository method here calls ``bind_session_tenant`` before its first
    query. Under w2v_common < 0.59.0 that was a NO-OP — `TENANT_RLS_MODE`
    defaulted to ``"off"`` — so it issued no `session.execute` and a
    `side_effect` list could start at the real query.

    w2v_common #113 flipped the default to ``"enforce"`` (fail-CLOSED: isolation
    must be opted OUT of, never opted IN to by remembering a ConfigMap key). The
    bind now runs and consumes one `execute`, shifting every subsequent mocked
    result by one — which is what reddened these tests on the 0.63.0 bump.

    That shift is the tests catching a REAL behaviour change, not a fixture
    nuisance: this service now binds the tenant GUC where it previously did not.
    Prepending the bind here models the call sequence the code actually makes.
    """
    return [MagicMock(), *results]


# ---------------------------------------------------------------------------
# Session factory helper
# ---------------------------------------------------------------------------


def _make_session_factory(session=None):
    """Build a mock async_sessionmaker that yields the given session."""
    if session is None:
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_result.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=mock_result)

    @asynccontextmanager
    async def factory():
        yield session

    return factory


# ---------------------------------------------------------------------------
# BatchRepository
# ---------------------------------------------------------------------------


class TestBatchRepository:
    @pytest.mark.asyncio
    async def test_create_batch(self) -> None:
        """BatchRepository.create inserts a new ScriptBatch row."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        await repo.create(batch_id="b1", tenant_id=TENANT_A_STR, total_parts=3)

        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        added_obj = session.add.call_args[0][0]
        assert added_obj.batch_id == "b1"
        assert added_obj.tenant_id == TENANT_A_STR
        assert added_obj.total_parts == 3
        assert added_obj.status == "pending"

    @pytest.mark.asyncio
    async def test_get_status_not_found(self) -> None:
        """BatchRepository.get_status returns None for missing batch."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        result = await repo.get_status("missing", tenant_id=TENANT_A_STR)
        assert result is None

    @pytest.mark.asyncio
    async def test_get_status_completed(self) -> None:
        """BatchRepository.get_status returns correct status for completed batch."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 2

        job1 = MagicMock()
        job1.status = "completed"
        job2 = MagicMock()
        job2.status = "completed"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1, job2]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result))
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        result = await repo.get_status("b1", tenant_id=TENANT_A_STR)

        assert result is not None
        assert result["batch_id"] == "b1"
        assert result["total"] == 2
        assert result["completed"] == 2
        assert result["failed"] == 0
        assert result["status"] == "completed"

    @pytest.mark.asyncio
    async def test_get_status_partial(self) -> None:
        """BatchRepository.get_status returns 'partial' when some jobs fail."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 2

        job1 = MagicMock()
        job1.status = "completed"
        job2 = MagicMock()
        job2.status = "failed"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1, job2]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result))
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        result = await repo.get_status("b1", tenant_id=TENANT_A_STR)

        assert result is not None
        assert result["status"] == "partial"
        assert result["failed"] == 1

    @pytest.mark.asyncio
    async def test_get_status_in_progress(self) -> None:
        """BatchRepository.get_status returns 'in_progress' when jobs are processing."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 3

        job1 = MagicMock()
        job1.status = "completed"
        job2 = MagicMock()
        job2.status = "processing"
        job3 = MagicMock()
        job3.status = "pending"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1, job2, job3]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result))
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        result = await repo.get_status("b1", tenant_id=TENANT_A_STR)

        assert result is not None
        assert result["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_get_status_pending(self) -> None:
        """BatchRepository.get_status returns 'pending' when all jobs pending."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 2

        job1 = MagicMock()
        job1.status = "pending"
        job2 = MagicMock()
        job2.status = "pending"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1, job2]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result))
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        result = await repo.get_status("b1", tenant_id=TENANT_A_STR)

        assert result is not None
        assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_update_status_not_found(self) -> None:
        """BatchRepository.update_status returns early for missing batch."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        await repo.update_status("missing", tenant_id=TENANT_A_STR)
        # Should not crash

    @pytest.mark.asyncio
    async def test_update_status_completed(self) -> None:
        """BatchRepository.update_status sets completed status."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 1

        job1 = MagicMock()
        job1.status = "completed"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result, AsyncMock()))
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        await repo.update_status("b1", tenant_id=TENANT_A_STR)

        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_status_in_progress(self) -> None:
        """BatchRepository.update_status sets in_progress status."""
        batch = MagicMock()
        batch.batch_id = "b1"
        batch.total_parts = 2

        job1 = MagicMock()
        job1.status = "completed"
        job2 = MagicMock()
        job2.status = "processing"

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        jobs_result = MagicMock()
        jobs_result.scalars.return_value.all.return_value = [job1, job2]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=_executes(batch_result, jobs_result, AsyncMock()))
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = BatchRepository(factory)
        await repo.update_status("b1", tenant_id=TENANT_A_STR)

        session.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# JobRepository
# ---------------------------------------------------------------------------


class TestJobRepository:
    @pytest.mark.asyncio
    async def test_create_job(self) -> None:
        """JobRepository.create inserts a new ScriptJob row."""
        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        job = await repo.create(
            tenant_id=TENANT_A_STR,
            part_id="p1",
            page_url="https://example.com",
            mode="webapp_commercial",
        )

        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        assert isinstance(job, Job)
        assert job.tenant_id == TENANT_A
        assert job.part_id == "p1"
        assert job.status == JobStatus.PENDING

    @pytest.mark.asyncio
    async def test_get_job_found(self) -> None:
        """JobRepository.get returns Job when found."""
        row = MagicMock()
        row.job_id = "j1"
        row.tenant_id = TENANT_A
        row.batch_id = None
        row.part_id = "p1"
        row.part_type = "browser_recording"
        row.status = "completed"
        row.shot_details = {}
        row.page_analysis = None
        row.theme = None
        row.storyline_context = None
        row.user_context = None
        row.mode = "webapp_commercial"
        row.source_type = "web"
        row.auth_enabled = False
        row.script_source = "code"
        row.script_path = "/path"
        row.script_artifact_id = None
        row.prompt_used = None
        row.generation_time_seconds = 1.5
        row.error_message = None
        row.created_at = datetime.now(tz=UTC)
        row.started_at = None
        row.completed_at = None

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        job = await repo.get("j1", tenant_id=TENANT_A_STR)

        assert job is not None
        assert job.job_id == "j1"
        assert job.status == JobStatus.COMPLETED

    @pytest.mark.asyncio
    async def test_get_job_not_found(self) -> None:
        """JobRepository.get returns None for missing job."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        job = await repo.get("missing", tenant_id=TENANT_A_STR)
        assert job is None

    @pytest.mark.asyncio
    async def test_get_row(self) -> None:
        """JobRepository.get_row returns raw ORM row."""
        row = MagicMock()
        row.job_id = "j1"

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_result)
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        result = await repo.get_row("j1", tenant_id=TENANT_A_STR)
        assert result is row

    @pytest.mark.asyncio
    async def test_mark_processing(self) -> None:
        """JobRepository.mark_processing updates status."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        await repo.mark_processing("j1", tenant_id=TENANT_A_STR)

        _assert_bound_then_queried(session)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_completed(self) -> None:
        """JobRepository.mark_completed updates status with output data."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        await repo.mark_completed(
            "j1",
            tenant_id=TENANT_A_STR,
            script_path="/scripts/j1.ts",
            script_source="const x = 1;",
            generation_time_seconds=2.0,
            provider_used="deepseek",
            extraction_tokens_used=150,
        )

        _assert_bound_then_queried(session)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_validated(self) -> None:
        """JobRepository.mark_validated stores validation results.

        Two statements now: the ``SELECT ... FOR UPDATE`` that reads the prior
        ``accepted`` value (so the caller can bill the acceptance exactly once)
        and the UPDATE itself.
        """
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        await repo.mark_validated(
            "j1",
            tenant_id=TENANT_A_STR,
            compiles=True,
            discoverable=True,
            runs_headless=True,
            accepted=True,
            validation_errors=[],
            improvement_attempts=0,
        )

        _assert_bound_then_queried(session, queries=2)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_failed(self) -> None:
        """JobRepository.mark_failed sets error and status."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        await repo.mark_failed("j1", "Something went wrong", tenant_id=TENANT_A_STR)

        _assert_bound_then_queried(session)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mark_cancelled(self) -> None:
        """JobRepository.mark_cancelled sets cancelled status."""
        session = AsyncMock()
        session.execute = AsyncMock()
        session.commit = AsyncMock()
        factory = _make_session_factory(session)

        repo = JobRepository(factory)
        await repo.mark_cancelled("j1", tenant_id=TENANT_A_STR)

        _assert_bound_then_queried(session)
        session.commit.assert_awaited_once()

    def test_to_domain_mapping(self) -> None:
        """JobRepository._to_domain maps all fields correctly."""
        row = MagicMock()
        row.job_id = "j1"
        row.tenant_id = TENANT_A
        row.batch_id = "b1"
        row.part_id = "p1"
        row.part_type = "title_card"
        row.status = "failed"
        row.shot_details = {"key": "val"}
        row.page_analysis = {"url": "https://example.com"}
        row.theme = {"primary": "#000"}
        row.storyline_context = "context"
        row.user_context = {"goals": []}
        row.mode = "commercial"
        row.source_type = "mobile"
        row.auth_enabled = True
        row.script_source = "code"
        row.script_path = "/path"
        row.script_artifact_id = None
        row.prompt_used = "prompt"
        row.generation_time_seconds = 3.0
        row.error_message = "error msg"
        row.created_at = datetime.now(tz=UTC)
        row.started_at = datetime.now(tz=UTC)
        row.completed_at = datetime.now(tz=UTC)

        job = JobRepository._to_domain(row)

        assert job.job_id == "j1"
        assert job.batch_id == "b1"
        assert job.part_type == "title_card"
        assert job.status == JobStatus.FAILED
        assert job.shot_details == {"key": "val"}
        assert job.auth_enabled is True
        assert job.error_message == "error msg"

    def test_to_domain_null_part_type(self) -> None:
        """JobRepository._to_domain defaults part_type to browser_recording."""
        row = MagicMock()
        row.job_id = "j1"
        row.tenant_id = TENANT_A
        row.batch_id = None
        row.part_id = "p1"
        row.part_type = None
        row.status = "pending"
        row.shot_details = None
        row.page_analysis = None
        row.theme = None
        row.storyline_context = None
        row.user_context = None
        row.mode = None
        row.source_type = None
        row.auth_enabled = False
        row.script_source = None
        row.script_path = None
        row.script_artifact_id = None
        row.prompt_used = None
        row.generation_time_seconds = None
        row.error_message = None
        row.created_at = None
        row.started_at = None
        row.completed_at = None

        job = JobRepository._to_domain(row)
        assert job.part_type == "browser_recording"
        assert job.shot_details == {}
