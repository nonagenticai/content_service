"""Tests for user context integration across services."""

from src.api.narrative_schemas import PageInput
from src.api.narrative_types import FeaturePriority, NarrationGuidance, UserContext
from src.services.narrative.chapter_planner import ChapterPlanner
from src.services.narrative.narrative_extractor import NarrativeExtractor
from src.services.narrative.storyline_generator import StorylineGenerator


class TestContextSectionBuilding:
    def test_empty_context(self) -> None:
        ctx = UserContext()
        result = StorylineGenerator._build_context_section(ctx)
        assert result == ""

    def test_full_context(self) -> None:
        ctx = UserContext(
            goals=["increase signups"],
            target_audience="developers",
            site_type="SaaS",
            narration_guidance=NarrationGuidance(tone="casual"),
        )
        result = StorylineGenerator._build_context_section(ctx)
        assert "## User Direction" in result
        assert "**Goals:** increase signups" in result
        assert "**Target Audience:** developers" in result
        assert "**Site Type:** SaaS" in result
        assert "**Tone:** casual" in result

    def test_partial_context(self) -> None:
        ctx = UserContext(goals=["demo features"])
        result = StorylineGenerator._build_context_section(ctx)
        assert "**Goals:**" in result
        assert "Target Audience" not in result


class TestPageFiltering:
    def test_exclude_pages(self) -> None:
        """Feature weights with skip should remove chapters."""
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Home",
                    "pages": ["https://example.com/"],
                    "narrative_arc": "home page",
                    "estimated_duration_seconds": 60,
                },
                {
                    "number": 2,
                    "title": "Blog Posts",
                    "pages": ["https://example.com/blog"],
                    "narrative_arc": "blog content",
                    "estimated_duration_seconds": 60,
                },
                {
                    "number": 3,
                    "title": "Pricing Plans",
                    "pages": ["https://example.com/pricing"],
                    "narrative_arc": "pricing info",
                    "estimated_duration_seconds": 60,
                },
            ],
        }
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="blog", priority="skip"),
            ],
        )
        gen = StorylineGenerator()
        result = gen._apply_feature_weights(storyline, ctx)
        assert len(result["chapters"]) == 2
        titles = [ch["title"] for ch in result["chapters"]]
        assert "Blog Posts" not in titles


class TestDurationWeighting:
    def test_high_priority_increases_duration(self) -> None:
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Pricing Plans",
                    "pages": [],
                    "narrative_arc": "pricing",
                    "estimated_duration_seconds": 30,
                },
            ],
        }
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="pricing", priority="high"),
            ],
        )
        gen = StorylineGenerator()
        result = gen._apply_feature_weights(storyline, ctx)
        # high = 1.4x, so 30 * 1.4 = 42
        assert result["chapters"][0]["estimated_duration_seconds"] == 42.0

    def test_low_priority_decreases_duration(self) -> None:
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "About Us Page",
                    "pages": [],
                    "narrative_arc": "about us",
                    "estimated_duration_seconds": 60,
                },
            ],
        }
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="about", priority="low"),
            ],
        )
        gen = StorylineGenerator()
        result = gen._apply_feature_weights(storyline, ctx)
        # low = 0.6x, so 60 * 0.6 = 36
        assert result["chapters"][0]["estimated_duration_seconds"] == 36.0

    def test_high_priority_enforces_minimum(self) -> None:
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Features",
                    "pages": [],
                    "narrative_arc": "features",
                    "estimated_duration_seconds": 10,
                },
            ],
        }
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="features", priority="high"),
            ],
        )
        gen = StorylineGenerator()
        result = gen._apply_feature_weights(storyline, ctx)
        # 10 * 1.4 = 14, but min is 20 for high priority
        assert result["chapters"][0]["estimated_duration_seconds"] == 20.0

    def test_chapters_renumbered_after_skip(self) -> None:
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "First",
                    "pages": [],
                    "narrative_arc": "",
                    "estimated_duration_seconds": 30,
                },
                {
                    "number": 2,
                    "title": "Blog Section",
                    "pages": [],
                    "narrative_arc": "blog",
                    "estimated_duration_seconds": 30,
                },
                {
                    "number": 3,
                    "title": "Third",
                    "pages": [],
                    "narrative_arc": "",
                    "estimated_duration_seconds": 30,
                },
            ],
        }
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="blog", priority="skip"),
            ],
        )
        gen = StorylineGenerator()
        result = gen._apply_feature_weights(storyline, ctx)
        assert len(result["chapters"]) == 2
        assert result["chapters"][0]["number"] == 1
        assert result["chapters"][1]["number"] == 2


