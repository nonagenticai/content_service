"""Tests for StorylineGenerator decomposed methods (_prepare_input, _post_process)."""

from __future__ import annotations

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.api.narrative_types import FeaturePriority, NarrationGuidance, UserContext
from src.services.narrative.storyline_generator import StorylineGenerator


def _make_analysis(n_pages: int = 3) -> AnalysisInput:
    pages = [
        PageInput(
            url=f"https://example.com/page-{i}",
            title=f"Page {i}",
            page_type="content",
            page_id=f"p{i}",
        )
        for i in range(n_pages)
    ]
    return AnalysisInput(
        pages=pages,
        site=SiteInput(description="Test site", tone="professional"),
    )


class TestPrepareInput:
    """Tests for _prepare_input()."""

    def test_overrides_duration_from_user_context(self):
        gen = StorylineGenerator()
        analysis = _make_analysis()
        uc = UserContext(
            narration_guidance=NarrationGuidance(target_length_minutes=5.0),
        )
        _, dur, _ = gen._prepare_input(analysis, 3.0, "", uc)
        assert dur == 5.0

    def test_auto_detects_duration_when_zero(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(3)
        _, dur, _ = gen._prepare_input(analysis, 0, "", None)
        assert dur == 2.0  # <=5 pages → 2.0

    def test_auto_detects_duration_many_pages(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(20)
        _, dur, _ = gen._prepare_input(analysis, 0, "", None)
        assert dur == 5.0  # >15 pages → min(5.0, 1.5 + 20*0.2)

    def test_builds_context_section(self):
        gen = StorylineGenerator()
        analysis = _make_analysis()
        uc = UserContext(goals=["increase signups"], target_audience="devs")
        _, _, msg = gen._prepare_input(analysis, 3.0, "", uc)
        assert "increase signups" in msg
        assert "devs" in msg

    def test_filters_excluded_pages(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(5)
        uc = UserContext(exclude_pages=["page-0", "page-1"])
        filtered_analysis, _, _ = gen._prepare_input(analysis, 3.0, "", uc)
        assert len(filtered_analysis.pages) == 3

    def test_preserves_message(self):
        gen = StorylineGenerator()
        analysis = _make_analysis()
        _, _, msg = gen._prepare_input(analysis, 3.0, "my direction", None)
        assert msg == "my direction"


class TestPostProcess:
    """Tests for _post_process()."""

    def test_validates_chapter_urls(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(2)
        storyline = {
            "video_title": "Test",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "professional",
            "intro": {"title": "", "subtitle": "", "duration_seconds": 8, "narrative_text": ""},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["https://example.com/page-0", "https://bogus.com/fake"],
                    "estimated_duration_seconds": 60,
                }
            ],
            "outro": {"title": "", "subtitle": "", "duration_seconds": 6, "narrative_text": ""},
        }
        result = gen._post_process(storyline, analysis, None)
        # Bogus URL should be dropped
        ch1_pages = result["chapters"][0]["pages"]
        assert "https://bogus.com/fake" not in ch1_pages
        assert "https://example.com/page-0" in ch1_pages

    def test_applies_feature_weights(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(2)
        uc = UserContext(
            feature_priorities=[
                FeaturePriority(feature="pricing", priority="high"),
            ],
        )
        storyline = {
            "video_title": "Test",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "professional",
            "intro": {"title": "", "subtitle": "", "duration_seconds": 8, "narrative_text": ""},
            "chapters": [
                {
                    "number": 1,
                    "title": "Pricing Plans",
                    "pages": ["https://example.com/page-0"],
                    "narrative_arc": "pricing overview",
                    "estimated_duration_seconds": 10,
                }
            ],
            "outro": {"title": "", "subtitle": "", "duration_seconds": 6, "narrative_text": ""},
        }
        result = gen._post_process(storyline, analysis, uc)
        # High-priority chapter should have at least high_priority_min_duration
        assert result["chapters"][0]["estimated_duration_seconds"] >= 15.0

    def test_rebalances_chapters(self):
        gen = StorylineGenerator()
        analysis = _make_analysis(2)
        storyline = {
            "video_title": "Test",
            "video_description": "",
            "target_duration_minutes": 1.0,
            "tone": "professional",
            "intro": {"title": "", "subtitle": "", "duration_seconds": 8, "narrative_text": ""},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["https://example.com/page-0"],
                    "estimated_duration_seconds": 120,
                }
            ],
            "outro": {"title": "", "subtitle": "", "duration_seconds": 6, "narrative_text": ""},
        }
        result = gen._post_process(storyline, analysis, None)
        # Chapter should be scaled down to fit 1 minute target
        assert result["chapters"][0]["estimated_duration_seconds"] <= 120
