"""Cross-replica cooperative cancellation for script jobs.

At >1 replica the ``asyncio`` task that runs a generation/validation job may
live on a *different* pod than the one that handles ``DELETE
/api/scripts/jobs/{job_id}``. ``TaskManager.cancel`` only reaches the local
pod's in-process task dict, so a cancel issued on pod B never stops a task
running on pod A. To close that gap we also set a **Redis flag** keyed by
(tenant, job): the running task polls it at safe checkpoints and stops
cooperatively regardless of which pod it lives on.

The flag is best-effort: if Redis is *not configured* the helper degrades to a
no-op (``is_cancelled`` returns ``False``) so the single-replica in-process
fast-path still works and generation is never blocked by a Redis outage.

A Redis *failure* is a different thing, and conflating the two is what this
module used to do: ``is_cancelled`` swallowed the error and returned ``False``,
making "could not read the flag" indistinguishable from "no cancel was
requested". With Redis degraded a cross-replica cancel was then never observed
-- the DB row said ``cancelled`` while the task on the other pod ran to
completion and called ``mark_completed``, flipping the terminal state back to
completed after the user had cancelled.

The gap is now expressible: :meth:`CancelFlags.probe` returns a tri-state where
``None`` means UNKNOWN. :meth:`CancelFlags.is_cancelled` resolves an UNKNOWN
against the **database**, which is the real authority -- ``DELETE
/api/scripts/jobs/{id}`` writes ``status='cancelled'`` durably and *before* it
touches Redis, so the row is never behind the flag. Only when neither authority
can answer does it fall back to ``False``, loudly: a hard fail-closed would kill
every live job on a Redis blip, which is strictly worse than missing a cancel.

That promise only holds because the degrade tuples below name
``redis.exceptions.RedisError``. It derives from ``Exception``, **not** from
``OSError``, and its ``ConnectionError``/``TimeoutError`` subclasses merely
shadow the builtin names — so the original all-builtins tuple could never
match a redis failure. A Redis blip therefore escaped ``is_cancelled``, which
propagated through ``raise_if_cancelled`` into the running job and 500'd
``DELETE /jobs/{id}``: a cross-pod cancel became an unkillable job.

The same trap then repeated one line further down, against the *database*: the
DB-fallback handler caught ``(OSError, RuntimeError, ValueError)``, and
SQLAlchemy and psycopg raise none of those. A Redis outage plus one pgbouncer
``QueuePool`` timeout therefore escaped ``is_cancelled`` into the orchestrator's
``except Exception``, which marked a healthy job FAILED -- at every cancel
checkpoint, for the whole outage. So the DB handler now catches ``Exception``:
the question it asks is "could any authority answer?", and an enumerated tuple
can only ever be a guess about somebody else's exception hierarchy.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Protocol

from redis.exceptions import RedisError

logger = logging.getLogger(__name__)

# Long enough to outlive any realistic generation/validation run so a flag set
# just before a checkpoint is still visible when the task reaches it, short
# enough that a crashed-before-clear flag self-evicts and cannot wedge a later
# job that happens to reuse the (uuid7) id space.
_CANCEL_TTL_SECONDS = 3600


class _RedisLike(Protocol):
    """The subset of ``src.infra.redis_client.RedisClient`` this helper uses."""

    async def set(self, key: str, value: object, ttl: int | None = None) -> bool: ...

    async def exists(self, key: str) -> bool: ...

    async def delete(self, key: str) -> bool: ...


class _JobRepoLike(Protocol):
    """The subset of ``src.repository.job_repository.JobRepository`` used here."""

    async def get(self, job_id: str, *, tenant_id: str) -> object | None: ...


def job_row_cancel_fallback(job_repo: _JobRepoLike) -> Callable[..., Awaitable[bool]]:
    """Build the DB authority used to resolve an UNKNOWN cancel probe.

    Lives here rather than inline in ``asgi.py`` so the wiring is unit-testable
    -- an unwired fallback makes the whole tri-state inert in production while
    every ``CancelFlags`` test still passes.
    """

    async def _row_says_cancelled(job_id: str, *, tenant_id: str) -> bool:
        job = await job_repo.get(job_id, tenant_id=tenant_id)
        return job is not None and str(getattr(job, "status", "")) == "cancelled"

    return _row_says_cancelled


class JobCancelledError(Exception):
    """Raised inside a running job when a cross-pod cancel flag is observed.

    Distinct from :class:`asyncio.CancelledError` (in-process task cancel) so
    the orchestrator can tell a cooperative cancel apart from a genuine
    failure and mark the job ``cancelled`` rather than ``failed``.
    """


class CancelFlags:
    """Best-effort cross-replica cancel flags backed by Redis."""

    def __init__(
        self,
        redis_client: _RedisLike | None,
        *,
        db_fallback: Callable[..., Awaitable[bool]] | None = None,
    ) -> None:
        self._redis = redis_client
        # Resolves an UNKNOWN probe against the durable job row. Optional so
        # unit tests and Redis-less dev keep working unchanged.
        self._db_fallback = db_fallback

    @staticmethod
    def _key(tenant_id: str, job_id: str) -> str:
        return f"content:scripts:cancel:{tenant_id}:{job_id}"

    async def request_cancel(self, job_id: str, *, tenant_id: str) -> None:
        """Set the cancel flag so a task on any replica stops at its next checkpoint."""
        if self._redis is None:
            return
        try:
            await self._redis.set(self._key(tenant_id, job_id), "1", ttl=_CANCEL_TTL_SECONDS)
        except (OSError, RedisError, RuntimeError) as exc:
            # A failed flag-set only loses the cross-pod stop; the DB cancel +
            # local task cancel still ran. Never let it fail the HTTP request.
            logger.warning("Failed to set cancel flag for job %s: %s", job_id, exc)

    async def probe(self, job_id: str, *, tenant_id: str) -> bool | None:
        """Tri-state read of the cancel flag.

        ``True``/``False`` are answers; ``None`` means UNKNOWN -- Redis could
        not be read, so nothing at all is known about this job's cancel state.
        A *missing* Redis client is a definite ``False``: that is a deliberate
        single-replica configuration, not a measurement gap.
        """
        if self._redis is None:
            return False
        try:
            return bool(await self._redis.exists(self._key(tenant_id, job_id)))
        except (OSError, RedisError, RuntimeError) as exc:
            logger.warning("Failed to read cancel flag for job %s: %s", job_id, exc)
            return None

    async def is_cancelled(self, job_id: str, *, tenant_id: str) -> bool:
        """Return True if a cancel has been requested for this job.

        An UNKNOWN probe is resolved against the DB row (the authority: it is
        written before the flag). If that cannot answer either, return ``False``
        so a Redis blip never kills a live job -- but say so at WARNING, because
        an unresolvable cancel state is a real gap, not a quiet "no".
        """
        state = await self.probe(job_id, tenant_id=tenant_id)
        if state is not None:
            return state
        if self._db_fallback is None:
            # Not reachable in production, and deliberately not logged: the only
            # two construction sites are asgi.py, which always passes a fallback,
            # and ScriptOrchestrator's ``CancelFlags(None)`` default, where the
            # missing Redis client makes probe() return a definite False above.
            # A WARNING here would be a branch that can only ever fire in a test.
            return False
        try:
            return bool(await self._db_fallback(job_id, tenant_id=tenant_id))
        except Exception as exc:  # noqa: BLE001 — no authority answered; see below
            # Deliberately broad, and NOT narrowed to SQLAlchemyError. Two
            # reasons. (1) ``db_fallback`` is an injected callable -- this module
            # is decoupled from any particular database library, so enumerating
            # one library's error types re-couples it to a dependency it does not
            # import, and psycopg.OperationalError is not a SQLAlchemyError, so
            # even that narrowing still leaks. (2) The question this handler asks
            # is "could ANY authority answer?", and every failure means no. The
            # cost of guessing wrong is not a missed log line: the error escapes
            # into the orchestrator's own ``except Exception``, which marks a
            # perfectly healthy job FAILED -- on every cancel checkpoint, for the
            # whole duration of a Redis outage. That is worse than the missed
            # cancel this class exists to fix.
            #
            # ``asyncio.CancelledError`` derives from BaseException, not
            # Exception, so a genuine task cancellation still propagates.
            logger.warning(
                "Cancel state for job %s is UNKNOWN: Redis unreadable and the "
                "DB authority also failed (%s: %s) — continuing the job",
                job_id,
                type(exc).__name__,
                exc,
            )
            return False

    async def clear(self, job_id: str, *, tenant_id: str) -> None:
        """Remove the cancel flag once a job has finished/observed it."""
        if self._redis is None:
            return
        try:
            await self._redis.delete(self._key(tenant_id, job_id))
        except (OSError, RedisError, RuntimeError) as exc:
            logger.warning("Failed to clear cancel flag for job %s: %s", job_id, exc)

    async def raise_if_cancelled(self, job_id: str, *, tenant_id: str) -> None:
        """Raise :class:`JobCancelledError` if a cancel has been requested."""
        if await self.is_cancelled(job_id, tenant_id=tenant_id):
            raise JobCancelledError(job_id)
