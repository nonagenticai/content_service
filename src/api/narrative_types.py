"""Rich domain types shared across content_service narrative endpoints and services."""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Storyline types
# ---------------------------------------------------------------------------


class StorylineIntro(BaseModel):
    """Intro title card section of a video storyline."""

    title: str = ""
    subtitle: str = ""
    duration_seconds: float = 8.0
    narrative_text: str = ""


class StorylineChapter(BaseModel):
    """Single chapter within a video storyline."""

    number: int = 0
    title: str = ""
    subtitle: str = ""
    pages: list[str] = Field(default_factory=list)
    narrative_arc: str = ""
    key_moments: list[str] = Field(default_factory=list)
    estimated_duration_seconds: float = 60.0


class StorylineOutro(BaseModel):
    """Outro title card section of a video storyline."""

    title: str = ""
    subtitle: str = ""
    duration_seconds: float = 6.0
    narrative_text: str = ""


class Storyline(BaseModel):
    """Complete video storyline."""

    video_title: str = ""
    video_description: str = ""
    target_duration_minutes: float = 5.0
    tone: str = ""
    intro: StorylineIntro = Field(default_factory=StorylineIntro)
    chapters: list[StorylineChapter] = Field(default_factory=list)
    outro: StorylineOutro = Field(default_factory=StorylineOutro)


# ---------------------------------------------------------------------------
# Narration types
# ---------------------------------------------------------------------------


class NarrationPart(BaseModel):
    """Narration text for a single video part."""

    part_id: str
    text: str = ""
    estimated_duration_seconds: float = 0.0
    word_count: int = 0


class NarrationScript(BaseModel):
    """Full narration script for the video."""

    parts: list[NarrationPart] = Field(default_factory=list)
    total_words: int = 0
    estimated_total_duration_seconds: float = 0.0
    tone: str = ""

    @model_validator(mode="after")
    def sync_totals(self) -> NarrationScript:
        """Sync derived totals with the actual parts data."""
        actual_words = sum(p.word_count for p in self.parts)
        actual_duration = sum(p.estimated_duration_seconds for p in self.parts)
        if self.total_words != actual_words:
            logger.debug(
                "NarrationScript: correcting total_words from %d to %d",
                self.total_words,
                actual_words,
            )
            self.total_words = actual_words
        if abs(self.estimated_total_duration_seconds - actual_duration) > 0.01:
            logger.debug(
                "NarrationScript: correcting duration from %.2f to %.2f",
                self.estimated_total_duration_seconds,
                actual_duration,
            )
            self.estimated_total_duration_seconds = actual_duration
        return self


# ---------------------------------------------------------------------------
# User context types
# ---------------------------------------------------------------------------


class FeaturePriority(BaseModel):
    """Priority weighting for a specific feature."""

    feature: str
    priority: str = "medium"  # high, medium, low, skip


class NarrationGuidance(BaseModel):
    """User-provided narration preferences."""

    tone: str = ""
    key_messages: list[str] = Field(default_factory=list)
    avoid_topics: list[str] = Field(default_factory=list)
    target_length_minutes: float = 0.0


class VisualPreferences(BaseModel):
    """User-provided visual style preferences."""

    style: str = ""
    color_scheme: str = ""
    transitions: str = ""


class CodeQualityFacts(BaseModel):
    """Code quality facts extracted from static analysis for narration enrichment."""

    primary_language: str = ""
    loc: int = 0
    maintainability_score: float = 0.0
    complexity_avg: float = 0.0
    test_coverage: float | None = None
    technical_debt: str = ""
    top_issues: list[str] = Field(default_factory=list)


class UserContext(BaseModel):
    """User-provided context that shapes storyline and narration generation."""

    goals: list[str] = Field(default_factory=list)
    target_audience: str = ""
    site_type: str = ""
    feature_priorities: list[FeaturePriority] = Field(default_factory=list)
    exclude_pages: list[str] = Field(default_factory=list)
    narration_guidance: NarrationGuidance = Field(default_factory=NarrationGuidance)
    visual_preferences: VisualPreferences = Field(default_factory=VisualPreferences)


# ---------------------------------------------------------------------------
# Critique / findings types
# ---------------------------------------------------------------------------


class CritiqueFinding(BaseModel):
    """A single finding from a website critique."""

    category: str = ""  # seo, security, a11y, performance, ux
    severity: str = "medium"  # critical, high, medium, low
    title: str = ""
    url: str | None = ""
    selector: str | None = ""
    recommendation: str | None = ""
