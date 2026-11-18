"""Tests for infra.task_manager.TaskManager."""

from __future__ import annotations

import asyncio

from src.infra.task_manager import TaskManager


class TestTaskManager:
    async def test_submit_runs_coroutine(self):
        tm = TaskManager(max_concurrent=2)
        result = {}

        async def job():
            result["done"] = True

        await tm.submit("j1", job())
        # Give the task time to run
        await asyncio.sleep(0.05)
        assert result.get("done") is True

    async def test_active_count(self):
        tm = TaskManager(max_concurrent=2)
        event = asyncio.Event()

        async def blocking_job():
            await event.wait()

        await tm.submit("j1", blocking_job())
        await asyncio.sleep(0.01)
        assert tm.active_count >= 1

        event.set()
        await asyncio.sleep(0.05)
        assert tm.active_count == 0

    async def test_cancel_job(self):
        tm = TaskManager(max_concurrent=2)
        event = asyncio.Event()

        async def blocking_job():
            await event.wait()

        await tm.submit("j1", blocking_job())
        await asyncio.sleep(0.01)
        assert await tm.cancel("j1") is True
        # cancel() awaits the task internally and pops from _active_tasks,
        # so active_count must be 0 the moment cancel() returns — no
        # post-cancel sleep needed (was a race-condition smell).
        assert tm.active_count == 0

    async def test_cancel_nonexistent_job(self):
        tm = TaskManager()
        assert await tm.cancel("nonexistent") is False

    async def test_shutdown_empty(self):
        tm = TaskManager()
        await tm.shutdown(timeout=1.0)
        # Should complete without error

    async def test_shutdown_with_tasks(self):
        tm = TaskManager(max_concurrent=2)
        completed = []

        async def job(n):
            await asyncio.sleep(0.01)
            completed.append(n)

        await tm.submit("j1", job(1))
        # Let the task start running before shutting down
        await asyncio.sleep(0.05)
        await tm.shutdown(timeout=5.0)
        # Should have completed (task started before shutdown event was set)
        assert 1 in completed

    async def test_shutdown_cancels_stragglers(self):
        tm = TaskManager(max_concurrent=2)

        async def long_job():
            await asyncio.sleep(100)

        await tm.submit("j1", long_job())
        await asyncio.sleep(0.01)
        await tm.shutdown(timeout=0.01)
        # Should not hang

    async def test_skips_job_during_shutdown(self):
        tm = TaskManager(max_concurrent=1)
        tm._shutdown_event.set()

        ran = False

        async def job():
            nonlocal ran
            ran = True

        await tm.submit("j1", job())
        await asyncio.sleep(0.1)
        assert ran is False

    async def test_job_failure_is_logged(self):
        tm = TaskManager(max_concurrent=2)

        async def failing_job():
            raise ValueError("boom")

        await tm.submit("j1", failing_job())
        await asyncio.sleep(0.1)
        # Should not propagate the error, just log it
        assert tm.active_count == 0
