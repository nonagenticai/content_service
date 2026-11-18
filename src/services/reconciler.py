"""Durable stale-job reconciler — a crash backstop for stuck ``processing`` rows.

Problem: if a worker pod is SIGKILLed / OOMed mid-job, the corresponding
``storyline_jobs`` / ``narration_jobs`` / ``script_jobs`` row is left at
``status='processing'`` forever — no in-band ``except`` block runs because the
process never gets to run one. The in-band httpx hardening (separate change)
covers *transient* errors; this periodic sweeper covers *crashes*.

Why a SQL function and not a plain ``UPDATE``: in prod the runtime connects as
the restricted, non-owner role ``w2v_content_app`` under
``TENANT_RLS_MODE=enforce``. The reconciler has no tenant context, so a plain
cross-tenant ``UPDATE storyline_jobs ...`` would be RLS-scoped to the (empty)
``app.current_tenant`` GUC and touch 0 rows. ``reconcile_stale_jobs`` is a
``SECURITY DEFINER`` function owned by the table owner (``w2v_service`` in prod,
the table-owning CI role in tests) so its body bypasses ENABLE-not-FORCE RLS and
sees every tenant's rows. EXECUTE is revoked from PUBLIC and granted to
``w2v_content_app``.

The DDL lives here (next to the loop that calls the function) so the alembic
migration and the test schema-bootstrap share one source of truth for the
function name + body.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import get_db_session

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SECURITY DEFINER function DDL — single source for migration 004 + tests.
# ---------------------------------------------------------------------------
#
# Per-table timestamp columns (verified against src/database/models.py):
#   * storyline_jobs / narration_jobs: error column is ``error``; the
#     "entered processing" proxy is ``updated_at`` (server onupdate=now()).
#   * script_jobs: error column is ``error_message``; processing start is
#     ``started_at`` (fall back to ``created_at`` if a row never recorded it).
RECONCILE_FN_NAME = "reconcile_stale_jobs"

# ---------------------------------------------------------------------------
# script_batches — the table the reaper did not cover at all.
# ---------------------------------------------------------------------------
#
# ``BatchRepository`` computes a batch's status from its child ``script_jobs``
# rows, and it is only ever called from the per-job background path
# (``scripts_routes._process_job``). ``ScriptOrchestrator.create_batch`` COMMITS
# the batch row FIRST and only then loops creating the part jobs, so a worker
# lost between those two steps leaves a batch with ZERO children — and a batch
# with zero children has nothing that will ever call ``update_status`` again.
#
# Measured in prod 2026-09: ``script_batches`` held exactly two statuses,
# ``partial`` (13) and ``pending`` (12). All twelve ``pending`` rows were created
# on the same day with ``total_parts = 6`` and have ZERO child ``script_jobs``
# rows. They were permanently stranded: no pass of ``reconcile_stale_jobs``
# named ``script_batches``, so the predicate could not reach them.
#
# TERMINAL STATE. The vocabulary ``BatchRepository`` can produce is exactly
# {pending, in_progress, completed, partial} (``create`` writes 'pending';
# ``get_status``/``update_status`` compute the other three). ``completed`` would
# be a lie about work that never happened, and there is no 'cancelled' in this
# vocabulary — nor would one be honest, because the user cancelled nothing. The
# only member meaning "terminal, and not everything succeeded" is ``partial``,
# which is literally true of a batch that finished with 0 of N parts. Rather
# than invent a status, the reaper writes ``partial`` and records WHAT ACTUALLY
# HAPPENED in the new ``script_batches.error`` column (mirroring
# ``storyline_jobs.error`` / ``script_jobs.error_message``), so a reconciled
# batch is never confused with one whose parts genuinely half-failed.
SCRIPT_BATCH_TERMINAL_STATUSES = frozenset({"completed", "partial"})
SCRIPT_BATCH_NON_TERMINAL_STATUSES = frozenset({"pending", "in_progress"})
SCRIPT_BATCH_REAPED_STATUS = "partial"

# The IN-list the sweep below uses, built from the constant above so the
# predicate and the invariant test cannot drift apart.
_BATCH_NON_TERMINAL_IN_LIST = ", ".join(
    "'" + status + "'" for status in sorted(SCRIPT_BATCH_NON_TERMINAL_STATUSES)
)

# Per-row reason, assembled from the PERSISTED row (``b``) and its persisted
# children — never from ambient context. It names the mechanism, not a generic
# "failed": which of the two abandonment shapes this row is, and the counts that
# distinguish them.
_BATCH_REAP_ERROR_EXPR = """
           'reconciled: batch abandoned before it reached a terminal status; '
        || (SELECT count(*) FROM script_jobs j
             WHERE j.batch_id = b.batch_id AND j.tenant_id = b.tenant_id)::text
        || ' of ' || b.total_parts::text || ' part job rows exist ('
        || (SELECT count(*) FROM script_jobs j
             WHERE j.batch_id = b.batch_id AND j.tenant_id = b.tenant_id
               AND j.status = 'completed')::text
        || ' completed). '
        || CASE WHEN NOT EXISTS (SELECT 1 FROM script_jobs j
                                  WHERE j.batch_id = b.batch_id
                                    AND j.tenant_id = b.tenant_id)
                THEN 'The batch row committed but its part jobs were never created: '
                     || 'ScriptOrchestrator.create_batch writes the batch row first and '
                     || 'only then loops creating part jobs, so a worker lost between '
                     || 'those two steps leaves a childless batch that nothing ever '
                     || 'recomputes.'
                ELSE 'Part jobs exist but no terminal recount ever ran: '
                     || 'BatchRepository.update_status is called only from the per-job '
                     || 'background path, which never reached it.'
           END
