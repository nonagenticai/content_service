"""Drop the redundant single-column ``ix_script_jobs_status`` index.

``script_jobs`` carries both ``ix_script_jobs_status`` (status) and the composite
``ix_script_jobs_status_created`` (status, created_at). By the B-tree leftmost-column
rule the composite already serves every ``WHERE status = ...`` lookup, so the single-
column index is dead weight (extra write amplification, no read benefit). Drop it and
keep the composite.

Idempotent: prod still carries the index; a from-scratch migrate creates it in
``001_initial`` (``status ... index=True``) and this migration removes it. ``IF EXISTS`` /
``IF NOT EXISTS`` keep both directions safe regardless of starting state. Runs as the
owner ``w2v_service`` in the initContainer, so ``DROP INDEX`` is permitted.

Revision ID: 005_drop_redundant_status_index
Revises: 004_reconcile_stale_jobs
"""

from alembic import op

revision = "005_drop_redundant_status_index"
down_revision = "004_reconcile_stale_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_script_jobs_status")


def downgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_script_jobs_status ON script_jobs (status)")
