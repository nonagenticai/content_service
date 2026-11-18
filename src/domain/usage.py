"""Usage tracking domain model."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel


class UsageRecord(BaseModel):
    """Daily usage record for a tenant."""

    tenant_id: str
    date: date
    request_count: int = 0
    scripts_generated: int = 0
    scripts_accepted: int = 0
    llm_tokens_used: int = 0
    error_count: int = 0
