"""Script generation domain models."""

from __future__ import annotations

from pydantic import BaseModel


class GeneratedScript(BaseModel):
    """Result of a script generation pass."""

    script_source: str
    prompt_used: str | None = None
    generation_time_seconds: float = 0.0
    llm_tokens_used: int = 0
    provider_used: str = ""


class ImprovementAttempt(BaseModel):
    """Detail for a single improvement attempt."""

    attempt: int
    errors: list[str] = []
    fix_applied: bool = False
    accepted: bool = False
    reason: str | None = None


class ValidationResult(BaseModel):
    """Result of script validation (compile + discover + dry-run)."""

    compiles: bool = False
    discoverable: bool = False
    runs_headless: bool = False
    accepted: bool = False
    errors: list[str] | None = None
    improvement_attempts: int = 0
    attempts_detail: list[ImprovementAttempt] = []
