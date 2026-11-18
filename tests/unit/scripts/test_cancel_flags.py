"""Tests for src.infra.cancel_flags.CancelFlags."""

from __future__ import annotations

import logging

import pytest
from psycopg import OperationalError as PsycopgOperationalError

# The BUILTIN ConnectionError is NOT what redis-py raises: its ConnectionError
# derives from RedisError(Exception) and merely shares the name. Injecting the
# builtin here is what let a handler that caught only builtins pass this test
# while being dead code in production -- so the fake must raise the real class.
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError as SAOperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from src.infra.cancel_flags import CancelFlags, JobCancelledError, job_row_cancel_fallback


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: object, ttl: int | None = None) -> bool:
        self.store[key] = str(value)
        return True

    async def exists(self, key: str) -> bool:
        return key in self.store

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None


class _BrokenRedis:
    async def set(self, key: str, value: object, ttl: int | None = None) -> bool:
        raise RedisConnectionError("redis down")

    async def exists(self, key: str) -> bool:
        raise RedisConnectionError("redis down")

    async def delete(self, key: str) -> bool:
        raise RedisConnectionError("redis down")


# The exceptions the REAL fallback stack raises: job_row_cancel_fallback ->
# job_repo.get -> SQLAlchemy/psycopg. NONE of these is an OSError, a
# RuntimeError or a ValueError, and psycopg's is not even a SQLAlchemyError --
# which is why the handler cannot enumerate DB error types and must catch
# Exception. Verified: issubclass(sqlalchemy.exc.OperationalError, OSError) is
# False.
_REAL_DB_FAILURES = [
    # pgbouncer/postgres dropped the connection mid-statement.
    SAOperationalError("SELECT 1", {}, PsycopgOperationalError("server closed the connection")),
    # QueuePool exhausted -- the pgbouncer saturation shape, and a SQLAlchemy
    # TimeoutError, NOT the builtin one.
    SATimeoutError("QueuePool limit of size 5 overflow 10 reached"),
    # A raw psycopg error that escaped without SQLAlchemy wrapping it: proof
    # that narrowing to SQLAlchemyError would still leak.
    PsycopgOperationalError("connection is closed"),
]
_REAL_DB_FAILURE_IDS = ["sqlalchemy-operational", "sqlalchemy-queuepool-timeout", "psycopg-raw"]


class _RaisingRepo:
    """A job repo whose ``get`` fails the way the real one fails."""

    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    async def get(self, job_id: str, *, tenant_id: str) -> object | None:
        raise self.exc


class TestCancelFlags:
    async def test_request_then_is_cancelled(self) -> None:
        flags = CancelFlags(_FakeRedis())
        assert await flags.is_cancelled("j1", tenant_id="t1") is False
        await flags.request_cancel("j1", tenant_id="t1")
        assert await flags.is_cancelled("j1", tenant_id="t1") is True

    async def test_clear_removes_flag(self) -> None:
        flags = CancelFlags(_FakeRedis())
        await flags.request_cancel("j1", tenant_id="t1")
        await flags.clear("j1", tenant_id="t1")
        assert await flags.is_cancelled("j1", tenant_id="t1") is False

    async def test_tenant_scoped_keys(self) -> None:
        redis = _FakeRedis()
        flags = CancelFlags(redis)
        await flags.request_cancel("j1", tenant_id="t1")
        assert await flags.is_cancelled("j1", tenant_id="t1") is True
        assert await flags.is_cancelled("j1", tenant_id="t2") is False
        # keys must embed the tenant to prevent cross-tenant cancel
        assert any("t1" in k and "j1" in k for k in redis.store)

    async def test_raise_if_cancelled(self) -> None:
        flags = CancelFlags(_FakeRedis())
        await flags.raise_if_cancelled("j1", tenant_id="t1")  # no flag -> no raise
        await flags.request_cancel("j1", tenant_id="t1")
        with pytest.raises(JobCancelledError):
            await flags.raise_if_cancelled("j1", tenant_id="t1")

    async def test_no_redis_degrades_to_noop(self) -> None:
        flags = CancelFlags(None)
        await flags.request_cancel("j1", tenant_id="t1")  # no-op, no error
        assert await flags.is_cancelled("j1", tenant_id="t1") is False
        await flags.clear("j1", tenant_id="t1")
        await flags.raise_if_cancelled("j1", tenant_id="t1")  # must not raise

    async def test_redis_errors_do_not_propagate(self) -> None:
        flags = CancelFlags(_BrokenRedis())
        await flags.request_cancel("j1", tenant_id="t1")  # swallowed
        assert await flags.is_cancelled("j1", tenant_id="t1") is False  # swallowed -> False
        await flags.clear("j1", tenant_id="t1")  # swallowed


