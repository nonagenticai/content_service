"""Give ``script_batches`` a reaper, and backfill the rows it was never given one for.

``reconcile_stale_jobs`` swept ``storyline_jobs``, ``narration_jobs`` and
``script_jobs`` in both a ``processing`` and a ``pending`` pass. ``script_batches``
appeared in NEITHER — it was not in the reaper at all, so its non-terminal rows
were predicate-unreachable.

A batch is not a job. Nothing polls it; the only thing that ever writes its status
again is ``BatchRepository.update_status``, which recomputes the status from the
child ``script_jobs`` rows and is called exclusively from the per-job background
path (``scripts_routes._process_job``). ``ScriptOrchestrator.create_batch`` COMMITS
the batch row FIRST and only then loops creating the part jobs, so a worker lost
between those two steps leaves a batch with ZERO children — and a childless batch
has no per-job path, hence nothing that will ever call ``update_status`` again.

Measured in prod 2026-09: ``script_batches`` held exactly two statuses — ``partial``
(13) and ``pending`` (12). All twelve ``pending`` rows were created on 2026-05-06
with ``total_parts = 6`` and have ZERO child ``script_jobs`` rows. Permanently
stranded, in exactly the shape above.

TERMINAL STATE. No new status is invented here. The vocabulary ``BatchRepository``
can produce is {pending, in_progress, completed, partial}; ``completed`` would be a
lie about work that never happened, and nothing in that vocabulary means
"cancelled" — nor would it be honest, since the user cancelled nothing. ``partial``
is the only member meaning "terminal, and not everything succeeded", and it is
literally true of a batch that ended with 0 of N parts. What ``partial`` alone
cannot say is WHY, so this migration adds ``script_batches.error`` (mirroring
``storyline_jobs.error`` / ``narration_jobs.error`` / ``script_jobs.error_message``)
and the reaper writes a per-row reason naming the actual mechanism and the counts
that distinguish the two abandonment shapes.

CUTOFF. The sweep reuses ``pending_older_than`` (default 6h), not ``older_than``
(30 min), for the same load-bearing reason the job-level ``pending`` pass does: a
batch is legitimately non-terminal for as long as its parts sit queued behind
``generator.parallel_workers``. 6h is far beyond any real batch, so live work is
not reaped.

BACKFILL. Deliberately NOT one giant UPDATE — it loops ``_CHUNK`` rows at a time,
ordered by primary key, ``FOR UPDATE SKIP LOCKED`` so a concurrently-running
reconciler sweep cannot deadlock it. Idempotent by construction: a row it touches
leaves the non-terminal set, so a re-run selects nothing. It spans many tenants and
reads every value it writes off the PERSISTED row (that row's own ``tenant_id``,
``total_parts``, and its own children) — there is no ambient identity here and none
is fabricated.

Revision ID: 008_reconcile_script_batches
Revises: 007_reconcile_pending_jobs
"""

import sqlalchemy as sa
from alembic import op

from src.services.reconciler import (
    SCRIPT_BATCH_BACKFILL_SQL,
    downgrade_statements,
    upgrade_statements,
)

revision = "008_reconcile_script_batches"
down_revision = "007_reconcile_pending_jobs"
branch_labels = None
depends_on = None

# Must match ``reconciler.StaleJobReconciler``'s runtime default
# (``reconciler_pending_stale_after_seconds`` = 6h). Expressed here as a literal so
# the historical backfill keeps reaping exactly the population it was written for
# even if the runtime default is later retuned.
_BACKFILL_CUTOFF = "6 hours"
_CHUNK = 500
# A stranded batch is a bug, not a workload: prod held 12. This bound only exists so
# a pathological table cannot turn a migration into an unbounded loop; hitting it
# means the next run continues where this one stopped (the backfill is re-runnable).
_MAX_CHUNKS = 2000

# 007's function body, frozen: ``downgrade()`` must restore the reconciler exactly as
# 007 left it, WITHOUT the script_batches pass (which writes a column this migration
# is about to drop).
_RECREATE_FN_007 = """
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
"""


def upgrade() -> None:
    # 1. The column the reason is recorded in. ``IF NOT EXISTS`` so a re-run (or a
    #    hand-applied hotfix) is a no-op rather than a DuplicateColumn abort.
    op.execute("ALTER TABLE script_batches ADD COLUMN IF NOT EXISTS error TEXT")

    # 2. The reaper itself, single-sourced from src/services/reconciler.py. Same
    #    two-argument signature as 007, so CREATE OR REPLACE replaces in place and
    #    no overload is left resident.
    for stmt in upgrade_statements():
        op.execute(stmt)

    # 3. Backfill the already-stranded population, in chunks.
    bind = op.get_bind()
    reaped = 0
    for _ in range(_MAX_CHUNKS):
        result = bind.execute(
            sa.text(SCRIPT_BATCH_BACKFILL_SQL),
            {"cutoff": _BACKFILL_CUTOFF, "chunk": _CHUNK},
        )
        if result.rowcount <= 0:
            break
        reaped += result.rowcount
    print(f"008_reconcile_script_batches: reaped {reaped} stranded script_batches row(s)")


def downgrade() -> None:
    # Restore 007's reconciler first: it must stop writing script_batches.error
    # BEFORE the column disappears.
    for stmt in downgrade_statements():
        op.execute(stmt)
    op.execute(_RECREATE_FN_007)
    op.execute("REVOKE EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) FROM PUBLIC")
    op.execute(
        """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_content_app') THEN
    GRANT EXECUTE ON FUNCTION reconcile_stale_jobs(interval, interval) TO w2v_content_app;
  END IF;
END
$do$
"""
    )
    # The reaped rows are NOT un-reaped: 'partial' is a legitimate member of the
    # status vocabulary and the pre-migration 'pending' was itself the bug. Only the
    # column carrying the reason goes.
    op.execute("ALTER TABLE script_batches DROP COLUMN IF EXISTS error")