"""


def _render(template: str) -> str:
    """Fill the ``__PLACEHOLDER__`` slots in a SQL template.

    ``str.replace`` rather than an f-string on purpose. Every substituted value is
    a module-level literal (a status name, a list of status names, a fixed SQL
    expression) — none of it is user input — but an f-string here trips ruff's
    S608 (bandit ``hardcoded_sql_expressions``), and silencing S608 on a query
    builder is exactly the wrong habit to normalise in this repo. Keeping the
    substitution non-f-string keeps the rule armed for real query construction.
    """
    return (
        template.replace("__REAPED_STATUS__", SCRIPT_BATCH_REAPED_STATUS)
        .replace("__NON_TERMINAL__", _BATCH_NON_TERMINAL_IN_LIST)
        .replace("__ERROR_EXPR__", _BATCH_REAP_ERROR_EXPR)
    )


# The sweep itself. It reuses ``pending_older_than`` (default 6h) rather than
# ``older_than`` (30 min) for the same load-bearing reason the job-level pending
# pass does: a batch legitimately sits non-terminal for as long as its parts are
# queued behind ``generator.parallel_workers``. 6h is far beyond any real batch.
_BATCH_REAP_SQL = _render("""
  UPDATE script_batches b
     SET status = '__REAPED_STATUS__',
         completed_at = now(),
         error = __ERROR_EXPR__
   WHERE b.status IN (__NON_TERMINAL__)
     AND b.created_at < now() - pending_older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;
""")

# Backfill payload for migration 008. It is deliberately NOT one giant UPDATE:
# ``:chunk`` rows at a time, ordered by primary key, ``SKIP LOCKED`` so a
# concurrently-running reconciler sweep cannot deadlock it. It is idempotent by
# construction — a row it touches leaves the non-terminal set, so re-running it
# selects nothing. Every value written comes from the PERSISTED row (its own
# ``tenant_id``, ``total_parts`` and children); the migration has no tenant
# context and fabricates none.
SCRIPT_BATCH_BACKFILL_SQL = _render("""
WITH victims AS (
    SELECT b0.id
      FROM script_batches b0
     WHERE b0.status IN (__NON_TERMINAL__)
       AND b0.created_at < now() - CAST(:cutoff AS interval)
     ORDER BY b0.id
     LIMIT :chunk
     FOR UPDATE SKIP LOCKED
)
UPDATE script_batches b
   SET status = '__REAPED_STATUS__',
       completed_at = now(),
       error = __ERROR_EXPR__
  FROM victims v
 WHERE b.id = v.id
