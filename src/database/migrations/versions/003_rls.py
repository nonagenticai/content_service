"""Phase D RLS — enable row-level security on the tenant-scoped tables.

Enables RLS + a ``tenant_isolation`` policy on every tenant-scoped table, keyed on
the per-request GUC ``app.current_tenant``. ENABLE-not-FORCE → inert until the
runtime connects as the restricted non-owner role ``w2v_content_app`` with
``TENANT_RLS_MODE=enforce``. No cross-tenant background loops in this service, so no
SECURITY DEFINER resolvers are needed. Fully reversible.

⚠️ PINNED — DO NOT "SIMPLIFY" THIS BACK TO
``w2v_common.database.rls.upgrade_statements(Base.metadata)``.

This migration used to render its DDL from LIVE model metadata. A migration is
immutable history: the moment the model changed (``tenant_id`` varchar → ``sa.Uuid``
in ``006_tenant_id_uuid``), this OLD revision started emitting a ``::uuid`` cast
against a column that is still ``character varying(255)`` at this point in the
chain, and a build from an EMPTY database died right here with::

    (psycopg.errors.UndefinedFunction) operator does not exist: character varying = uuid

Every static gate (ruff, ty, ``alembic heads``) is green and blind to it — only
``alembic upgrade head`` on an empty DB sees it. The same trap fires again the day a
sixth ``tenant_id`` table is added (this revision would emit DDL for a table that
does not exist yet).

So the DDL below is the LITERAL text the helper rendered at THIS revision's era:
five NOT NULL ``tenant_id`` varchar columns → the strict text-comparison predicate
``(tenant_id)::text = NULLIF(current_setting('app.current_tenant'::text, true), ''::text)``
(as Postgres stores it), written here in its source form. ``006_tenant_id_uuid``
re-creates these policies with the ``::uuid`` cast after the type flip.

Revision ID: 003_rls
Revises: 002_add_script_artifact_id
"""

from alembic import op

revision = "003_rls"
down_revision = "002_add_script_artifact_id"
branch_labels = None
depends_on = None

# The five tenant-scoped tables as they existed at THIS revision (created in
# 001_initial). Pinned in time — never derive this from live metadata.
TABLES = (
    "narration_jobs",
    "script_batches",
    "script_jobs",
    "script_usage",
    "storyline_jobs",
)

POLICY_NAME = "tenant_isolation"

# Policy names w2v_common may render (strict + the nullable "global-or-own"
# variants), so the downgrade drop is exhaustive regardless of shape applied.
_ALL_POLICY_NAMES = (
    POLICY_NAME,
    f"{POLICY_NAME}_read",
    f"{POLICY_NAME}_ins",
    f"{POLICY_NAME}_upd",
    f"{POLICY_NAME}_del",
)

# Era-correct predicate: tenant_id is character varying(255) here, so the GUC
# (always text) is compared text-to-text. Casting it to uuid would raise
# ``operator does not exist: character varying = uuid``. NULLIF folds the empty
# string a pooled PgBouncer connection leaves behind back to NULL → predicate
# false → 0 rows, instead of raising on ''::uuid.
_GUC = "NULLIF(current_setting('app.current_tenant', true), '')"
_PREDICATE = f"tenant_id = {_GUC}"


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        op.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} USING ({_PREDICATE}) WITH CHECK ({_PREDICATE})"
        )


def downgrade() -> None:
    for table in TABLES:
        for policy in _ALL_POLICY_NAMES:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
