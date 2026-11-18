"""Build a shot list from a storyline and analysis data."""

from __future__ import annotations

import logging
from typing import Any

from src.api.narrative_schemas import AnalysisInput
from src.services.narrative.degradation import SHOT_MISSING_URL

logger = logging.getLogger(__name__)

MIN_PART_DURATION_S = 20.0
MIN_TITLE_CARD_DURATION_S = 2.0

_WIDGET_SELECTOR_PATTERNS = (
    "chatbot",
    "chat-widget",
    "chat-launcher",
    "intercom",
    "drift",
    "crisp",
    "hubspot",
    "livechat",
    "tawk",
    "cookie",
    "consent",
    "gdpr",
)

_SECTION_ACTIONS = {
    "hero": "Spotlight hero section -- highlight main heading",
    "features": "Scroll to features and highlight key items",
    "testimonials": "Spotlight testimonials -- linger on social proof",
    "cta": "Highlight call-to-action button",
    "pricing": "Show pricing tiers -- highlight each plan",
    "footer": "Scroll to footer and show links",
}


class ShotListBuilder:
    """Break a storyline into individual recordable parts."""

    def __init__(
        self,
        chapter_title_card_duration: float = 5.0,
        min_part_duration: float = MIN_PART_DURATION_S,
        min_title_card_duration: float = MIN_TITLE_CARD_DURATION_S,
    ) -> None:
        self.min_part_duration = min_part_duration
        self.min_title_card_duration = min_title_card_duration
        self.chapter_title_card_duration = max(
            chapter_title_card_duration, self.min_title_card_duration
        )

    @staticmethod
    def _truncate_page_title(page_title: str) -> str:
        if not page_title or len(page_title) <= 50:
            return page_title
        for sep in [" | ", " - ", " — ", ":"]:
            if sep in page_title:
                return page_title.split(sep)[0].strip()
        return page_title[:47] + "..."

    @staticmethod
    def _section_actions(page: Any) -> list[str]:
        actions: list[str] = []
        for section in (page.content_sections or [])[:3]:
            heading = section.get("heading", "").strip()
            st = section.get("section_type", "content")
            if st in _SECTION_ACTIONS:
                actions.append(_SECTION_ACTIONS[st])
            elif heading:
                actions.append(f"Highlight '{heading}'")
        return actions

    @staticmethod
    def _page_key_elements(page: Any) -> list[str]:
        key_elements: list[str] = []
        for e in (page.interactive_elements or [])[:5]:
            sel = e.get("selector", "")
            if sel and not any(p in sel.lower() for p in _WIDGET_SELECTOR_PATTERNS):
                key_elements.append(sel)
        return key_elements

    @classmethod
    def _build_page_actions(
        cls, chapter: dict, page: Any, page_url: str
    ) -> tuple[list[str], list[str]]:
        actions = [f"Navigate to {page_url}"]
        key_elements: list[str] = []
        key_moments = chapter.get("key_moments", [])
        if key_moments:
            actions.extend(f"Highlight '{moment}'" for moment in key_moments if moment)
        elif page:
            actions.extend(cls._section_actions(page))
        if page:
            if page.has_forms:
                actions.append("Highlight form fields")
            key_elements = cls._page_key_elements(page)
        if len(actions) <= 1:
            actions.append("Scroll through page content")
        return actions, key_elements

    def _build_chapter_part(
        self,
        chapter: dict,
        ch_num: int,
        part_num: int,
        page_entry: Any,
        page_by_id: dict,
        page_by_url: dict,
        duration_per_page: float,
    ) -> tuple[str, dict]:
        page = page_by_id.get(page_entry) or page_by_url.get(page_entry)
        page_url = page.url if page else page_entry
        actions, key_elements = self._build_page_actions(chapter, page, page_url)
        page_title = self._truncate_page_title(page.title if page else f"Page {part_num}")
        ch_title = chapter.get("title", "")
        if len(ch_title) > 40:
            ch_title = ch_title[:37] + "..."
        combined_title = (
            f"{ch_title} -- {page_title}" if ch_title and page_title else ch_title or page_title
        )
        part_id = f"ch{ch_num:02d}-part{part_num}"
        return part_id, {
            "id": part_id,
            "part_type": "browser_recording",
            "chapter": ch_num,
            "title": combined_title,
            "subtitle": chapter.get("narrative_arc", ""),
            "pages": [page_url],
            "actions": actions,
            "key_elements": key_elements,
            "estimated_duration_seconds": duration_per_page,
        }

    def _build_chapter(
        self,
        chapter: dict,
        page_by_id: dict,
        page_by_url: dict,
    ) -> tuple[list[dict], list[str]]:
        parts: list[dict] = []
        order: list[str] = []
        ch_num = chapter.get("number", 1)
        ch_title_id = f"ch{ch_num:02d}-title"
        parts.append(
            {
                "id": ch_title_id,
                "part_type": "title_card",
                "chapter": ch_num,
                "title": chapter.get("title", ""),
                "subtitle": chapter.get("subtitle", ""),
                "estimated_duration_seconds": self.chapter_title_card_duration,
            }
        )
        order.append(ch_title_id)
        chapter_pages = chapter.get("pages", [])
        if not chapter_pages:
            # A chapter that declared NO pages gives us no URL to record. The stub
            # below is a placeholder, not a plan — it used to ship with the
            # chapter's FULL duration budget, no `pages`, and no marker, so a
            # recorder could not tell it from a real browser_recording and simply
            # burned the whole budget on "navigate somewhere". That is a
            # measurement gap scoring as a measurement.
            #
            # Make the gap expressible (`degraded: True`, an explicit empty
            # `pages`) and loud (a WARNING naming the chapter), and refuse the
            # confident-looking full budget: an unplanned part gets the floor
            # duration only, so the deficit shows up in the deck's total instead
            # of being silently filled with stub time.
            part_id = f"ch{ch_num:02d}-part1"
            logger.warning(
                "Chapter %s (%r) declared no pages — emitting a DEGRADED "
                "browser_recording stub %s with no URL at the %.1fs floor instead of "
                "its %.1fs budget; nothing concrete can be recorded for it",
                ch_num,
                chapter.get("title", ""),
                part_id,
                self.min_part_duration,
                float(chapter.get("estimated_duration_seconds", 60)),
            )
            parts.append(
                {
                    "id": part_id,
                    "part_type": "browser_recording",
                    "chapter": ch_num,
                    "title": chapter.get("title", ""),
                    "pages": [],
                    "actions": ["Navigate to relevant page", "Explore content"],
                    "estimated_duration_seconds": self.min_part_duration,
                    "degraded": True,
                    # Name the gap. ``degraded: True`` says "not a real plan";
                    # this says WHICH gap, in the vocabulary the top-level
                    # ``ShotListResponse.degradation`` aggregate groups by, so a
                    # caller can tell "nothing to record" from "recorded fine"
                    # without inferring it from an empty ``pages``.
                    "degraded_reason": SHOT_MISSING_URL,
                }
            )
            order.append(part_id)
            return parts, order

        duration_per_page = max(
            float(chapter.get("estimated_duration_seconds", 60)) / max(len(chapter_pages), 1),
            self.min_part_duration,
        )
        for i, page_entry in enumerate(chapter_pages):
            part_id, part = self._build_chapter_part(
                chapter, ch_num, i + 1, page_entry, page_by_id, page_by_url, duration_per_page
            )
            parts.append(part)
            order.append(part_id)
        return parts, order

    def build(self, storyline: dict, analysis: AnalysisInput) -> dict:
        """Build a shot list from a storyline and analysis data.

        Args:
            storyline: Video storyline dict with intro, chapters, and outro.
            analysis: Website analysis with page data and site metadata.

        Returns:
            Shot list dict with parts, recording_order, total_parts,
            and estimated_total_duration_seconds.
        """
        logger.info(
            "ShotListBuilder.build: chapters=%d, pages=%d",
            len(storyline.get("chapters", [])),
            len(analysis.pages),
        )
        parts: list[dict] = []
        recording_order: list[str] = []

        page_by_url = {p.url: p for p in analysis.pages}
        page_by_id = {p.page_id: p for p in analysis.pages if p.page_id}

        # Intro
        intro = storyline.get("intro", {})
        intro_duration = max(float(intro.get("duration_seconds", 8)), self.min_title_card_duration)
        parts.append(
            {
                "id": "intro",
                "part_type": "title_card",
                "title": intro.get("title", ""),
                "subtitle": intro.get("subtitle", ""),
                "estimated_duration_seconds": intro_duration,
            }
        )
        recording_order.append("intro")

        for chapter in storyline.get("chapters", []):
            ch_parts, ch_order = self._build_chapter(chapter, page_by_id, page_by_url)
            parts.extend(ch_parts)
            recording_order.extend(ch_order)

        # Outro
        outro = storyline.get("outro", {})
        outro_duration = max(float(outro.get("duration_seconds", 6)), self.min_title_card_duration)
        parts.append(
            {
                "id": "outro",
                "part_type": "title_card",
                "title": outro.get("title", ""),
                "subtitle": outro.get("subtitle", ""),
                "estimated_duration_seconds": outro_duration,
            }
        )
        recording_order.append("outro")

        total_duration = sum(p["estimated_duration_seconds"] for p in parts)

        logger.info(
            "ShotListBuilder.build: produced %d parts, %.1fs total duration",
            len(parts),
            total_duration,
        )
        return {
            "parts": parts,
            "recording_order": recording_order,
            "total_parts": len(parts),
            "estimated_total_duration_seconds": total_duration,
        }
