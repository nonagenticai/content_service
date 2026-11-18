"""Additional unit tests for NarrativeExtractor to cover missing lines.

Covers: feature section without heading (text_preview fallback path),
edge cases around content_sections processing.
"""

from __future__ import annotations

from src.api.narrative_schemas import PageInput
from src.services.narrative.narrative_extractor import NarrativeExtractor


class TestNarrativeExtractorCoverage:
    def setup_method(self) -> None:
        self.extractor = NarrativeExtractor()

    def test_feature_section_without_heading_uses_text_preview(self) -> None:
        """Feature section with no heading falls back to text_preview first sentence."""
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                content_sections=[
                    {
                        "section_type": "features",
                        "heading": "",
                        "text_preview": "Lightning fast performance. Built for scale.",
                    },
                ],
            ),
        ]
        result = self.extractor.extract(pages)
        assert "Lightning fast performance" in result["feature_highlights"]

    def test_feature_section_without_heading_or_text_preview(self) -> None:
        """Feature section with no heading and no text_preview is skipped."""
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                content_sections=[
                    {
                        "section_type": "features",
                        "heading": "",
                        "text_preview": "",
                    },
                ],
            ),
        ]
        result = self.extractor.extract(pages)
        assert result["feature_highlights"] == []

    def test_feature_section_no_heading_key(self) -> None:
        """Feature section missing the heading key entirely."""
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                content_sections=[
                    {
                        "section_type": "features",
                        "text_preview": "Easy integration with your stack. Deploy in minutes.",
                    },
                ],
            ),
        ]
        result = self.extractor.extract(pages)
        assert "Easy integration with your stack" in result["feature_highlights"]

    def test_long_text_preview_truncated(self) -> None:
        """Feature text_preview is truncated to MAX_FEATURE_PREVIEW_LENGTH."""
        long_text = "A" * 200 + ". Second sentence."
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                content_sections=[
                    {
                        "section_type": "features",
                        "heading": "",
                        "text_preview": long_text,
                    },
                ],
            ),
        ]
        result = self.extractor.extract(pages)
        assert len(result["feature_highlights"]) == 1
        assert len(result["feature_highlights"][0]) <= 100

    def test_heading_length_boundaries(self) -> None:
        """Only headings within MIN_HEADING_LENGTH < len < MAX_HEADING_LENGTH are hooks."""
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                headings=[
                    "Short",  # <= 10 chars, too short
                    "This heading is just right for being a hook",  # within range
                    "A" * 81,  # > 80 chars, too long
                ],
            ),
        ]
        result = self.extractor.extract(pages)
        assert len(result["hooks"]) == 1
        assert result["hooks"][0] == "This heading is just right for being a hook"

    def test_multiple_pages_aggregate(self) -> None:
        """Extract aggregates hooks, quotes, features across multiple pages."""
        pages = [
            PageInput(
                url="https://example.com/",
                title="Home",
                headings=["Welcome to Our Platform"],
                content_sections=[
                    {"section_type": "features", "heading": "Fast Delivery"},
                ],
                has_forms=True,
                has_media=True,
            ),
            PageInput(
                url="https://example.com/about",
                title="About",
                headings=["About Our Company Story"],
                content_sections=[
                    {"section_type": "testimonials", "text_preview": "Amazing service!"},
                ],
                has_forms=False,
                has_media=True,
            ),
        ]
        result = self.extractor.extract(pages)
        assert len(result["hooks"]) == 2
        assert result["total_pages"] == 2
        assert result["pages_with_forms"] == 1
        assert result["pages_with_media"] == 2
        assert "Fast Delivery" in result["feature_highlights"]
        assert "Amazing service!" in result["key_quotes"]