# ── an UNREADABLE flag is not "no cancel requested" ────────────────────
#
# ``is_cancelled`` swallowed every Redis error and returned False, making
# "could not read the flag" indistinguishable from "no cancel was requested".
# With Redis degraded a cross-replica cancel is therefore never observed: the
# DELETE route has already written status='cancelled' to the DB (durably, and
# BEFORE it touches Redis), but the task on the other pod runs to completion
# and calls ``mark_completed`` -- the terminal state flips back to completed
# after the user cancelled.
#
# The DB row is the authority here precisely because it is written first, so
# the gap is resolvable rather than merely loud.


class TestProbeMakesTheGapExpressible:
    async def test_probe_returns_none_when_redis_cannot_answer(self) -> None:
        flags = CancelFlags(_BrokenRedis())
        assert await flags.probe("j1", tenant_id="t1") is None

    async def test_probe_is_false_when_redis_answers_no_flag(self) -> None:
        assert await CancelFlags(_FakeRedis()).probe("j1", tenant_id="t1") is False

    async def test_probe_is_true_when_the_flag_is_set(self) -> None:
        flags = CancelFlags(_FakeRedis())
        await flags.request_cancel("j1", tenant_id="t1")
        assert await flags.probe("j1", tenant_id="t1") is True

    async def test_no_redis_configured_is_a_definite_no_not_unknown(self) -> None:
        """Redis-less is a deliberate single-replica config, not a measurement gap."""
        assert await CancelFlags(None).probe("j1", tenant_id="t1") is False


