"""Job domain models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Job(BaseModel):
    """Domain representation of a script generation job."""

    job_id: str
    tenant_id: UUID
    batch_id: str | None = None
    part_id: str
    part_type: str = "browser_recording"
    status: JobStatus = JobStatus.PENDING
    # Input
    shot_details: dict = {}
    page_analysis: dict | None = None
    theme: dict | None = None
    storyline_context: str | None = None
    user_context: dict | None = None
    mode: str | None = None
    source_type: str | None = None
    auth_enabled: bool = False
    # Output
    script_source: str | None = None
    script_path: str | None = None
    script_artifact_id: UUID | None = None
    prompt_used: str | None = None
    generation_time_seconds: float | None = None
    error_message: str | None = None
    # Timestamps
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