""")

_CREATE_RECONCILE_FN = """
CREATE OR REPLACE FUNCTION reconcile_stale_jobs(
    older_than interval,
    pending_older_than interval
  )
  RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path FROM CURRENT AS $fn$
DECLARE
  v_total integer := 0;
  v_count integer;
BEGIN
  UPDATE storyline_jobs
     SET status = 'failed',
         error = 'reconciled: stale processing job',
         updated_at = now()
   WHERE status = 'processing'
     AND updated_at < now() - older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  UPDATE narration_jobs
     SET status = 'failed',
         error = 'reconciled: stale processing job',
         updated_at = now()
   WHERE status = 'processing'
     AND updated_at < now() - older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  UPDATE script_jobs
     SET status = 'failed',
         error_message = 'reconciled: stale processing job',
         completed_at = now()
   WHERE status = 'processing'
     AND COALESCE(started_at, created_at) < now() - older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  -- ⚠️ SECOND PASS, on 'pending', with its OWN far longer cutoff.
  --
  -- A row is COMMITTED as 'pending' by the POST handler and only then handed to
  -- TaskManager.submit; JobRepository.mark_processing is the first thing the
  -- background coroutine does. Two reachable ways the coroutine never gets
  -- there, both in src/infra/task_manager.py:
  --   * shutdown: _run_with_semaphore does `coro.close(); return` for every job
  --     still queued behind the semaphore — on a rolling deploy that silently
  --     discards the whole queued tail of a batch;
  --   * a straggler cancelled by shutdown() while blocked on the semaphore.
  -- Neither runs an except block, so nothing ever writes the row again. With the
  -- passes above scoped to 'processing', such a row was reclaimed by NOTHING:
  -- GET /jobs/{id} answered 'pending' for the life of the database and the batch
  -- never reached a terminal status.
  --
  -- The separate, much longer cutoff is load-bearing, not padding: unlike
  -- 'processing', 'pending' is a LEGITIMATE queued state — parallel_workers
  -- bounds concurrency, so a large batch can hold real work in 'pending' for a
  -- long time. Reusing `older_than` (30 min) here would fail out live queued
  -- jobs. `pending_older_than` defaults to 6h, far beyond any real queue wait.
  UPDATE storyline_jobs
     SET status = 'failed',
         error = 'reconciled: never started (worker lost the job)',
         updated_at = now()
   WHERE status = 'pending'
     AND updated_at < now() - pending_older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  UPDATE narration_jobs
     SET status = 'failed',
         error = 'reconciled: never started (worker lost the job)',
         updated_at = now()
   WHERE status = 'pending'
     AND updated_at < now() - pending_older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  UPDATE script_jobs
     SET status = 'failed',
         error_message = 'reconciled: never started (worker lost the job)',
         completed_at = now()
   WHERE status = 'pending'
     AND created_at < now() - pending_older_than;
  GET DIAGNOSTICS v_count = ROW_COUNT;
  v_total := v_total + v_count;

  -- ⚠️ THIRD PASS, on script_batches — the table that was in NO pass at all.
  --
  -- A batch is not a job: nothing polls it, and the only thing that ever writes
  -- its status again is BatchRepository.update_status, reached exclusively from
  -- the per-job background path. A batch whose children were never created has
  -- no per-job path, so it was reclaimed by NOTHING and answered a non-terminal
  -- status for the life of the database. See the module docstring block above
  -- _BATCH_REAP_SQL for why 'partial' is the honest terminal state and why the
  -- reason is recorded in the new script_batches.error column.
__BATCH_REAP__
  RETURN v_total;