class TestUnknownIsResolvedAgainstTheDatabase:
    async def test_unreadable_flag_falls_back_to_the_db_row(self) -> None:
        calls: list[tuple[str, str]] = []

        async def _db_says_cancelled(job_id: str, *, tenant_id: str) -> bool:
            calls.append((job_id, tenant_id))
            return True

        flags = CancelFlags(_BrokenRedis(), db_fallback=_db_says_cancelled)
        assert await flags.is_cancelled("j1", tenant_id="t1") is True
        assert calls == [("j1", "t1")], "the DB authority was never consulted"

    async def test_raise_if_cancelled_honours_the_db_fallback(self) -> None:
        async def _db_says_cancelled(job_id: str, *, tenant_id: str) -> bool:
            return True

        flags = CancelFlags(_BrokenRedis(), db_fallback=_db_says_cancelled)
        with pytest.raises(JobCancelledError):
            await flags.raise_if_cancelled("j1", tenant_id="t1")

    async def test_db_says_running_lets_the_job_continue(self) -> None:
        async def _db_says_running(job_id: str, *, tenant_id: str) -> bool:
            return False

        flags = CancelFlags(_BrokenRedis(), db_fallback=_db_says_running)
        assert await flags.is_cancelled("j1", tenant_id="t1") is False

    async def test_a_readable_flag_never_pays_for_a_db_round_trip(self) -> None:
        called = False

        async def _db(job_id: str, *, tenant_id: str) -> bool:
            nonlocal called
            called = True
            return True

        flags = CancelFlags(_FakeRedis(), db_fallback=_db)
        assert await flags.is_cancelled("j1", tenant_id="t1") is False
        assert called is False, "the DB was queried on the happy path"

    @pytest.mark.parametrize("exc", _REAL_DB_FAILURES, ids=_REAL_DB_FAILURE_IDS)
    async def test_db_fallback_failure_does_not_kill_a_live_job(self, exc: BaseException) -> None:
        """Neither authority can answer -> continue. A blip must not fail-closed.

        The exceptions here are the ones the REAL stack raises. An earlier
        version of this test invented ``OSError("db down")``, which no part of
        SQLAlchemy or psycopg ever raises -- so it proved the handler catches
        ``OSError`` and said nothing about what the database throws. It passed
        against an ``except (OSError, RuntimeError, ValueError)`` that caught
        none of the real types, and the error escaped ``is_cancelled`` into the
        orchestrator, which marked the live job FAILED on every checkpoint of a
        Redis outage. That is the same trap this module's header warns about for
        redis-py, committed a second time against the DB.
        """
        flags = CancelFlags(
            _BrokenRedis(),
            db_fallback=job_row_cancel_fallback(_RaisingRepo(exc)),
        )
        assert await flags.is_cancelled("j1", tenant_id="t1") is False

    @pytest.mark.parametrize("exc", _REAL_DB_FAILURES, ids=_REAL_DB_FAILURE_IDS)
    async def test_a_db_failure_never_cancels_a_live_job(
        self, exc: BaseException, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The regression, stated as the orchestrator sees it.

        ``raise_if_cancelled`` must not leak a DB error: the orchestrator wraps
        the whole run in ``except Exception`` and calls ``_mark_failed_best_effort``,
        so a leak here turns a healthy job into a FAILED one.

        The sibling tests cover ``is_cancelled``; this one covers the entry point
        the orchestrator actually calls, and asserts the WARNING survives it. That
        log line is the ONLY observability an unresolvable cancel has -- the
        ``db_fallback is None`` warning was deleted as unreachable -- so if
        ``raise_if_cancelled`` ever grew its own quiet path around ``is_cancelled``
        the gap would go dark with every other test still green.
        """
        flags = CancelFlags(
            _BrokenRedis(),
            db_fallback=job_row_cancel_fallback(_RaisingRepo(exc)),
        )
        with caplog.at_level(logging.WARNING, logger="src.infra.cancel_flags"):
            # Explicitly the return value, not merely "it did not raise": the
            # contract is that the checkpoint passes and yields nothing.
            assert await flags.raise_if_cancelled("j1", tenant_id="t1") is None
            # ... and that the same unresolvable state is still a definite "no".
            assert await flags.is_cancelled("j1", tenant_id="t1") is False

        unresolved = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "UNKNOWN" in r.getMessage()
        ]
        assert unresolved, (
            "raise_if_cancelled absorbed an unresolvable cancel state without a WARNING"
        )
        first = unresolved[0].getMessage()
        assert "j1" in first, f"the WARNING does not name the job: {first!r}"
        assert type(exc).__name__ in first, f"the WARNING does not name what failed: {first!r}"

    @pytest.mark.parametrize("exc", _REAL_DB_FAILURES, ids=_REAL_DB_FAILURE_IDS)
    async def test_an_unresolvable_unknown_is_logged_loudly(
        self, exc: BaseException, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The gap must be visible in the logs, not merely absorbed.

        This is the ONLY reachable unresolvable-UNKNOWN path in production:
        ``asgi.py`` wires ``db_fallback`` unconditionally, so "no DB authority
        wired" cannot happen there -- Redis is readable-or-not and the fallback
        is always present. Hence Redis down + the DB down is the shape to log.
        """
        flags = CancelFlags(
            _BrokenRedis(),
            db_fallback=job_row_cancel_fallback(_RaisingRepo(exc)),
        )
        with caplog.at_level(logging.WARNING, logger="src.infra.cancel_flags"):
            assert await flags.is_cancelled("j1", tenant_id="t1") is False
        assert any("unknown" in r.message.lower() for r in caplog.records), (
            "an unresolvable cancel state was absorbed silently"
        )


class TestTheDatabaseAuthorityIsWiredNotJustAvailable:
    """A tri-state nobody wired is inert in production.

    ``job_row_cancel_fallback`` is the exact callable ``asgi.py`` hands to
    ``CancelFlags``; testing it here is what stops the whole defect-2 fix from
    being a mechanism with no consumer.
    """

    class _Job:
        def __init__(self, status: str) -> None:
            self.status = status

    class _Repo:
        def __init__(self, job: object | None) -> None:
            self.job = job
            self.seen: list[tuple[str, str]] = []

        async def get(self, job_id: str, *, tenant_id: str) -> object | None:
            self.seen.append((job_id, tenant_id))
            return self.job

    async def test_a_cancelled_row_is_reported_cancelled(self) -> None:
        from src.infra.cancel_flags import job_row_cancel_fallback

        repo = self._Repo(self._Job("cancelled"))
        assert await job_row_cancel_fallback(repo)("j1", tenant_id="t1") is True
        assert repo.seen == [("j1", "t1")], "the fallback must stay tenant-scoped"

    async def test_a_running_row_is_not_reported_cancelled(self) -> None:
        from src.infra.cancel_flags import job_row_cancel_fallback

        assert (
            await job_row_cancel_fallback(self._Repo(self._Job("processing")))("j1", tenant_id="t1")
            is False
        )

    async def test_a_missing_row_is_not_reported_cancelled(self) -> None:
        from src.infra.cancel_flags import job_row_cancel_fallback

        assert await job_row_cancel_fallback(self._Repo(None))("j1", tenant_id="t1") is False

    async def test_it_resolves_an_unknown_probe_end_to_end(self) -> None:
        from src.infra.cancel_flags import job_row_cancel_fallback

        flags = CancelFlags(
            _BrokenRedis(),
            db_fallback=job_row_cancel_fallback(self._Repo(self._Job("cancelled"))),
        )
        with pytest.raises(JobCancelledError):
            await flags.raise_if_cancelled("j1", tenant_id="t1")
