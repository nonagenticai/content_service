"""Unit tests for narrative service."""

import json

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.services.narrative.shotlist_builder import ShotListBuilder
from src.services.narrative.storyline_generator import StorylineGenerator


class TestStorylineGenerator:
    def setup_method(self) -> None:
        self.gen = StorylineGenerator(settings=None)

    def test_default_storyline(self) -> None:
        long_title = "RaiseMyTickets is an AI-powered enterprise ticketing platform"
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title=long_title,
                    page_type="homepage",
                    page_id="p1",
                ),
                PageInput(
                    url="https://example.com/about",
                    title="About Us",
                    page_type="about",
                    page_id="p2",
                ),
            ],
            site=SiteInput(description="A great website", tone="professional"),
        )
        result = self.gen._default_storyline(analysis, 3.0)
        assert result["video_title"] == "A great website — Product Tour"
        assert len(result["chapters"]) == 2
        # Long title (>40 chars, no separator) is truncated to 50 chars
        assert result["chapters"][0]["title"] == long_title[:50]
        assert result["chapters"][0]["estimated_duration_seconds"] == 83.0
        assert result["intro"]["narrative_text"].startswith("Welcome to A great website.")
        assert result["outro"]["narrative_text"].startswith(
            "That wraps up our tour of A great website."
        )
        assert result["target_duration_minutes"] == 3.0

    def test_default_storyline_no_pages(self) -> None:
        analysis = AnalysisInput(site=SiteInput(description="Empty site"))
        result = self.gen._default_storyline(analysis, 2.0)
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "Empty site Overview"
        assert result["chapters"][0]["estimated_duration_seconds"] == 106.0
        assert result["intro"]["narrative_text"]
        assert result["outro"]["narrative_text"]

    def test_parse_valid_json(self) -> None:
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Test"),
        )
        raw = json.dumps(
            {
                "video_title": "Test Demo",
                "video_description": "A demo",
                "target_duration_minutes": 3.0,
                "tone": "casual",
                "intro": {"title": "Welcome", "subtitle": "Let's go", "duration_seconds": 8},
                "chapters": [
                    {
                        "number": 1,
                        "title": "First Look",
                        "pages": ["https://example.com/"],
                        "narrative_arc": "Introduction",
                        "estimated_duration_seconds": 60,
                    }
                ],
                "outro": {"title": "Thanks", "subtitle": "Visit us", "duration_seconds": 6},
            }
        )
        result = self.gen._parse_storyline(raw, analysis, 3.0)
        assert result["video_title"] == "Test Demo"
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "First Look"

    def test_parse_invalid_json_falls_back(self) -> None:
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Test"),
        )
        result = self.gen._parse_storyline("This is not JSON at all", analysis, 3.0)
        assert "chapters" in result
        assert len(result["chapters"]) >= 1


class TestShotListBuilder:
    def setup_method(self) -> None:
        self.builder = ShotListBuilder()

    def test_build_basic(self) -> None:
        storyline = {
            "intro": {"title": "Welcome", "subtitle": "", "duration_seconds": 8},
            "chapters": [
                {
                    "number": 1,
                    "title": "Getting Started",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 60,
                }
            ],
            "outro": {"title": "Goodbye", "subtitle": "", "duration_seconds": 6},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title="Home",
                    page_id="p1",
                    content_sections=[{"section_type": "hero", "heading": "Welcome"}],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        assert result["total_parts"] >= 3  # intro + ch title + part + outro
        assert "intro" in result["recording_order"]
        assert "outro" in result["recording_order"]

    def test_build_no_pages_chapter(self) -> None:
        storyline = {
            "intro": {"title": "Intro", "duration_seconds": 8},
            "chapters": [
                {"number": 1, "title": "Overview", "pages": [], "estimated_duration_seconds": 30}
            ],
            "outro": {"title": "End", "duration_seconds": 6},
        }
        analysis = AnalysisInput(site=SiteInput(description="Test"))
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert "Navigate to relevant page" in browser_parts[0]["actions"]

    def test_build_multi_chapter(self) -> None:
        storyline = {
            "intro": {"title": "Intro", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["https://example.com/a"],
                    "estimated_duration_seconds": 30,
                },
                {
                    "number": 2,
                    "title": "Ch2",
                    "pages": ["https://example.com/b"],
                    "estimated_duration_seconds": 30,
                },
            ],
            "outro": {"title": "Outro", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(url="https://example.com/a", title="A", page_id="a1"),
                PageInput(url="https://example.com/b", title="B", page_id="b1"),
            ],
            site=SiteInput(description="Multi"),
        )
        result = self.builder.build(storyline, analysis)
        title_cards = [p for p in result["parts"] if p["part_type"] == "title_card"]
        assert len(title_cards) >= 4  # intro + ch1-title + ch2-title + outro

    def test_min_duration_enforcement(self) -> None:
        storyline = {
            "intro": {"title": "I", "duration_seconds": 0.5},
            "chapters": [],
            "outro": {"title": "O", "duration_seconds": 0.5},
        }
        analysis = AnalysisInput(site=SiteInput(description="Test"))
        result = self.builder.build(storyline, analysis)
        for p in result["parts"]:
            assert p["estimated_duration_seconds"] >= 2.0

    def test_widget_selectors_filtered(self) -> None:
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 30,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title="Home",
                    page_id="p1",
                    interactive_elements=[
                        {"selector": "#chatbot-widget", "tag": "div"},
                        {"selector": "#main-content", "tag": "div"},
                    ],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        for part in browser_parts:
            for elem in part.get("key_elements", []):
                assert "chatbot" not in elem.lower()