class TestAutoDetectDuration:
    def test_few_pages(self) -> None:
        assert StorylineGenerator._auto_detect_duration(3) == 2.0

    def test_medium_pages(self) -> None:
        assert StorylineGenerator._auto_detect_duration(10) == 3.0

    def test_many_pages(self) -> None:
        result = StorylineGenerator._auto_detect_duration(20)
        assert result == min(5.0, 1.5 + 20 * 0.2)


class TestNarrativeExtractor:
    def test_extract_hooks(self) -> None:
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                headings=["Short", "This is a valid heading for extraction"],
            ),
        ]
        extractor = NarrativeExtractor()
        result = extractor.extract(pages)
        assert "This is a valid heading for extraction" in result["hooks"]
        assert "Short" not in result["hooks"]  # Too short (< 10 chars)

    def test_extract_features_and_quotes(self) -> None:
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                content_sections=[
                    {"section_type": "features", "heading": "Lightning Fast"},
                    {"section_type": "testimonials", "text_preview": "Best product ever!"},
                ],
            ),
        ]
        extractor = NarrativeExtractor()
        result = extractor.extract(pages)
        assert "Lightning Fast" in result["feature_highlights"]
        assert "Best product ever!" in result["key_quotes"]

    def test_counts(self) -> None:
        pages = [
            PageInput(url="https://a.com/", title="A", has_forms=True, has_media=True),
            PageInput(url="https://b.com/", title="B", has_forms=False, has_media=True),
            PageInput(url="https://c.com/", title="C"),
        ]
        extractor = NarrativeExtractor()
        result = extractor.extract(pages)
        assert result["total_pages"] == 3
        assert result["pages_with_forms"] == 1
        assert result["pages_with_media"] == 2


class TestChapterPlanner:
    def test_validate_empty_chapters(self) -> None:
        planner = ChapterPlanner()
        warnings = planner.validate({"chapters": []})
        assert any("No chapters" in w for w in warnings)

    def test_validate_too_many_chapters(self) -> None:
        planner = ChapterPlanner()
        chapters = [
            {"number": i, "title": f"Ch{i}", "estimated_duration_seconds": 30} for i in range(11)
        ]
        warnings = planner.validate({"chapters": chapters})
        assert any("Too many" in w for w in warnings)

    def test_validate_no_pages(self) -> None:
        planner = ChapterPlanner()
        warnings = planner.validate(
            {
                "chapters": [{"number": 1, "title": "Ch1", "pages": []}],
            }
        )
        assert any("no pages" in w for w in warnings)

    def test_validate_duration_overage(self) -> None:
        planner = ChapterPlanner(duration_overage_threshold=1.5)
        warnings = planner.validate(
            {
                "target_duration_minutes": 1.0,
                "intro": {"duration_seconds": 8},
                "chapters": [
                    {
                        "number": 1,
                        "title": "Ch1",
                        "pages": ["p1"],
                        "estimated_duration_seconds": 200,
                    },
                ],
                "outro": {"duration_seconds": 6},
            }
        )
        assert any("exceeds target" in w for w in warnings)

    def test_rebalance(self) -> None:
        planner = ChapterPlanner()
        storyline = {
            "target_duration_minutes": 2.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {"number": 1, "title": "Ch1", "estimated_duration_seconds": 60},
                {"number": 2, "title": "Ch2", "estimated_duration_seconds": 120},
            ],
        }
        result = planner.rebalance(storyline)
        # Total available: 120 - 14 = 106s
        # Original total chapters: 180s, ratio ~0.589
        total_ch = sum(ch["estimated_duration_seconds"] for ch in result["chapters"])
        assert total_ch <= 120  # Should fit within target

    def test_rebalance_preserves_original(self) -> None:
        planner = ChapterPlanner()
        original = {
            "target_duration_minutes": 3.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {"number": 1, "title": "Ch1", "estimated_duration_seconds": 60},
            ],
        }
        planner.rebalance(original)
        # Original should not be mutated
        assert original["chapters"][0]["estimated_duration_seconds"] == 60

    def test_rebalance_enforces_minimum(self) -> None:
        planner = ChapterPlanner()
        storyline = {
            "target_duration_minutes": 0.5,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {"number": 1, "title": "Ch1", "estimated_duration_seconds": 60},
                {"number": 2, "title": "Ch2", "estimated_duration_seconds": 60},
                {"number": 3, "title": "Ch3", "estimated_duration_seconds": 60},
            ],
        }
        result = planner.rebalance(storyline)
        for ch in result["chapters"]:
            assert ch["estimated_duration_seconds"] >= 15.0
