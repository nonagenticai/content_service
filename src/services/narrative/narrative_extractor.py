"""Extract narrative hooks, key quotes, and feature highlights from website pages."""

from __future__ import annotations

import logging

from src.api.narrative_schemas import PageInput

logger = logging.getLogger(__name__)

MAX_HOOKS = 10
MAX_KEY_QUOTES = 5
MAX_FEATURE_HIGHLIGHTS = 10
MIN_HEADING_LENGTH = 10
MAX_HEADING_LENGTH = 80
MAX_FEATURE_PREVIEW_LENGTH = 100
MAX_QUOTE_PREVIEW_LENGTH = 150


class NarrativeExtractor:
    """Extract narrative material (compelling angles, key content) from pages."""

    @staticmethod
    def _extract_feature(section: dict, feature_highlights: list[str]) -> None:
        heading = section.get("heading", "")
        if heading:
            feature_highlights.append(heading)
            return
        text_preview = section.get("text_preview", "")
        if text_preview:
            first_sentence = text_preview.split(".")[0].strip()
            if first_sentence:
                feature_highlights.append(first_sentence[:MAX_FEATURE_PREVIEW_LENGTH])

    @classmethod
    def _process_section(
        cls, section: dict, feature_highlights: list[str], key_quotes: list[str]
    ) -> None:
        section_type = section.get("section_type", "content")
        if section_type == "features":
            cls._extract_feature(section, feature_highlights)
        elif section_type == "testimonials":
            text_preview = section.get("text_preview", "")
            if text_preview:
                key_quotes.append(text_preview[:MAX_QUOTE_PREVIEW_LENGTH])

    def extract(self, pages: list[PageInput]) -> dict:
        """Extract narrative hooks from page content.

        Returns a dict with hooks, key_quotes, feature_highlights,
        total_pages, pages_with_forms, pages_with_media.
        """
        hooks: list[str] = []
        key_quotes: list[str] = []
        feature_highlights: list[str] = []

        for page in pages:
            for heading in page.headings:
                if MIN_HEADING_LENGTH < len(heading) < MAX_HEADING_LENGTH:
                    hooks.append(heading)
            for section in page.content_sections:
                self._process_section(section, feature_highlights, key_quotes)

        return {
            "hooks": hooks[:MAX_HOOKS],
            "key_quotes": key_quotes[:MAX_KEY_QUOTES],
            "feature_highlights": feature_highlights[:MAX_FEATURE_HIGHLIGHTS],
            "total_pages": len(pages),
            "pages_with_forms": sum(1 for p in pages if p.has_forms),
            "pages_with_media": sum(1 for p in pages if p.has_media),
        }
