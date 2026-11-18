"""Tests for api.scripts_routes."""

from __future__ import annotations

from unittest.mock import AsyncMock

from src.api.scripts_routes import _process_job


class TestProcessJob:
    async def test_process_job_success(self):
        orch = AsyncMock()
        orch.process_job = AsyncMock()
        orch.batch_repo = AsyncMock()

        await _process_job(orch, "j1", "t1")
        orch.process_job.assert_awaited_once_with("j1", "t1", llm_config=None)

    async def test_process_job_failure_logged(self):
        orch = AsyncMock()
        orch.process_job = AsyncMock(side_effect=RuntimeError("LLM error"))
        orch.batch_repo = AsyncMock()

        # Should not propagate
        await _process_job(orch, "j1", "t1")
        orch.process_job.assert_awaited_once()

    async def test_process_job_updates_batch(self):
        orch = AsyncMock()
        orch.process_job = AsyncMock()
        orch.batch_repo = AsyncMock()

        await _process_job(orch, "j1", "t1", batch_id="b1")
        orch.batch_repo.update_status.assert_awaited_once_with("b1", tenant_id="t1")

    async def test_process_job_batch_update_failure(self):
        orch = AsyncMock()
        orch.process_job = AsyncMock()
        orch.batch_repo = AsyncMock()
        orch.batch_repo.update_status = AsyncMock(side_effect=RuntimeError("DB error"))

        # Should not propagate batch update error
        await _process_job(orch, "j1", "t1", batch_id="b1")

    async def test_process_job_with_llm_config(self):
        from src.api.scripts_schemas import LLMConfigOverride

        orch = AsyncMock()
        orch.process_job = AsyncMock()
        config = LLMConfigOverride(model="gpt-4")

        await _process_job(orch, "j1", "t1", llm_config=config)
        orch.process_job.assert_awaited_once_with("j1", "t1", llm_config=config)
