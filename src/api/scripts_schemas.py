"""Request/response Pydantic models for script_generator_service."""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

# A part id is used verbatim to build on-disk filenames (e.g.
# ``{part_id}.demo.spec.ts``) and path components in storage_service. It MUST
# therefore be a single, traversal-free path segment. Reject anything outside a
# conservative filename-safe alphabet at the edge (422) so a malicious id never
# reaches the filesystem layer.
_PART_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _validate_part_id(value: str) -> str:
    """Validate a string destined to become a filesystem path component."""
    if not _PART_ID_RE.match(value):
        raise ValueError(
            "must be 1-128 chars of [A-Za-z0-9._-] with no path separators",
        )
    if ".." in value:
        raise ValueError("must not contain '..'")
    return value


class LLMConfigOverride(BaseModel):
    """Optional overrides for the LLM provider used during generation."""

    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None


class ShotDetailInput(BaseModel):
    """Shot/part details for script generation."""

    id: str = Field(..., description="Part ID")

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        return _validate_part_id(value)

    part_type: str = Field(
        default="browser_recording",
        description="Part type: browser_recording, title_card, ai_generated",
    )
    title: str = Field(default="", description="Part title")
    subtitle: str | None = Field(default=None, description="Part subtitle")
    chapter: int | None = Field(default=None, description="Chapter number")
    pages: list[str] = Field(default_factory=list, description="Page IDs to display")
    actions: list[str] = Field(default_factory=list, description="Actions to perform")
    key_elements: list[str] = Field(default_factory=list, description="CSS selectors to highlight")
    estimated_duration_seconds: float = Field(default=10.0, description="Estimated duration")
    narration_text: str | None = Field(default=None, description="Narration text for this part")


class PageAnalysisInput(BaseModel):
    """Page analysis data for script generation context."""

    page_id: str = Field(..., description="Page ID")
    url: str = Field(..., description="Page URL")
    page_type: str | None = Field(
        default=None,
        # ``page_type`` selects a prompt template by filename
        # (``PromptBuilder._load_template(f"{page_type}.md")``). Free-form text
        # there allowed ``../`` traversal and absolute paths into the LLM
        # prompt. Constrain it to a bare identifier; the loader also enforces
        # containment for non-HTTP callers that bypass this schema.
        pattern=r"^[A-Za-z0-9_-]{1,50}$",
        max_length=50,
        description="Classified page type (bare identifier: letters, digits, '_' and '-')",
    )
    title: str | None = Field(default=None, description="Page title")
    interactive_elements: list[dict] | None = Field(
        default=None, description="Interactive elements found"
    )
    content_sections: list[dict] | None = Field(default=None, description="Content sections")
    navigation_elements: list[dict] | None = Field(default=None, description="Navigation elements")


class ThemeInput(BaseModel):
    """Theme colors for script visual effects."""

    model_config = ConfigDict(populate_by_name=True)

    primary_color: str = Field(default="#2563EB", alias="primaryColor")
    background_color: str = Field(default="#FFFFFF", alias="backgroundColor")
    accent_color: str = Field(default="#F59E0B", alias="accentColor")
    text_color: str = Field(default="#1F2937", alias="textColor")
    subtitle_color: str = Field(default="#9CA3AF", alias="subtitleColor")
    gradient_start: str = Field(default="#2563EB", alias="gradientStart")
    gradient_end: str = Field(default="#7C3AED", alias="gradientEnd")
    font_family: str = Field(default="system-ui, -apple-system, sans-serif")


class NarrationGuidanceInput(BaseModel):
    """Guidance for narration tone and content."""

    tone: str | None = None
    key_messages: list[str] = Field(default_factory=list)
    avoid_topics: list[str] = Field(default_factory=list)


class UserContextInput(BaseModel):
    """User-provided context about the target website."""

    description: str | None = None
    site_type: str | None = None
    target_audience: str | None = None
    goals: list[str] = Field(default_factory=list)
    focus_pages: list[str] = Field(default_factory=list)
    exclude_pages: list[str] = Field(default_factory=list)
    narration_guidance: NarrationGuidanceInput | None = None


class CampaignVariantMetaResponse(BaseModel):
    """Metadata describing one generated campaign variant."""

    part_id: str
    style: str
    variant_index: int
    hook: str | None = None
    cta: str | None = None


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class GenerateRequest(BaseModel):
    """Request body for script generation."""

    part: ShotDetailInput = Field(..., description="Shot/part details")
    page_analysis: dict[str, PageAnalysisInput] | None = Field(
        default=None, description="Page analyses keyed by page_id"
    )
    storyline_context: str | None = Field(default=None, description="Storyline narrative context")
    theme: ThemeInput | None = Field(default=None, description="Theme colors")
    message: str | None = Field(default=None, description="User message/instructions")
    mode: str = "webapp_commercial"
    source_type: str = "web"
    user_context: UserContextInput | None = None
    auth_enabled: bool = False
    llm_config: LLMConfigOverride | None = None


