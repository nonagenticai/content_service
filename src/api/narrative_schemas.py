"""Request/response Pydantic models for content_service narrative API endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field

from src.api.narrative_types import CodeQualityFacts, CritiqueFinding, UserContext


class DegradationReason(BaseModel):
    """One stable reason code plus how many items carried it."""

    code: str
    count: int = 0


class DegradationReport(BaseModel):
    """Aggregate fail-open signal, read at the TOP of a job/API envelope.

    The per-item ``degraded`` booleans below are honest but buried: a caller has
    to walk the nested content JSON to find them, and the orchestrator does not.
    This is the one field it can read instead. ``degraded`` is the OR of every
    item marker; ``reasons`` carries stable codes with counts (see
    ``src.services.narrative.degradation``) and is EMPTY on a healthy job.

    Visibility only — a degraded job is still ``status="completed"``.
    """

    degraded: bool = False
    reasons: list[DegradationReason] = []


class SiteInput(BaseModel):
    """High-level metadata about the website being analysed."""

    description: str = ""
    target_audience: list[str] = []
    use_cases: list[str] = []
    selling_points: list[str] = []
    tone: str = "professional"
    visual_identity: dict = {}


class PageInput(BaseModel):
    """Crawled page data used as input for storyline and narration generation."""

    page_id: str = ""
    url: str
    title: str = ""
    page_type: str = "unknown"
    word_count: int = 0
    interactive_elements: list[dict] = []
    content_sections: list[dict] = []
    navigation: list[dict] = []
    headings: list[str] = []
    has_forms: bool = False
    has_media: bool = False
    meta_description: str = ""
    is_spa_section: bool = False


class AnalysisInput(BaseModel):
    """Container for website analysis results: pages and site-level metadata."""

    pages: list[PageInput] = []
    site: SiteInput = Field(default_factory=SiteInput)


class ReviewEntry(BaseModel):
    """Validated structure for a single review entry."""

    rating: float | None = None
    sentiment: str = ""
    summary: str = ""
    issues: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)


class LLMConfigOverride(BaseModel):
    """Optional per-request LLM configuration override."""

    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None


class StorylineRequest(BaseModel):
    """Request body for the POST /api/storyline endpoint."""

    analysis: AnalysisInput
    reviews: dict[str, ReviewEntry] | None = None
    duration_minutes: float = 3.0
    message: str = ""
    user_context: UserContext | None = None
    mode: str = "webapp_commercial"
    llm_config: LLMConfigOverride | None = None


class ShotListRequest(BaseModel):
    """Request body for the POST /api/shotlist endpoint."""

    storyline: dict
    analysis: AnalysisInput
    chapter_title_card_duration: float = 5.0
    user_context: UserContext | None = None
    job_id: str | None = None
    llm_config: LLMConfigOverride | None = None


# --- Response models ---


class IntroResponse(BaseModel):
    """Intro title card in a storyline response."""

    title: str = ""
    subtitle: str = ""
    duration_seconds: float = 8.0
    narrative_text: str = ""


class ChapterResponse(BaseModel):
    """Single chapter within a storyline response."""

    number: int
    title: str
    subtitle: str = ""
    pages: list[str] = []
    narrative_arc: str = ""
    key_moments: list[str] = []
    estimated_duration_seconds: float = 60.0


class OutroResponse(BaseModel):
    """Outro title card in a storyline response."""

    title: str = ""
    subtitle: str = ""
    duration_seconds: float = 6.0
    narrative_text: str = ""


class StorylineResponse(BaseModel):
    """Full storyline returned from generation or findings conversion."""

    video_title: str
    video_description: str = ""
    target_duration_minutes: float = 3.0
    tone: str = "professional"
    intro: IntroResponse = Field(default_factory=IntroResponse)
    chapters: list[ChapterResponse] = []
    outro: OutroResponse = Field(default_factory=OutroResponse)
    # ``StorylineGenerator._default_storyline`` marks its deterministic fail-open
    # placeholder with ``degraded: True`` and the job is still persisted as
    # ``status="completed"``. Without the field declared here pydantic silently
    # DROPPED it on the way out of the API, so a caller could not tell a real
    # storyline from a boilerplate fallback — the whole point of the marker.
    # Same contract as ``NarrationPartResponse.degraded`` below. Keep it.
    degraded: bool = False


class ShotListPartResponse(BaseModel):
    """Single recordable part within a shot list."""

    id: str
    part_type: str
    chapter: int | None = None
    title: str = ""
    subtitle: str = ""
    pages: list[str] = []
    actions: list[str] = []
    key_elements: list[str] = []
    estimated_duration_seconds: float = 0.0
    narration_text: str = ""
    # ``ShotListBuilder`` marks a part it could not plan concretely (a chapter
    # that declared no pages, so there is no URL to record) with
    # ``degraded: True``. Undeclared, pydantic strips it and the stub part is
    # indistinguishable from a real recording plan. Same contract as
    # ``NarrationPartResponse.degraded``.
    degraded: bool = False
    # WHY it is degraded, as a stable code (``degradation.SHOT_MISSING_URL``).
    # ``degraded: True`` alone cannot tell "no URL, nothing to record" from any
    # future fail-open shape, and the aggregate below groups by this code.
    degraded_reason: str | None = None


class ShotListResponse(BaseModel):
    """Full shot list with parts, recording order, and duration totals."""

    parts: list[ShotListPartResponse] = []
    recording_order: list[str] = []
    total_parts: int = 0
    estimated_total_duration_seconds: float = 0.0
    # Aggregate of the per-part markers above — the field a caller reads instead
    # of walking ``parts[*].degraded``.
    degradation: DegradationReport = Field(default_factory=DegradationReport)


class StorylineJobResponse(BaseModel):
    """Accepted response for an async storyline generation job."""

    job_id: str
    status: str
    created_at: str | None = None


class StorylineResultResponse(BaseModel):
    """Polling response for storyline job status and results."""

    job_id: str
    status: str
    storyline: StorylineResponse | None = None
    error: str | None = None
    token_usage: dict | None = None
    # Aggregate fail-open signal. A caller reads THIS instead of reaching into
    # ``storyline.degraded``; status stays ``completed`` either way.
    degradation: DegradationReport = Field(default_factory=DegradationReport)


# --- Narrative Extract schemas ---


class NarrativeExtractRequest(BaseModel):
    """Request body for the POST /api/narrative/extract endpoint."""

    pages: list[PageInput]


class NarrativeExtractResponse(BaseModel):
    """Extracted narrative hooks, quotes, and feature highlights from pages."""

    hooks: list[str] = []
    key_quotes: list[str] = []
    feature_highlights: list[str] = []
    total_pages: int = 0
    pages_with_forms: int = 0
    pages_with_media: int = 0


# --- Chapter Plan schemas ---


class ChapterPlanRequest(BaseModel):
    """Request body for the POST /api/storyline/plan endpoint."""

    storyline: dict
    target_duration_minutes: float = Field(default=3.0, gt=0, le=600.0)
    # An overage RATIO (e.g. 1.5 = 150%); a non-positive value inverts the overage
    # warning in ChapterPlanner.validate, so bound it positive at the schema.
    duration_overage_threshold: float = Field(default=1.5, gt=0, le=100.0)


class ChapterPlanResponse(BaseModel):
    """Validated and rebalanced chapter plan with warnings."""

    warnings: list[str] = []
    storyline: dict = {}


# --- Narration schemas ---


class NarrationRequest(BaseModel):
    """Request body for the POST /api/narration endpoint."""

    storyline: dict
    analysis: AnalysisInput
    user_context: UserContext | None = None
    message: str = ""
    synthesize_audio: bool = False
    mode: str = "webapp_commercial"
    code_quality_facts: CodeQualityFacts | None = None
    llm_config: LLMConfigOverride | None = None


class NarrationForShotListRequest(BaseModel):
    """Request body for the POST /api/narration/shotlist endpoint."""

    shot_list: dict
    storyline: dict
    user_context: UserContext | None = None
    message: str = ""
    synthesize_audio: bool = False
    mode: str = "webapp_commercial"
    code_quality_facts: CodeQualityFacts | None = None
    llm_config: LLMConfigOverride | None = None


class NarrationJobResponse(BaseModel):
    """Accepted response for an async narration generation job."""

    job_id: str
    status: str
    created_at: str | None = None


class NarrationPartResponse(BaseModel):
    """Narration text and optional audio for a single video part."""

    part_id: str
    text: str = ""
    estimated_duration_seconds: float = 0.0
    word_count: int = 0
    audio_base64: str | None = None
    # Producers of deterministic boilerplate (chapter fallback, part fallback,
    # storyline fallback) set ``degraded`` on the dict they emit. Without the
    # field declared here pydantic silently DROPPED it on the way out of the
    # API, so a caller could not tell an LLM narration from a timeout
    # placeholder — the whole point of the marker. Keep it.
    degraded: bool = False


class NarrationScriptResponse(BaseModel):
    """Complete narration script across all video parts."""

    parts: list[NarrationPartResponse] = []
    total_words: int = 0
    estimated_total_duration_seconds: float = 0.0
    tone: str = ""


class NarrationResultResponse(BaseModel):
    """Polling response for narration job status and results."""

    job_id: str
    status: str
    narration: NarrationScriptResponse | None = None
    error: str | None = None
    token_usage: dict | None = None
    # Aggregate fail-open signal. A caller reads THIS instead of iterating
    # ``narration.parts[*].degraded``; status stays ``completed`` either way.
    degradation: DegradationReport = Field(default_factory=DegradationReport)


# --- Shot List Enhance schemas ---


class ShotListEnhanceRequest(BaseModel):
    """Request body for the POST /api/shotlist/enhance endpoint."""

    shot_list: dict
    storyline: dict
    analysis: AnalysisInput
    llm_config: LLMConfigOverride | None = None


class ShotListEnhanceResponse(BaseModel):
    """Response containing the AI-enhanced shot list."""

    shot_list: dict = {}
    token_usage: dict | None = None


# --- Findings conversion schemas ---


class FindingsToStorylineRequest(BaseModel):
    """Request body for converting critique/review findings to a storyline."""

    findings: list[CritiqueFinding] = []
    scores: dict[str, float] | None = None
    analysis: dict | None = None
    reviews: dict[str, dict] | None = None
    target_minutes: float = 3.0
    conversion_type: str = "critique"  # "critique" or "reviews"


class FindingsToShotListRequest(BaseModel):
    """Request body for converting critique/review findings to a shot list."""

    storyline: dict
    findings: list[CritiqueFinding] = []
    scores: dict[str, float] | None = None
    reviews: dict[str, dict] | None = None
    conversion_type: str = "critique"  # "critique" or "reviews"
