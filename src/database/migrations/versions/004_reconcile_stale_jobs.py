"""Fold the stale-job reconciler SECURITY DEFINER function into a migration.

``reconcile_stale_jobs`` was first applied out-of-band as ``w2v_service`` during the
content_service hardening sweep (PR#52), so prod already carries it. The DDL is single-
sourced in ``src/services/reconciler.py`` (next to the loop that calls it); this migration
just routes that same source through alembic so a DB rebuilt purely from migrations also
gets the function. ``CREATE OR REPLACE`` makes the upgrade a safe no-op on the existing
prod function.

Why this is safe to run in alembic now: the ``run-migrations`` initContainer connects as
the table OWNER ``w2v_service`` (live-verified 2026-06-18 — every w2v init uses the
privileged ``<svc>-secrets/DATABASE_URL``; only the runtime uses the restricted
``w2v_content_app`` via ``content-runtime-secret``), so ``CREATE FUNCTION`` succeeds. The
``GRANT EXECUTE ... TO w2v_content_app`` is wrapped in a ``DO`` block guarded on
``pg_roles`` so CI — which has no ``w2v_content_app`` role — skips it instead of erroring.

Revision ID: 004_reconcile_stale_jobs
Revises: 003_rls

⛔ FROZEN 2026-09-04 — this migration used to import the DDL from
``src.services.reconciler``, which made a historical migration emit TODAY's
function body. Migration 007 changes the signature to two intervals and adds the
``pending`` sweep; that belongs to 007. A migration is a historical record and
must not move when the module beside it moves.
"""

from alembic import op


revision = "004_reconcile_stale_jobs"
down_revision = "003_rls"
branch_labels = None
depends_on = None


_CREATE_FN_004 = """
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

_REVOKE_004 = "REVOKE EXECUTE ON FUNCTION reconcile_stale_jobs(interval) FROM PUBLIC"

_GRANT_004 = """
DO $do$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'w2v_content_app') THEN
    GRANT EXECUTE ON FUNCTION reconcile_stale_jobs(interval) TO w2v_content_app;
  END IF;
END
$do$
"""

_DROP_004 = "DROP FUNCTION IF EXISTS reconcile_stale_jobs(interval)"


def upgrade() -> None:
    for stmt in (_CREATE_FN_004, _REVOKE_004, _GRANT_004):
        op.execute(stmt)


def downgrade() -> None:
    op.execute(_DROP_004)
