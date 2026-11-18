"""SQLAlchemy ORM models for content_service.

Combined models from narrative_service (StorylineJob, NarrationJob) and
script_generator_service (ScriptJob, ScriptBatch, ScriptUsage).
"""

import datetime as dt
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from w2v_common.database.rls import register_rls_ddl


class Base(DeclarativeBase):
    """Base class for all models."""


# ---------------------------------------------------------------------------
# Narrative-domain tables
# ---------------------------------------------------------------------------


class StorylineJob(Base):
    """Async storyline generation job."""

    __tablename__ = "storyline_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    storyline: Mapped[dict | None] = mapped_column(JSONB)
    shot_list: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class NarrationJob(Base):
    """Async narration generation job."""

    __tablename__ = "narration_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    narration: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ---------------------------------------------------------------------------
# Script-domain tables
# ---------------------------------------------------------------------------


class ScriptJob(Base):
    """Script generation job tracking."""

    __tablename__ = "script_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    batch_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # No single-column index on status: the composite ``ix_script_jobs_status_created``
    # (status, created_at) below already serves status-only lookups (B-tree leftmost
    # column). Migration 005 drops the redundant ``ix_script_jobs_status``.
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")

    # Input
    part_id: Mapped[str] = mapped_column(String(255))
    part_type: Mapped[str] = mapped_column(
        String(50), default="browser_recording", server_default="browser_recording"
    )
    page_url: Mapped[str | None] = mapped_column(String(2048))
    page_type: Mapped[str | None] = mapped_column(String(50))
    shot_details: Mapped[dict | None] = mapped_column(JSONB)
    page_analysis: Mapped[dict | None] = mapped_column(JSONB)
    theme: Mapped[dict | None] = mapped_column(JSONB)
    storyline_context: Mapped[str | None] = mapped_column(Text)

    # User context and generation mode
    user_context: Mapped[dict | None] = mapped_column(JSONB)
    mode: Mapped[str | None] = mapped_column(String(50))
    source_type: Mapped[str | None] = mapped_column(String(20))
    auth_enabled: Mapped[bool | None] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    # Output
    script_path: Mapped[str | None] = mapped_column(String(500))
    script_artifact_id: Mapped[str | None] = mapped_column(String(36))
    script_source: Mapped[str | None] = mapped_column(Text)
    prompt_used: Mapped[str | None] = mapped_column(Text)
    generation_time_seconds: Mapped[float | None] = mapped_column(Float)
    provider_used: Mapped[str | None] = mapped_column(String(100))
    extraction_tokens_used: Mapped[int | None] = mapped_column(Integer)

    # Validation
    compiles: Mapped[int | None] = mapped_column(Integer)
    discoverable: Mapped[int | None] = mapped_column(Integer)
    runs_headless: Mapped[int | None] = mapped_column(Integer)
    accepted: Mapped[int | None] = mapped_column(Integer)
    validation_errors: Mapped[dict | None] = mapped_column(JSONB)
    improvement_attempts: Mapped[int | None] = mapped_column(Integer, default=0, server_default="0")

    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_script_jobs_status_created", "status", "created_at"),)


class ScriptBatch(Base):
    """Batch generation tracking."""

    __tablename__ = "script_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    total_parts: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="pending", server_default="pending")
    # Mirrors ``storyline_jobs.error`` / ``narration_jobs.error`` /
    # ``script_jobs.error_message``: the batch had NO place to record WHY it
    # reached a non-successful terminal status, so a reconciled batch was
    # indistinguishable from one whose parts genuinely half-failed. Added by
    # migration 008 together with the ``script_batches`` sweep in
    # ``reconcile_stale_jobs``.
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class ScriptUsage(Base):
    """Daily script generation usage per tenant."""

    __tablename__ = "script_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scripts_generated: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    scripts_accepted: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    llm_tokens_used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    __table_args__ = (UniqueConstraint("tenant_id", "date", name="uq_script_usage_tenant_date"),)


# Phase D RLS: enable + tenant_isolation policy on every tenant_id table when a
# fresh schema is built via create_all (tests/dev). Prod gets the SAME DDL from the
# alembic migration (single source: w2v_common.database.rls). Inert until the
# runtime connects as the restricted w2v_content_app role + TENANT_RLS_MODE=enforce.
register_rls_ddl(Base.metadata)
