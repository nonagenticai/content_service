"""Add script_artifact_id column to script_jobs.

Revision ID: 002_add_script_artifact_id
Revises: 001_initial
Create Date: 2026-05-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision = "002_add_script_artifact_id"
down_revision = "001_initial"
branch_labels = None
depends_on = None


def _column_exists(table_name: str, column_name: str) -> bool:
    # Needed because the persistent content_service_ci DB was bootstrapped with
    # a manual ALTER TABLE + `alembic stamp head`.  Re-running migrations against
    # it would error with DuplicateColumn without this guard.  The same pattern
    # also protects any future blue/green or partial-rollback scenario.
    bind = op.get_bind()
    insp = inspect(bind)
    return any(col["name"] == column_name for col in insp.get_columns(table_name))


def upgrade() -> None:
    if not _column_exists("script_jobs", "script_artifact_id"):
        op.add_column(
            "script_jobs",
            sa.Column("script_artifact_id", sa.String(36), nullable=True),
        )


def downgrade() -> None:
    if _column_exists("script_jobs", "script_artifact_id"):
        op.drop_column("script_jobs", "script_artifact_id")
