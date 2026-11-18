"""Pure-unit tests for the stale-job reconciler — no database required.

Covers config gating, the fail-open contract (a sweep that raises returns None
instead of crashing the loop), the happy-path result handling, and the
start/stop task lifecycle. The DB-backed behavioural test lives in
``tests/test_reconciler_db.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.services.reconciler import StaleJobReconciler


class _Result:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class _FakeSession:
    """Minimal AsyncSession stand-in capturing execute/commit calls."""

    def __init__(self, value: int) -> None:
        self._value = value
        self.committed = False
        self.execute_calls: list[dict] = []

    async def execute(self, _stmt, params=None):
        self.execute_calls.append(params or {})
        return _Result(self._value)

    async def commit(self) -> None:
        self.committed = True


def _provider_for(session: _FakeSession):
    @asynccontextmanager
    async def _provider():
        yield session

    return _provider


def _raising_provider():
    @asynccontextmanager
    async def _provider():
        raise RuntimeError("db down")
        yield  # pragma: no cover

    return _provider


# ---------------------------------------------------------------------------
# Config gating
# ---------------------------------------------------------------------------


def test_reconciler_settings_defaults(monkeypatch) -> None:
    from src.config import Settings

    settings = Settings()
    assert settings.reconciler_enabled is True
    assert settings.reconciler_interval_seconds == 300
    assert settings.reconciler_stale_after_seconds == 1800
    assert settings.reconciler_pending_stale_after_seconds == 21600


def test_reconciler_settings_env_override(monkeypatch) -> None:
    from src.config import Settings

    monkeypatch.setenv("RECONCILER_ENABLED", "false")
    monkeypatch.setenv("RECONCILER_INTERVAL_SECONDS", "42")
    monkeypatch.setenv("RECONCILER_STALE_AFTER_SECONDS", "60")
    monkeypatch.setenv("RECONCILER_PENDING_STALE_AFTER_SECONDS", "600")

    settings = Settings()
    assert settings.reconciler_enabled is False
    assert settings.reconciler_interval_seconds == 42
    assert settings.reconciler_stale_after_seconds == 60
    assert settings.reconciler_pending_stale_after_seconds == 600


# ---------------------------------------------------------------------------
# reconcile_once
# ---------------------------------------------------------------------------


async def test_reconcile_once_returns_count_and_commits() -> None:
    session = _FakeSession(value=3)
    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=_provider_for(session),
    )

    count = await reconciler.reconcile_once()

    assert count == 3
    assert session.committed is True
    # BOTH thresholds are forwarded to the SQL function as bound params. The
    # 'pending' pass has its own, far longer cutoff on purpose: 'pending' is a
    # legitimate queued state, so reusing `secs` there would fail out live work.
    assert session.execute_calls == [{"secs": 1800, "pending_secs": 21600}]


async def test_reconcile_once_is_fail_open_on_error() -> None:
    reconciler = StaleJobReconciler(
        interval_seconds=999,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=_raising_provider(),
    )

    # Must swallow the error and report None rather than propagate.
    assert await reconciler.reconcile_once() is None


# ---------------------------------------------------------------------------
# start / stop lifecycle
# ---------------------------------------------------------------------------


async def test_start_then_stop_cancels_loop() -> None:
    session = _FakeSession(value=0)
    reconciler = StaleJobReconciler(
        interval_seconds=1000,  # loop parks in sleep() immediately
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=_provider_for(session),
    )

    reconciler.start()
    assert reconciler._task is not None
    await asyncio.sleep(0)  # let the loop reach its first sleep()
    assert not reconciler._task.done()

    await reconciler.stop()
    assert reconciler._task is None


async def test_stop_without_start_is_noop() -> None:
    reconciler = StaleJobReconciler(
        interval_seconds=1000,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=_provider_for(_FakeSession(value=0)),
    )
    # Should not raise.
    await reconciler.stop()


async def test_loop_swallows_sweep_errors(monkeypatch) -> None:
    """A loop iteration whose sweep raises must not kill the loop task."""
    reconciler = StaleJobReconciler(
        interval_seconds=0.001,
        stale_after_seconds=1800,
        pending_stale_after_seconds=21600,
        session_provider=_provider_for(_FakeSession(value=0)),
    )

    calls = {"n": 0}

    async def _boom() -> int | None:
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(reconciler, "reconcile_once", _boom)

    reconciler.start()
    # Give the loop several iterations to prove it keeps going after errors.
    await asyncio.sleep(0.05)
    task = reconciler._task
    assert task is not None and not task.done()
    await reconciler.stop()
    assert calls["n"] >= 1


@pytest.mark.parametrize("value", [0, None])
async def test_reconcile_once_handles_zero_and_error(value) -> None:
    if value == 0:
        session = _FakeSession(value=0)
        reconciler = StaleJobReconciler(
            interval_seconds=999,
            stale_after_seconds=1800,
            pending_stale_after_seconds=21600,
            session_provider=_provider_for(session),
        )
        assert await reconciler.reconcile_once() == 0
    else:
        reconciler = StaleJobReconciler(
            interval_seconds=999,
            stale_after_seconds=1800,
            pending_stale_after_seconds=21600,
            session_provider=_raising_provider(),
        )
        assert await reconciler.reconcile_once() is None
