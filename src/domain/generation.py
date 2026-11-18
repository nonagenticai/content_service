"""Generation request domain model."""

from __future__ import annotations

from pydantic import BaseModel


class GenerationRequest(BaseModel):
    """Input for script generation."""

    part: dict
    page_analysis: dict[str, dict] | None = None
    storyline_context: str | None = None
    theme: dict | None = None
    user_context: dict | None = None
    message: str | None = None
    mode: str = "webapp_commercial"
    source_type: str = "web"
    auth_enabled: bool = False
