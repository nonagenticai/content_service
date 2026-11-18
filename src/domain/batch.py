"""Batch domain models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel


class BatchStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    PARTIAL = "partial"


class Batch(BaseModel):
    """Domain representation of a script generation batch."""

    batch_id: str
    tenant_id: str
    total_parts: int
    status: BatchStatus = BatchStatus.PENDING
    created_at: datetime | None = None
    completed_at: datetime | None = None