END;
$fn$
""".replace("__BATCH_REAP__", _BATCH_REAP_SQL)

_REVOKE_RECONCILE_FN = (
    "REVOKE EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) FROM PUBLIC"
)

# Guarded so the migration is CI-safe: the ``w2v_content_app`` role only exists
# in prod (created out-of-band in the Stage-A role cutover), not in CI/tests.
#
# The signature here MUST be the TWO-argument one created above. ``upgrade_statements()``
# runs ``DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval)`` FIRST, so by the time
# this GRANT executes the one-argument overload is gone; naming it raises
# ``UndefinedFunction`` and aborts the whole migration transaction. That is not
# hypothetical -- it shipped as ``(interval)`` and crash-looped the prod
# ``run-migrations`` initContainer. CI cannot catch it, because ``w2v_content_app``
# does not exist there, so the guard is FALSE and the bad statement never runs; the
# signatures are asserted directly in ``tests/test_reconciler_db.py`` instead, which
# needs no role.
_GRANT_RECONCILE_FN = """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_content_app') THEN
    GRANT EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) TO w2v_content_app;
  END IF;
END
$do$
"""

_DROP_RECONCILE_FN = "DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval, interval)"


def create_reconcile_function_statements() -> list[str]:
    """DDL to (idempotently) create the function only — used by tests."""
    return [_CREATE_RECONCILE_FN]


DROP_LEGACY_ONE_ARG_FN = "DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval)"


def upgrade_statements() -> list[str]:
    """Full forward DDL for the alembic migration: create + lock down + grant.

    The legacy ONE-argument overload is dropped first. ``CREATE OR REPLACE``
    cannot change a function's argument list — it would leave BOTH overloads
    resident, and the old one silently keeps sweeping only ``processing``.
    """
    return [
        DROP_LEGACY_ONE_ARG_FN,
        _CREATE_RECONCILE_FN,
        _REVOKE_RECONCILE_FN,
        _GRANT_RECONCILE_FN,
    ]


def downgrade_statements() -> list[str]:
    """Reverse DDL for the alembic migration: drop the function."""
    return [_DROP_RECONCILE_FN]


# ---------------------------------------------------------------------------
# Periodic reconciler loop
# ---------------------------------------------------------------------------

SessionProvider = Callable[[], AbstractAsyncContextManager[AsyncSession]]


class StaleJobReconciler:
    """Periodically marks long-stuck ``processing`` jobs as ``failed``.

    Fail-open by design: a sweep that raises (DB blip, missing function in an
    environment that never ran migration 004, ...) is logged and swallowed so
    the loop keeps running and never takes the process down.
    """

    def __init__(
        self,
        *,
        interval_seconds: float,
        stale_after_seconds: float,
        pending_stale_after_seconds: float,
        session_provider: SessionProvider = get_db_session,
    ) -> None:
        self._interval_seconds = interval_seconds
        self._stale_after_seconds = stale_after_seconds
        self._pending_stale_after_seconds = pending_stale_after_seconds
        self._session_provider = session_provider
        self._task: asyncio.Task[None] | None = None

    async def reconcile_once(self) -> int | None:
        """Run a single sweep. Returns rows reconciled, or ``None`` on error."""
        try:
            async with self._session_provider() as session:
                result = await session.execute(
                    text(
                        "SELECT reconcile_stale_jobs("
                        "make_interval(secs => :secs), make_interval(secs => :pending_secs))"
                    ),
                    {
                        "secs": self._stale_after_seconds,
                        "pending_secs": self._pending_stale_after_seconds,
                    },
                )
                count = int(result.scalar_one())
                await session.commit()
                return count
        except Exception:
            logger.exception("Stale-job reconciler sweep failed; continuing")
            return None

    async def _loop(self) -> None:
        logger.info(
            "Stale-job reconciler started: interval=%ss, stale_after=%ss",
            self._interval_seconds,
            self._stale_after_seconds,
        )
        while True:
            try:
                await asyncio.sleep(self._interval_seconds)
                count = await self.reconcile_once()
                if count:
                    logger.warning(
                        "Stale-job reconciler marked %d stuck job(s) failed",
                        count,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Stale-job reconciler loop iteration failed")

    def start(self) -> None:
        """Spawn the background loop task (no-op if already running)."""
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop(), name="stale-job-reconciler")

    async def stop(self) -> None:
        """Cancel the loop task and await its teardown."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info("Stale-job reconciler stopped")