class GenerateBatchRequest(BaseModel):
    """Request body for batch script generation."""

    parts: list[ShotDetailInput] = Field(
        ..., min_length=1, max_length=50, description="Parts to generate scripts for"
    )

    @field_validator("parts")
    @classmethod
    def validate_batch_size(cls, v: list[ShotDetailInput]) -> list[ShotDetailInput]:
        if len(v) > 50:
            raise ValueError("Max 50 parts per batch")
        return v

    page_analysis: dict[str, PageAnalysisInput] | None = Field(default=None)
    storyline_context: str | None = Field(default=None)
    theme: ThemeInput | None = Field(default=None)
    message: str | None = Field(default=None)
    mode: str = "webapp_commercial"
    source_type: str = "web"
    user_context: UserContextInput | None = None
    auth_enabled: bool = False
    llm_config: LLMConfigOverride | None = None


class GenerateCampaignPackRequest(BaseModel):
    """Request body for template-driven campaign pack generation."""

    part: ShotDetailInput | None = Field(
        default=None,
        description="Optional seed part. If omitted, one is inferred from page_analysis.",
    )
    page_analysis: dict[str, PageAnalysisInput] | None = Field(
        default=None, description="Page analyses keyed by page_id"
    )
    storyline_context: str | None = Field(default=None, description="Storyline narrative context")
    theme: ThemeInput | None = Field(default=None, description="Theme colors")
    message: str | None = Field(default=None, description="User message/instructions")
    source_type: str = "web"
    user_context: UserContextInput | None = None
    auth_enabled: bool = False
    llm_config: LLMConfigOverride | None = None

    script_styles: list[str] = Field(
        default_factory=lambda: ["webapp_commercial", "commercial", "company_commercial"],
        min_length=1,
        max_length=10,
        description="Template modes to generate per campaign pack.",
    )
    variants_per_style: int = Field(
        default=3, ge=1, le=10, description="How many variants to generate for each style."
    )
    variant_duration_seconds: float = Field(
        default=15.0,
        ge=5.0,
        le=60.0,
        description="Target duration budget for each short ad variant.",
    )
    hooks: list[str] | None = Field(
        default=None,
        description="Optional custom hook lines to rotate across variants.",
    )
    ctas: list[str] | None = Field(
        default=None,
        description="Optional custom CTA lines to rotate across variants.",
    )


class ValidateRequest(BaseModel):
    """Request body for script validation."""

    scripts_dir: str | None = Field(
        default=None,
        description=(
            "Deprecated and IGNORED. The validation directory is derived solely "
            "from the job's storage key; an external path is never honored "
            "(it would set the cwd of npx tsc/playwright — RCE-capable). "
            "Retained only for wire compatibility."
        ),
    )


class ScaffoldRequest(BaseModel):
    """Request to scaffold a TypeScript project for a job."""

    theme: ThemeInput | None = None
    source_type: str = "web"


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class AsyncJobResponse(BaseModel):
    """Response returned when an async generation job is created."""

    job_id: str
    status: str = "pending"
    poll_url: str


class BatchJobResponse(BaseModel):
    """Response returned when a batch generation run is created."""

    batch_id: str
    job_ids: dict[str, str]
    status: str = "pending"


class CampaignPackResponse(BaseModel):
    """Response returned when a campaign-pack generation run is created."""

    batch_id: str
    job_ids: dict[str, str]
    status: str = "pending"
    total_variants: int
    variants: list[CampaignVariantMetaResponse] = Field(default_factory=list)


class GenerationResultResponse(BaseModel):
    """Result of a script generation job."""

    job_id: str
    part_id: str
    status: str
    script_source: str | None = None
    script_path: str | None = None
    script_artifact_id: UUID | None = Field(
        default=None,
        description=(
            "Identifier in artifact_service for this script's bytes. None means the "
            "upload was skipped or failed; consumers MUST fall back to script_path "
            "(local-pod-only) only when this is None."
        ),
    )
    generation_time_seconds: float | None = None
    provider_used: str | None = None
    error: str | None = None
    token_usage: dict | None = None


class ImprovementAttemptResponse(BaseModel):
    """Detail for a single validation/improvement attempt."""

    attempt: int
    errors: list[str] = Field(default_factory=list)
    fix_applied: bool = False
    accepted: bool = False
    reason: str | None = None


class ValidationResultResponse(BaseModel):
    """Result of script validation."""

    job_id: str
    part_id: str
    compiles: bool = False
    discoverable: bool = False
    runs_headless: bool = False
    accepted: bool = False
    errors: list[str] | None = None
    improvement_attempts: int = 0
    attempts_detail: list[ImprovementAttemptResponse] = Field(default_factory=list)


class JobStatusResponse(BaseModel):
    """Current status, timestamps, and full result for a generation job."""

    job_id: str
    status: str
    part_id: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    # Result fields (merged from old /result endpoint)
    script_source: str | None = None
    script_path: str | None = None
    script_artifact_id: UUID | None = Field(
        default=None,
        description=(
            "Identifier in artifact_service for this script's bytes. None means the "
            "upload was skipped or failed; consumers MUST fall back to script_path "
            "(local-pod-only) only when this is None."
        ),
    )
    generation_time_seconds: float | None = None
    provider_used: str | None = None
    token_usage: dict | None = None


class ScaffoldResponse(BaseModel):
    """Response from scaffolding a TypeScript project."""

    success: bool
    scripts_dir: str | None = None
    error: str | None = None


class BatchStatusResponse(BaseModel):
    """Aggregated status for a batch generation run."""

    batch_id: str
    total: int
    completed: int
    failed: int
    status: str
    results: list[GenerationResultResponse] = Field(default_factory=list)
