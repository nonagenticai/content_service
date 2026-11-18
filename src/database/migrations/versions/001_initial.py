"""Initial migration: create all content_service tables.

Revision ID: 001_initial
Revises:
Create Date: 2026-03-10
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision = "001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -- Narrative-domain tables --

    op.create_table(
        "storyline_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("storyline", JSONB, nullable=True),
        sa.Column("shot_list", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "narration_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("narration", JSONB, nullable=True),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- Script-domain tables --

    op.create_table(
        "script_jobs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("batch_id", sa.String(36), nullable=True, index=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending", index=True),
        # Input
        sa.Column("part_id", sa.String(255), nullable=False),
        sa.Column("part_type", sa.String(50), nullable=False, server_default="browser_recording"),
        sa.Column("page_url", sa.String(2048), nullable=True),
        sa.Column("page_type", sa.String(50), nullable=True),
        sa.Column("shot_details", JSONB, nullable=True),
        sa.Column("page_analysis", JSONB, nullable=True),
        sa.Column("theme", JSONB, nullable=True),
        sa.Column("storyline_context", sa.Text, nullable=True),
        # User context and generation mode
        sa.Column("user_context", JSONB, nullable=True),
        sa.Column("mode", sa.String(50), nullable=True),
        sa.Column("source_type", sa.String(20), nullable=True),
        sa.Column("auth_enabled", sa.Boolean, server_default="false"),
        # Output
        sa.Column("script_path", sa.String(500), nullable=True),
        sa.Column("script_source", sa.Text, nullable=True),
        sa.Column("prompt_used", sa.Text, nullable=True),
        sa.Column("generation_time_seconds", sa.Float, nullable=True),
        sa.Column("provider_used", sa.String(100), nullable=True),
        sa.Column("extraction_tokens_used", sa.Integer, nullable=True),
        # Validation
        sa.Column("compiles", sa.Integer, nullable=True),
        sa.Column("discoverable", sa.Integer, nullable=True),
        sa.Column("runs_headless", sa.Integer, nullable=True),
        sa.Column("accepted", sa.Integer, nullable=True),
        sa.Column("validation_errors", JSONB, nullable=True),
        sa.Column("improvement_attempts", sa.Integer, nullable=True, server_default="0"),
        sa.Column("error_message", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_script_jobs_status_created", "script_jobs", ["status", "created_at"])

    op.create_table(
        "script_batches",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("batch_id", sa.String(36), unique=True, nullable=False, index=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("total_parts", sa.Integer, nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "script_usage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(255), nullable=False, index=True),
        sa.Column("date", sa.Date, nullable=False, index=True),
        sa.Column("request_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scripts_generated", sa.Integer, nullable=False, server_default="0"),
        sa.Column("scripts_accepted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("llm_tokens_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("error_count", sa.Integer, nullable=False, server_default="0"),
        sa.UniqueConstraint("tenant_id", "date", name="uq_script_usage_tenant_date"),
    )


def downgrade() -> None:
    op.drop_table("script_usage")
    op.drop_table("script_batches")
    op.drop_index("ix_script_jobs_status_created", table_name="script_jobs")
    op.drop_table("script_jobs")
    op.drop_table("narration_jobs")
    op.drop_table("storyline_jobs")
