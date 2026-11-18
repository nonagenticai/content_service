"""Additional tests for ShotListBuilder edge cases."""

from __future__ import annotations

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.services.narrative.shotlist_builder import (
    MIN_PART_DURATION_S,
    MIN_TITLE_CARD_DURATION_S,
    ShotListBuilder,
)


class TestShotListBuilderEdgeCases:
    """Cover edge cases not reached by existing tests in test_narrative_service.py."""

    def setup_method(self) -> None:
        self.builder = ShotListBuilder()

    def test_page_lookup_by_page_id(self) -> None:
        """Pages in chapter.pages can be referenced by page_id (not just url)."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["page-id-1"],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/home",
                    title="Home",
                    page_id="page-id-1",
                    content_sections=[{"section_type": "hero", "heading": "Welcome"}],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert browser_parts[0]["pages"] == ["https://example.com/home"]

    def test_page_with_forms_adds_highlight_action(self) -> None:
        """Pages with has_forms=True get a 'Highlight form fields' action."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Signup",
                    "pages": ["https://example.com/signup"],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/signup",
                    title="Signup",
                    page_id="s1",
                    has_forms=True,
                    content_sections=[],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        actions = browser_parts[0]["actions"]
        assert "Highlight form fields" in actions

    def test_page_with_no_extra_actions_gets_scroll(self) -> None:
        """When a page has no content sections/forms, 'Scroll through page content' is appended."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Empty",
                    "pages": ["https://example.com/empty"],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/empty",
                    title="Empty Page",
                    page_id="e1",
                    content_sections=[],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert "Scroll through page content" in browser_parts[0]["actions"]

    def test_unknown_page_entry_uses_raw_string(self) -> None:
        """When a page entry doesn't match any url or page_id, the raw string is used."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Unknown",
                    "pages": ["https://unknown.example.com/"],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert browser_parts[0]["pages"] == ["https://unknown.example.com/"]
        # Title falls back to "Page N" when page is not found
        assert "Page 1" in browser_parts[0]["title"]

    def test_custom_title_card_duration_enforces_minimum(self) -> None:
        """Chapter title card duration cannot go below MIN_TITLE_CARD_DURATION_S."""
        builder = ShotListBuilder(chapter_title_card_duration=0.5)
        assert builder.chapter_title_card_duration >= MIN_TITLE_CARD_DURATION_S

    def test_section_actions_mapping(self) -> None:
        """Content sections with known types get mapped actions."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Rich",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 60,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title="Home",
                    page_id="h1",
                    content_sections=[
                        {"section_type": "hero", "heading": "Welcome"},
                        {"section_type": "testimonials", "text_preview": "Great!"},
                        {"section_type": "pricing", "heading": "Plans"},
                    ],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        actions = browser_parts[0]["actions"]
        assert any("hero" in a.lower() or "highlight main heading" in a.lower() for a in actions)
        assert any("testimonials" in a.lower() or "social proof" in a.lower() for a in actions)
        assert any("pricing" in a.lower() for a in actions)

    def test_total_duration_is_sum_of_parts(self) -> None:
        """estimated_total_duration_seconds equals sum of all part durations."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 10},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["https://example.com/a"],
                    "estimated_duration_seconds": 30,
                },
            ],
            "outro": {"title": "O", "duration_seconds": 8},
        }
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/a", title="A", page_id="a1")],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        expected = sum(p["estimated_duration_seconds"] for p in result["parts"])
        assert result["estimated_total_duration_seconds"] == expected

    def test_multiple_pages_per_chapter(self) -> None:
        """Duration is split evenly among pages in a chapter."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Multi",
                    "pages": ["https://a.com/", "https://b.com/", "https://c.com/"],
                    "estimated_duration_seconds": 90,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(url="https://a.com/", title="A", page_id="a"),
                PageInput(url="https://b.com/", title="B", page_id="b"),
                PageInput(url="https://c.com/", title="C", page_id="c"),
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 3
        # Each should get 90/3 = 30s, which meets MIN_PART_DURATION_S
        for bp in browser_parts:
            assert bp["estimated_duration_seconds"] >= MIN_PART_DURATION_S

    def test_duration_per_page_enforces_minimum(self) -> None:
        """Even with short chapter duration, each page part meets minimum."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Short",
                    "pages": ["https://a.com/", "https://b.com/"],
                    "estimated_duration_seconds": 10,  # 5s each < 20s min
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(url="https://a.com/", title="A", page_id="a"),
                PageInput(url="https://b.com/", title="B", page_id="b"),
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        for bp in browser_parts:
            assert bp["estimated_duration_seconds"] >= MIN_PART_DURATION_S

    def test_unknown_section_type_uses_heading_in_action(self) -> None:
        """Content sections with unknown types fall back to heading-based action."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title="Home",
                    page_id="h1",
                    content_sections=[
                        {"section_type": "custom_widget", "heading": "My Widget"},
                    ],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = self.builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert any("My Widget" in a for a in browser_parts[0]["actions"])
