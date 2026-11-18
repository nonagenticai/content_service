"""The stale-job reconciler must also reclaim rows stranded in ``pending``.

A job row is COMMITTED as ``pending`` by the POST handler and only then handed to
``TaskManager.submit``; ``mark_processing`` is the first thing the background
coroutine does. ``src/infra/task_manager.py`` has two reachable paths where that
coroutine never gets there — ``_run_with_semaphore`` does ``coro.close(); return``
for every job still queued behind the semaphore once ``_shutdown_event`` is set
(a rolling deploy silently discards the whole queued tail of a batch), and
``shutdown()`` cancels stragglers still blocked on the semaphore. Neither runs an
``except`` block, so nothing ever writes the row again.

``reconcile_stale_jobs`` swept only ``status = 'processing'``, so such a row was
reclaimed by NOTHING: ``GET /jobs/{id}`` answered ``pending`` for the life of the
database and the batch never reached a terminal status.

The new second interval is load-bearing rather than padding. Unlike
``processing``, ``pending`` is a LEGITIMATE queued state — ``TaskManager`` bounds
concurrency at ``generator.parallel_workers`` — so reusing the 30-minute
``older_than`` here would fail out live queued work. The runtime passes
``reconciler_pending_stale_after_seconds`` (default 6h).

The one-argument overload is DROPPED first: ``CREATE OR REPLACE`` cannot change a
function's argument list, so replacing in place would leave both overloads
resident and the old one would keep answering the old call shape.

Revision ID: 007_reconcile_pending_jobs
Revises: 006_tenant_id_uuid
"""

from alembic import op

revision = "007_reconcile_pending_jobs"
down_revision = "006_tenant_id_uuid"
branch_labels = None
depends_on = None

# ⛔ FROZEN 2026-09-08 (added by migration 008) — 007's payload, verbatim as it was
# when 007 shipped. It used to be imported live from ``src.services.reconciler``,
# which made this HISTORICAL migration emit TODAY's function body. Migration 008
# adds a ``script_batches`` sweep to that module, and that sweep belongs to 008 —
# it also writes ``script_batches.error``, a column 008 creates and 007 must not
# assume. Same freeze, same reason, as migration 004.
_UPGRADE_STATEMENTS_007 = [
    """DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval)""",
    """
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

  RETURN v_total;
END;
$fn$
""",
    """REVOKE EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) FROM PUBLIC""",
    """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_content_app') THEN
    GRANT EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) TO w2v_content_app;
  END IF;
END
$do$
""",
]

# 004's frozen body, restored verbatim on downgrade.
_RECREATE_ONE_ARG_FN = """
CREATE OR REPLACE FUNCTION reconcile_stale_jobs(older_than interval)
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

  RETURN v_total;
END;
$fn$
"""

# 004's grant, restored alongside it. ``downgrade()`` DROPs the two-argument function
# and CREATEs the one-argument one fresh, so the recreated function starts with an
# EMPTY ACL. Without this, a downgrade would leave prod's runtime role
# ``w2v_content_app`` with no EXECUTE and the (fail-open) reconciler would log a
# permission error every sweep forever instead of reclaiming anything. Guarded on
# ``pg_roles`` for the same reason as the forward grant: the role exists only in prod.
_REGRANT_ONE_ARG_FN = """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_content_app') THEN
    GRANT EXECUTE ON FUNCTION reconcile_stale_jobs(interval) TO w2v_content_app;
  END IF;
END
$do$
"""


def upgrade() -> None:
    for stmt in _UPGRADE_STATEMENTS_007:
        op.execute(stmt)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval, interval)")
    op.execute(_RECREATE_ONE_ARG_FN)
    op.execute("REVOKE EXECUTE ON FUNCTION reconcile_stale_jobs(interval) FROM PUBLIC")
    op.execute(_REGRANT_ONE_ARG_FN)
