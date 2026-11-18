"""Fleet ruling: ``tenant_id`` is a real ``uuid``, never text, never a sentinel.

All five tenant-scoped tables carried ``tenant_id`` as ``character varying(255)``.
The only non-uuid value in prod was the literal ``n8n-dev`` — which is **not** a
sentinel, it is a REGISTERED TENANT NAME whose uuid is
``019de842-c92a-7e37-8fdd-eb0d8c5396f9`` (verified live in ``licenses.tenants``
2026-08-26). So this is a name -> uuid backfill plus a type flip, not a product
decision.

Order is load-bearing:

1. **Drop the RLS policies first.** Postgres refuses ``ALTER TABLE ... ALTER COLUMN
   ... TYPE`` on a column referenced by a policy predicate
   (``cannot alter type of a column used in a policy definition``). The policies
   created by ``003_rls`` compare ``tenant_id`` to the ``app.current_tenant`` GUC as
   **text**; leaving them in place would abort the migration outright.
2. **Backfill while the column is still text.** ``'n8n-dev'`` -> the tenant uuid.
   This cannot be done after the cast (``'n8n-dev'::uuid`` raises).
3. **Guard, fail loud.** A ``DO`` block counts every remaining non-uuid, non-NULL
   value and ``RAISE EXCEPTION``s with the count. Deliberately NOT a
   ``WHERE tenant_id ~ ...`` filter on the ALTER: a filter would quietly leave
   unconvertible rows behind (or drop them), and silent data loss on a tenant key
   is worse than a failed migration. If this raises, someone wrote a new non-uuid
   tenant value and it must be triaged by hand, not swallowed.
4. **Cast** each column to ``uuid``. Nullability is preserved by ``ALTER COLUMN
   TYPE``; indexes and ``uq_script_usage_tenant_date`` are rebuilt automatically.
5. **Recreate the policies with a ``::uuid`` cast on the GUC.** The GUC is always
   text; against a ``uuid`` column the old text predicate would raise
   ``operator does not exist: uuid = text``. Getting this wrong returns ZERO rows
   (or 500s) from the pod after deploy, so it is re-created in the SAME migration.

The predicate here is written out literally rather than rendered from
``w2v_common.database.rls.upgrade_statements(Base.metadata)`` (the style
``003_rls`` uses) on purpose: a migration is immutable history, and a
metadata-rendered statement would start emitting DDL for tables that do not exist
yet at this point in the revision chain the day a sixth ``tenant_id`` table is
added. It matches what ``register_rls_ddl`` now renders for a ``Uuid`` column
(``_predicate`` keys the cast off ``isinstance(col.type, String)``; ``sa.Uuid`` is
not a ``String``), so migration and ``create_all`` still agree.

Downgrade restores ``character varying(255)`` and the text predicate. The
name -> uuid backfill is intentionally NOT reversed: ``019de842-...`` is the
correct, canonical identity for that tenant and ``n8n-dev`` was the defect. Going
back to varchar does not make the old string right again, and re-introducing it
would just re-dirty the column.

Revision ID: 006_tenant_id_uuid
Revises: 005_drop_redundant_status_index
"""

from alembic import op

revision = "006_tenant_id_uuid"
down_revision = "005_drop_redundant_status_index"
branch_labels = None
depends_on = None

# Every table in this service carrying a tenant_id column, pinned in time.
TABLES = (
    "narration_jobs",
    "script_batches",
    "script_jobs",
    "script_usage",
    "storyline_jobs",
)

# The registered tenant NAME that was stored instead of its uuid, and its uuid.
LEGACY_TENANT_NAME = "n8n-dev"
LEGACY_TENANT_UUID = "019de842-c92a-7e37-8fdd-eb0d8c5396f9"

# 003_rls / w2v_common render exactly these. The GUC is transaction-scoped and
# always text; NULLIF folds the empty string PgBouncer leaves behind back to NULL
# so the predicate is simply false (0 rows) instead of raising on ''::uuid.
_GUC = "NULLIF(current_setting('app.current_tenant', true), '')"
_PREDICATE_UUID = f"tenant_id = {_GUC}::uuid"
_PREDICATE_TEXT = f"tenant_id = {_GUC}"

_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Policy names w2v_common may have created (strict + the nullable "global-or-own"
# variants), so the drop is exhaustive regardless of which shape was applied.
_POLICY_NAMES = (
    "tenant_isolation",
    "tenant_isolation_read",
    "tenant_isolation_ins",
    "tenant_isolation_upd",
    "tenant_isolation_del",
)


def _drop_policies() -> None:
    for table in TABLES:
        for policy in _POLICY_NAMES:
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")


def _create_policies(predicate: str) -> None:
    # ENABLE, never FORCE: the owner (w2v_service) must keep its bypass; the
    # runtime isolates because it connects as the restricted non-owner
    # w2v_content_app role. See w2v_common.database.rls.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING ({predicate}) WITH CHECK ({predicate})"
        )


def upgrade() -> None:
    # 1. Policies reference tenant_id -> they block the type change.
    _drop_policies()

    # 2. Name -> uuid backfill, while the column is still text.
    for table in TABLES:
        op.execute(
            f"UPDATE {table} SET tenant_id = '{LEGACY_TENANT_UUID}' "  # noqa: S608
            f"WHERE tenant_id = '{LEGACY_TENANT_NAME}'"
        )

    # 3. Fail loud on anything still non-uuid. No silent skip, no row loss.
    for table in TABLES:
        op.execute(
            f"""
            DO $$
            DECLARE
                bad_count bigint;
                sample text;
            BEGIN
                SELECT count(*), min(tenant_id)
                  INTO bad_count, sample
                  FROM {table}
                 WHERE tenant_id IS NOT NULL
                   AND tenant_id !~ '{_UUID_RE}';
                IF bad_count > 0 THEN
                    RAISE EXCEPTION
                        '{table}.tenant_id: % row(s) are not a uuid and cannot be '
                        'cast (first offender: %). Triage these by hand - map each '
                        'tenant NAME to its uuid in licenses.tenants and extend the '
                        'backfill above. Refusing to drop or skip rows.',
                        bad_count, sample;
                END IF;
            END $$;
            """  # noqa: S608
        )

    # 4. The cast. Preserves nullability; rebuilds indexes + unique constraints.
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE uuid USING tenant_id::uuid")

    # 5. Re-create RLS with the GUC cast to uuid, or the pod reads 0 rows.
    _create_policies(_PREDICATE_UUID)


def downgrade() -> None:
    _drop_policies()

    for table in TABLES:
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN tenant_id TYPE character varying(255) "
            f"USING tenant_id::text"
        )

    # Back to the text-to-text predicate that matches a varchar column.
    # NOTE: the n8n-dev -> uuid backfill is deliberately NOT reversed (see docstring).
    _create_policies(_PREDICATE_TEXT)
