"""Tests to boost coverage to 90%+.

Covers:
- findings_converter: critique_to_shotlist, reviews_to_shotlist, reviews_to_storyline
- shotlist_enhancer: _parse_json_from_response, ShotListEnhancer class
- narration_builder: generate_for_shot_list, _generate_part_narration, _synthesize_parts
- database/connection: _ensure_database_exists, create_engine_with_retry, health_check, etc.
- database/session: get_db, get_db_session, close_session_maker
- api/routes: helpers, background tasks, endpoint branches
- config: validators, helper methods
- auth/models: User.effective_tenant_id
"""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from tests.conftest import TENANT_A

if TYPE_CHECKING:
    from src.services.narrative.narration_builder import NarrationBuilder

# ---------------------------------------------------------------------------
# findings_converter: critique_to_shotlist
# ---------------------------------------------------------------------------


class TestCritiqueToShotlist:
    def test_basic_critique_to_shotlist(self) -> None:
        """critique_to_shotlist produces parts with intro, chapters, and outro."""
        from src.services.narrative.findings_converter import critique_to_shotlist

        storyline = {
            "intro": {
                "title": "Intro Title",
                "subtitle": "Intro Sub",
                "duration_seconds": 6.0,
            },
            "chapters": [
                {
                    "number": 1,
                    "title": "SEO Growth Areas",
                    "subtitle": "3 opportunities",
                    "pages": ["https://example.com/"],
                },
            ],
            "outro": {
                "title": "Outro Title",
                "subtitle": "Outro Sub",
                "duration_seconds": 5.0,
            },
        }
        findings = [
            {
                "category": "seo",
                "title": "Missing Meta Tags",
                "url": "https://example.com/",
                "selector": "#meta-section",
                "recommendation": "Add meta descriptions",
            },
            {
                "category": "seo",
                "title": "Slow Load",
                "url": "https://example.com/about",
                "recommendation": "Optimize images",
            },
        ]
        scores = {"seo": 45.0}

        result = critique_to_shotlist(storyline, findings, scores)

        assert "parts" in result
        assert "recording_order" in result
        assert result["total_parts"] == len(result["parts"])
        assert result["estimated_total_duration_seconds"] > 0

        # Check intro and outro title cards
        intro_part = result["parts"][0]
        assert intro_part["part_type"] == "title_card"
        assert intro_part["id"] == "findings-intro"
        outro_part = result["parts"][-1]
        assert outro_part["part_type"] == "title_card"
        assert outro_part["id"] == "findings-outro"

    def test_critique_with_no_url_findings(self) -> None:
        """Findings without URLs get summary card parts."""
        from src.services.narrative.findings_converter import critique_to_shotlist

        storyline = {
            "intro": {"title": "I"},
            "chapters": [
                {
                    "number": 1,
                    "title": "Performance Optimizations",
                    "pages": ["https://example.com/"],
                }
            ],
            "outro": {"title": "O"},
        }
        findings = [
            {
                "category": "performance",
                "title": "No URL Finding",
                "recommendation": "Fix it",
            },
        ]

        result = critique_to_shotlist(storyline, findings)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) >= 1
        # The no-url finding gets a summary card
        assert any("Additional Opportunities" in p.get("title", "") for p in browser_parts)

    def test_critique_with_score_card(self) -> None:
        """Score summary card added when scores are provided for a category."""
        from src.services.narrative.findings_converter import critique_to_shotlist

        storyline = {
            "intro": {"title": "I"},
            "chapters": [
                {
                    "number": 1,
                    "title": "Accessibility Opportunities",
                }
            ],
            "outro": {"title": "O"},
        }
        findings = [
            {
                "category": "a11y",
                "title": "Color Contrast",
                "url": "https://example.com/",
            },
        ]
        scores = {"a11y": 72.5}

        result = critique_to_shotlist(storyline, findings, scores)
        score_parts = [p for p in result["parts"] if "Score Overview" in p.get("title", "")]
        assert len(score_parts) == 1
        assert "72.5" in score_parts[0]["actions"][0]


# ---------------------------------------------------------------------------
# findings_converter: reviews_to_shotlist
# ---------------------------------------------------------------------------


class TestReviewsToShotlist:
    def test_basic_reviews_to_shotlist(self) -> None:
        """reviews_to_shotlist produces parts for each chapter page."""
        from src.services.narrative.findings_converter import reviews_to_shotlist

        storyline = {
            "intro": {
                "title": "Review Intro",
                "subtitle": "Highlights",
                "duration_seconds": 6.0,
            },
            "chapters": [
                {
                    "number": 1,
                    "title": "Design Strengths",
                    "subtitle": "Visual excellence",
                    "pages": ["https://example.com/design"],
                    "estimated_duration_seconds": 60,
                },
            ],
            "outro": {
                "title": "Review Outro",
                "duration_seconds": 5.0,
            },
        }
        reviews = {
            "design_review": {"strengths": ["Great layout"]},
        }

        result = reviews_to_shotlist(storyline, reviews)

        assert result["total_parts"] == len(result["parts"])
        assert result["estimated_total_duration_seconds"] > 0
        assert result["parts"][0]["id"] == "review-intro"
        assert result["parts"][-1]["id"] == "review-outro"

    def test_reviews_chapter_without_pages(self) -> None:
        """Chapters with no pages get a summary part."""
        from src.services.narrative.findings_converter import reviews_to_shotlist

        storyline = {
            "intro": {"title": "I"},
            "chapters": [
                {
                    "number": 1,
                    "title": "General Review",
                    "pages": [],
                    "key_moments": ["moment1", "moment2"],
                    "estimated_duration_seconds": 45,
                },
            ],
            "outro": {"title": "O"},
        }
        reviews = {"general_review": {"notes": ["Nice"]}}

        result = reviews_to_shotlist(storyline, reviews)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert "Overview" in browser_parts[0]["title"]

    def test_reviews_chapter_no_pages_no_key_moments(self) -> None:
        """Chapters with no pages and no key_moments get default action."""
        from src.services.narrative.findings_converter import reviews_to_shotlist

        storyline = {
            "intro": {"title": "I"},
            "chapters": [
                {
                    "number": 1,
                    "title": "Empty Chapter",
                    "pages": [],
                    "estimated_duration_seconds": 30,
                },
            ],
            "outro": {"title": "O"},
        }
        reviews = {}

        result = reviews_to_shotlist(storyline, reviews)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert browser_parts[0]["actions"] == ["Show review highlights"]


# ---------------------------------------------------------------------------
# findings_converter: reviews_to_storyline
# ---------------------------------------------------------------------------


class TestReviewsToStoryline:
    def test_reviews_to_storyline_with_strengths_and_opportunities(self) -> None:
        """reviews_to_storyline extracts strengths and opportunities from reviews."""
        from src.services.narrative.findings_converter import reviews_to_storyline

        reviews = {
            "design_review": {
                "strengths": ["Clean layout", "Great colors"],
                "improvement_areas": ["Navbar could be wider"],
            },
        }
        analysis = {"description": "Acme SaaS Tool. The best."}

        result = reviews_to_storyline(reviews, analysis, target_minutes=2.0)

        assert "Acme SaaS Tool" in result["video_title"]
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "Design Strengths & Opportunities"
        assert "Celebrating 2 strong points" in result["chapters"][0]["narrative_arc"]

    def test_reviews_to_storyline_with_url_pages(self) -> None:
        """reviews_to_storyline extracts URLs from review data."""
        from src.services.narrative.findings_converter import reviews_to_storyline

        reviews = {
            "usability_review": {
                "page_link": "https://example.com/",
                "items": [
                    {"url": "https://example.com/settings"},
                ],
            },
        }

        result = reviews_to_storyline(reviews)
        ch = result["chapters"][0]
        assert "https://example.com/" in ch["pages"]
        assert "https://example.com/settings" in ch["pages"]

    def test_reviews_to_storyline_with_empty_reviews(self) -> None:
        """String-only / empty reviews yield no chapters => raise, not a stub 200.

        Previously asserted `len(chapters) == 0`, pinning the #119 gap.
        """
        import pytest

        from src.services.narrative.findings_converter import reviews_to_storyline

        reviews = {
            "design_review": "just a string",
            "usability_review": {},
        }

        with pytest.raises(ValueError, match="zero-chapter storyline"):
            reviews_to_storyline(reviews)

    def test_reviews_to_storyline_no_strengths_uses_default_arc(self) -> None:
        """When no strengths/opportunities found, uses default narrative arc."""
        from src.services.narrative.findings_converter import reviews_to_storyline

        reviews = {
            "content_quality_review": {
                "summary": "The content is adequate.",
            },
        }

        result = reviews_to_storyline(reviews)
        assert len(result["chapters"]) == 1
        assert "thorough look" in result["chapters"][0]["narrative_arc"]


# ---------------------------------------------------------------------------
# findings_converter helpers
# ---------------------------------------------------------------------------


class TestFindingsConverterHelpers:
    def test_site_name_with_long_description(self) -> None:
        """_site_name returns 'Website' for descriptions longer than 60 chars."""
        from src.services.narrative.findings_converter import _site_name

        analysis = {"description": "A" * 100 + ". More text."}
        assert _site_name(analysis) == "Website"

    def test_site_name_none(self) -> None:
        from src.services.narrative.findings_converter import _site_name

        assert _site_name(None) == "Website"

    def test_category_narrative_with_high_severity(self) -> None:
        from src.services.narrative.findings_converter import _category_narrative

        findings = [
            {"severity": "critical", "recommendation": "Fix ASAP"},
            {"severity": "low"},
        ]
        result = _category_narrative("seo", findings)
        assert "1 high-impact" in result
        assert "Actionable recommendations" in result

    def test_review_type_title_unknown(self) -> None:
        from src.services.narrative.findings_converter import _review_type_title

        assert _review_type_title("custom_metric") == "Custom Metric"

    def test_finding_actions_full(self) -> None:
        from src.services.narrative.findings_converter import _finding_actions

        finding = {
            "url": "https://example.com/",
            "selector": "#btn",
            "title": "Button Issue",
            "recommendation": "Make it bigger",
        }
        actions = _finding_actions(finding)
        assert len(actions) == 4
        assert "Navigate to" in actions[0]
        assert "Scroll to" in actions[1]
        assert "Show finding card" in actions[2]
        assert "Display recommendation" in actions[3]

    def test_finding_duration_with_selector_and_recommendation(self) -> None:
        from src.services.narrative.findings_converter import _finding_duration

        finding = {"selector": "#x", "recommendation": "Fix it"}
        duration = _finding_duration(finding)
        assert duration == 15.0 + 5.0 + 3.0

    def test_chapter_to_category_unknown(self) -> None:
        from src.services.narrative.findings_converter import _chapter_to_category

        assert _chapter_to_category("General") == "general"
        assert _chapter_to_category("") == "unknown"


# ---------------------------------------------------------------------------
# shotlist_enhancer: _parse_json_from_response
# ---------------------------------------------------------------------------


class TestParseJsonFromResponse:
    def test_plain_json(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        assert _parse_json_from_response('{"key": "val"}') == {"key": "val"}

    def test_markdown_fenced_json(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        raw = '```json\n{"key": "val"}\n```'
        assert _parse_json_from_response(raw) == {"key": "val"}

    def test_json_embedded_in_text(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        raw = 'Here is the result: {"actions": ["a", "b"]} end'
        result = _parse_json_from_response(raw)
        assert result == {"actions": ["a", "b"]}

    def test_list_json(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        result = _parse_json_from_response('["a", "b"]')
        assert result == ["a", "b"]

    def test_invalid_json_returns_none(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        assert _parse_json_from_response("not json at all") is None

    def test_broken_embedded_json_returns_none(self) -> None:
        from src.services.narrative.shotlist_enhancer import _parse_json_from_response

        assert _parse_json_from_response("before {broken after") is None


# ---------------------------------------------------------------------------
# shotlist_enhancer: ShotListEnhancer class
# ---------------------------------------------------------------------------


class TestShotListEnhancer:
    def _make_settings(self) -> MagicMock:
        settings = MagicMock()
        settings.shotlist.timeout_seconds = 60
        settings.shotlist.ai_creative = True
        return settings

    @pytest.mark.asyncio
    async def test_enhance_returns_original_when_no_template(self) -> None:
        """ShotListEnhancer.enhance returns original when no template loaded."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = ""
        enhancer._jinja_template = None

        shot_list = {"parts": []}
        analysis = AnalysisInput(pages=[])
        result = await enhancer.enhance(shot_list, {}, analysis)
        assert result is shot_list

    @pytest.mark.asyncio
    async def test_enhance_skips_title_cards(self) -> None:
        """enhance() skips title_card parts."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = "dummy"
        enhancer._jinja_template = MagicMock()
        enhancer._jinja_template.render = MagicMock(return_value="prompt")

        shot_list = {
            "parts": [
                {"id": "intro", "part_type": "title_card", "title": "Intro"},
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Demo",
                    "actions": ["Navigate to example.com"],
                    "pages": [],
                },
            ]
        }
        storyline = {"chapters": [{"number": 1, "title": "Ch1", "narrative_arc": "arc"}]}
        analysis = AnalysisInput(pages=[])

        # Mock _enhance_chapter to track calls
        enhancer._enhance_chapter = AsyncMock()

        await enhancer.enhance(shot_list, storyline, analysis)
        enhancer._enhance_chapter.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enhance_chapter_exception_is_caught(self) -> None:
        """enhance() catches exceptions from _enhance_chapter gracefully."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = "dummy"
        enhancer._jinja_template = MagicMock()

        shot_list = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Demo",
                    "actions": ["Do stuff"],
                    "pages": [],
                },
            ]
        }
        storyline = {"chapters": [{"number": 1, "title": "Ch1"}]}
        analysis = AnalysisInput(pages=[])

        enhancer._enhance_chapter = AsyncMock(side_effect=RuntimeError("LLM fail"))

        result = await enhancer.enhance(shot_list, storyline, analysis)
        # Original shot_list returned unchanged
        assert result["parts"][0]["actions"] == ["Do stuff"]

    @pytest.mark.asyncio
    async def test_enhance_chapter_with_llm_response(self) -> None:
        """_enhance_chapter calls LLM and applies parsed actions."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = "template"
        enhancer._jinja_template = MagicMock()
        enhancer._jinja_template.render = MagicMock(return_value="prompt text")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = '{"actions": ["Enhanced action 1"]}'
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=mock_response.choices[0].message.content or "",
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        enhancer._wm = mock_wm

        parts = [
            {
                "id": "ch01-part1",
                "title": "Test Part",
                "actions": ["Navigate to https://example.com", "Do stuff"],
                "pages": ["https://example.com"],
            }
        ]
        story_chapter = {"title": "Ch1", "narrative_arc": "arc", "key_moments": ["m1"]}
        storyline = {"tone": "professional"}
        page_lookup = {}

        await enhancer._enhance_chapter(parts, story_chapter, storyline, page_lookup)

        # Actions should be replaced with Navigate preserved
        assert "Navigate to https://example.com" in parts[0]["actions"]
        assert "Enhanced action 1" in parts[0]["actions"]

    @pytest.mark.asyncio
    async def test_enhance_chapter_empty_response(self) -> None:
        """_enhance_chapter with empty LLM response preserves original actions."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = "template"
        enhancer._jinja_template = MagicMock()
        enhancer._jinja_template.render = MagicMock(return_value="prompt text")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=mock_response.choices[0].message.content or "",
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        enhancer._wm = mock_wm

        parts = [{"id": "ch01-part1", "title": "P", "actions": ["Original"], "pages": []}]
        story_chapter = {"title": "Ch1"}
        storyline = {}

        await enhancer._enhance_chapter(parts, story_chapter, storyline, {})

        assert parts[0]["actions"] == ["Original"]

    @pytest.mark.asyncio
    async def test_enhance_chapter_unparseable_response(self) -> None:
        """_enhance_chapter with unparseable LLM response preserves actions."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        settings = self._make_settings()
        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer.settings = settings
        enhancer.timeout_seconds = 60
        enhancer._template_source = "template"
        enhancer._jinja_template = MagicMock()
        enhancer._jinja_template.render = MagicMock(return_value="prompt text")

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Not valid JSON at all"
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=mock_response.choices[0].message.content or "",
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        enhancer._wm = mock_wm

        parts = [{"id": "ch01-part1", "title": "P", "actions": ["Original"], "pages": []}]
        story_chapter = {"title": "Ch1"}
        storyline = {}

        await enhancer._enhance_chapter(parts, story_chapter, storyline, {})

        assert parts[0]["actions"] == ["Original"]

    @pytest.mark.asyncio
    async def test_enhance_chapter_no_template_early_return(self) -> None:
        """_enhance_chapter returns early when no template."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        enhancer = ShotListEnhancer.__new__(ShotListEnhancer)
        enhancer._jinja_template = None

        parts = [{"actions": ["Original"]}]
        await enhancer._enhance_chapter(parts, {}, {}, {})
        assert parts[0]["actions"] == ["Original"]


class TestApplyEnhancedActions:
    def test_list_response(self) -> None:
        """_apply_enhanced_actions handles list-format response."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        parts = [{"actions": ["Navigate to x", "Old"]}]
        ShotListEnhancer._apply_enhanced_actions(parts, ["New action 1", "New action 2"])
        assert "Navigate to x" in parts[0]["actions"]
        assert "New action 1" in parts[0]["actions"]

    def test_dict_with_parts_key(self) -> None:
        """_apply_enhanced_actions handles dict with 'parts' key."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to a", "Old a"]},
            {"id": "ch01-part2", "actions": ["Navigate to b", "Old b"]},
        ]
        parsed = {
            "parts": [
                {"id": "ch01-part1", "actions": ["New A"]},
                {"id": "ch01-part2", "actions": ["New B"]},
            ]
        }
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "New A" in parts[0]["actions"]
        assert "New B" in parts[1]["actions"]

    def test_dict_parts_unmatched_id_ignored(self) -> None:
        """AI parts whose echoed id matches no real part are ignored; a matching

        id is applied to the correct part (never shifted by position).
        """
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        parts = [{"id": "ch01-part1", "actions": ["Old"]}]
        parsed = {
            "parts": [
                {"id": "ch01-part1", "actions": ["A"]},
                {"id": "ghost", "actions": ["B"]},
            ]
        }
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "A" in parts[0]["actions"]
        assert "B" not in parts[0]["actions"]

    def test_empty_list_response(self) -> None:
        """Empty list response does nothing."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        parts = [{"actions": ["Original"]}]
        ShotListEnhancer._apply_enhanced_actions(parts, [])
        assert parts[0]["actions"] == ["Original"]

    def test_replace_actions_empty_new_actions(self) -> None:
        """_replace_actions with empty actions list does nothing."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        part = {"actions": ["Original"]}
        ShotListEnhancer._replace_actions(part, [])
        assert part["actions"] == ["Original"]

    def test_replace_actions_preserves_nav(self) -> None:
        """_replace_actions preserves Navigate actions."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        part = {"actions": ["Navigate to https://x.com", "Old"]}
        ShotListEnhancer._replace_actions(part, ["New action"])
        assert part["actions"][0] == "Navigate to https://x.com"
        assert "New action" in part["actions"]

    def test_replace_actions_no_nav_needed(self) -> None:
        """When new actions include Navigate, no insertion."""
        from src.services.narrative.shotlist_enhancer import ShotListEnhancer

        part = {"actions": ["Navigate to x", "Old"]}
        ShotListEnhancer._replace_actions(part, ["Navigate to x", "New"])
        assert part["actions"] == ["Navigate to x", "New"]


# ---------------------------------------------------------------------------
# narration_builder: generate_for_shot_list
# ---------------------------------------------------------------------------


class TestNarrationBuilderGenerateForShotList:
    def _make_builder(self) -> NarrationBuilder:
        """Create a NarrationBuilder with mocked LLM client."""
        from src.services.narrative.narration_builder import NarrationBuilder

        settings = MagicMock()
        settings.narrative_llm_timeout_seconds = 90
        settings.narration.words_per_minute = 150
        settings.narration.voice = "af_heart"
        settings.narration.speed = 1.0
        settings.narration.tts_timeout_seconds = 60.0
        settings.narration.duration_safety_margin = 0.85
        settings.narration.max_retry_attempts = 2
        settings.is_production.return_value = False

        return NarrationBuilder(settings)

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_basic(self) -> None:
        """generate_for_shot_list produces narration for browser_recording parts."""
        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "This is great narration text."
        mock_response.usage = None

        # Patch the AsyncOpenAI constructor so the NEW client created inside
        # generate_for_shot_list also returns our mock response.
        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        builder._wm = mock_wm

        shot_list = {
            "parts": [
                {
                    "id": "intro",
                    "part_type": "title_card",
                    "title": "Welcome",
                    "narrative_text": "",
                },
                {
                    "id": "ch01-title",
                    "part_type": "title_card",
                    "chapter": 1,
                    "title": "Chapter 1",
                },
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Demo Feature",
                    "actions": ["Navigate to x", "Click button"],
                    "pages": ["https://example.com"],
                    "estimated_duration_seconds": 30,
                },
                {
                    "id": "outro",
                    "part_type": "title_card",
                    "title": "Goodbye",
                    "narrative_text": "",
                },
            ],
            "recording_order": ["intro", "ch01-title", "ch01-part1", "outro"],
        }
        storyline = {
            "video_title": "Demo",
            "chapters": [{"number": 1, "title": "Ch1", "narrative_arc": "arc"}],
            "intro": {"narrative_text": "Welcome to the demo."},
            "outro": {"narrative_text": "Thanks for watching."},
            "tone": "professional",
        }

        result = await builder.generate_for_shot_list(shot_list, storyline)

        assert "parts" in result
        assert result["total_words"] > 0
        assert result["tone"] == "professional"

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_position_calculation(self) -> None:
        """Position calculation: first, middle, last for multiple parts."""
        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Narration text."
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        builder._wm = mock_wm

        shot_list = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Part 1",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 20,
                },
                {
                    "id": "ch01-part2",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Part 2",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 20,
                },
                {
                    "id": "ch01-part3",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Part 3",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 20,
                },
            ],
            "recording_order": ["ch01-part1", "ch01-part2", "ch01-part3"],
        }
        storyline = {
            "chapters": [{"number": 1, "title": "Ch1"}],
            "tone": "casual",
        }

        result = await builder.generate_for_shot_list(shot_list, storyline)
        assert len(result["parts"]) == 3

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_part_narration_failure(self) -> None:
        """Failure in _generate_part_narration produces fallback text."""
        builder = self._make_builder()

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(side_effect=RuntimeError("LLM down"))
        builder._wm = mock_wm

        shot_list = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "Feature Demo",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 30,
                },
            ],
            "recording_order": ["ch01-part1"],
        }
        storyline = {"chapters": [{"number": 1, "title": "Ch1"}], "tone": "pro"}

        result = await builder.generate_for_shot_list(shot_list, storyline)
        assert "Feature Demo" in result["parts"][0]["text"]

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_with_user_context(self) -> None:
        """generate_for_shot_list respects user_context tone override."""
        from src.api.narrative_types import NarrationGuidance, UserContext

        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Text."
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        builder._wm = mock_wm

        shot_list = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "P",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 20,
                },
            ],
            "recording_order": ["ch01-part1"],
        }
        storyline = {"chapters": [{"number": 1, "title": "Ch1"}], "tone": "formal"}
        user_context = UserContext(
            goals=["sell more"],
            narration_guidance=NarrationGuidance(tone="casual"),
        )

        result = await builder.generate_for_shot_list(
            shot_list, storyline, user_context=user_context
        )
        assert result["tone"] == "casual"

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_with_audio_synthesis(self) -> None:
        """generate_for_shot_list calls _synthesize_parts when synthesize_audio=True."""
        from src.services.narrative.narration_builder import NarrationBuilder

        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Text content."
        mock_response.usage = None

        mock_wm = AsyncMock()
        mock_wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        builder._wm = mock_wm

        shot_list = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "chapter": 1,
                    "title": "P",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 20,
                },
            ],
            "recording_order": ["ch01-part1"],
        }
        storyline = {"chapters": [{"number": 1, "title": "Ch1"}], "tone": "pro"}

        mock_synthesize = AsyncMock()
        with patch.object(NarrationBuilder, "_synthesize_parts", mock_synthesize):
            await builder.generate_for_shot_list(shot_list, storyline, synthesize_audio=True)
        mock_synthesize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_generate_for_shot_list_missing_part_in_order(self) -> None:
        """Parts in recording_order that don't exist in parts dict are skipped."""
        builder = self._make_builder()

        shot_list = {
            "parts": [],
            "recording_order": ["missing-part"],
        }
        storyline = {"chapters": [], "tone": "pro"}

        result = await builder.generate_for_shot_list(shot_list, storyline)
        assert len(result["parts"]) == 0


# ---------------------------------------------------------------------------
# narration_builder: _generate_part_narration template branches
# ---------------------------------------------------------------------------


class TestGeneratePartNarration:
    def _make_builder(self):
        from src.services.narrative.narration_builder import NarrationBuilder

        settings = MagicMock()
        settings.narrative_llm_timeout_seconds = 90
        settings.narration.words_per_minute = 150
        settings.narration.voice = "af_heart"
        settings.narration.speed = 1.0
        settings.narration.tts_timeout_seconds = 60.0
        settings.narration.duration_safety_margin = 0.85
        settings.narration.max_retry_attempts = 2
        settings.is_production.return_value = False

        return NarrationBuilder(settings)

    @pytest.mark.asyncio
    async def test_part_narration_empty_llm_response(self) -> None:
        """Empty LLM response gets fallback text."""
        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = ""
        mock_response.usage = None
        builder._wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )

        part = {
            "id": "ch01-part1",
            "title": "Features",
            "actions": [],
            "pages": [],
            "estimated_duration_seconds": 30,
        }
        result = await builder._generate_part_narration(
            part=part, chapter={"title": "Ch1"}, tone="pro"
        )
        assert "Features" in result["text"]
        assert "key capabilities" in result["text"]

    @pytest.mark.asyncio
    async def test_part_narration_critique_mode_uses_mode_template(self) -> None:
        """critique_video mode attempts to use its own template."""
        builder = self._make_builder()

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = "Critique narration text."
        mock_response.usage = None
        builder._wm.message = AsyncMock(
            return_value=WMResponse(
                output=str(mock_response.choices[0].message.content),
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )

        part = {
            "id": "ch01-part1",
            "title": "SEO Review",
            "actions": ["Check SEO"],
            "pages": [],
            "estimated_duration_seconds": 25,
        }
        result = await builder._generate_part_narration(
            part=part,
            chapter={"title": "SEO"},
            tone="consultant",
            mode="critique_video",
        )
        assert result["text"] == "Critique narration text."


# ---------------------------------------------------------------------------
# database/session
# ---------------------------------------------------------------------------


class TestDatabaseSession:
    @pytest.mark.asyncio
    async def test_get_db_commits_on_success(self) -> None:
        """get_db commits session on successful yield."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.close = AsyncMock()

        mock_maker = MagicMock()

        @asynccontextmanager
        async def mock_session_ctx():
            yield mock_session

        mock_maker.return_value = mock_session
        mock_maker.side_effect = None
        # Need the async context manager behavior
        mock_maker_instance = MagicMock()
        mock_maker_instance.__aenter__ = AsyncMock(return_value=mock_session)
        mock_maker_instance.__aexit__ = AsyncMock(return_value=False)
        mock_maker.return_value = mock_maker_instance

        with patch(
            "w2v_common.database.session.get_session_maker",
            new_callable=AsyncMock,
            return_value=mock_maker,
        ):
            from src.database.session import get_db

            gen = get_db()
            session = await gen.__anext__()
            assert session is mock_session
            # Complete generator (triggers commit)
            with suppress(StopAsyncIteration):
                await gen.__anext__()

    @pytest.mark.asyncio
    async def test_get_db_session_context_manager(self) -> None:
        """get_db_session works as async context manager."""
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.close = AsyncMock()

        mock_maker_instance = MagicMock()
        mock_maker_instance.__aenter__ = AsyncMock(return_value=mock_session)
        mock_maker_instance.__aexit__ = AsyncMock(return_value=False)

        mock_maker = MagicMock(return_value=mock_maker_instance)

        with patch(
            "w2v_common.database.session.get_session_maker",
            new_callable=AsyncMock,
            return_value=mock_maker,
        ):
            from src.database.session import get_db_session

            async with get_db_session() as session:
                assert session is mock_session

    @pytest.mark.asyncio
    async def test_close_session_maker(self) -> None:
        """close_session_maker resets the global session maker."""
        import w2v_common.database.session as session_mod

        original = session_mod._async_session_maker
        try:
            session_mod._async_session_maker = MagicMock()
            await session_mod.close_session_maker()
            assert session_mod._async_session_maker is None
        finally:
            session_mod._async_session_maker = original


# ---------------------------------------------------------------------------
# config: validators and helper methods
# ---------------------------------------------------------------------------


class TestConfigHelpers:
    def test_get_database_url(self) -> None:
        from src.config import Settings

        s = Settings(environment="test")
        assert "postgresql" in s.get_database_url()

    def test_get_redis_url(self) -> None:
        from src.config import Settings

        s = Settings(environment="test")
        assert "redis" in s.get_redis_url()

    def test_is_production(self) -> None:
        from src.config import Settings

        s = Settings(environment="production")
        assert s.is_production() is True
        assert s.is_development() is False

    def test_is_development(self) -> None:
        from src.config import Settings

        s = Settings(environment="development")
        assert s.is_development() is True
        assert s.is_production() is False

    def test_api_cors_origins_from_list(self) -> None:
        from src.config import APISettings

        s = APISettings(cors_origins=["http://a.com", "http://b.com"])
        assert s.cors_origins == ["http://a.com", "http://b.com"]

    def test_api_cors_origins_empty_list(self) -> None:
        from src.config import APISettings

        s = APISettings(cors_origins=[])
        assert s.cors_origins == []

    def test_api_cors_origins_single(self) -> None:
        from src.config import APISettings

        s = APISettings(cors_origins=["http://a.com"])
        assert s.cors_origins == ["http://a.com"]

    def test_logging_validate_level_invalid(self) -> None:
        from src.config import LoggingSettings

        with pytest.raises(Exception, match="Invalid log level"):
            LoggingSettings(level="NOTVALID")

    def test_storyline_validate_duration_invalid(self) -> None:
        from src.config import StorylineSettings

        with pytest.raises(Exception, match="target_duration_minutes"):
            StorylineSettings(target_duration_minutes=20.0)

    def test_storyline_validate_duration_zero_is_auto(self) -> None:
        from src.config import StorylineSettings

        s = StorylineSettings(target_duration_minutes=0)
        assert s.target_duration_minutes == 0

    def test_storyline_validate_overage_threshold_low(self) -> None:
        from src.config import StorylineSettings

        with pytest.raises(Exception, match="duration_overage_threshold"):
            StorylineSettings(duration_overage_threshold=0.5)

    def test_narration_validate_wpm_invalid(self) -> None:
        from src.config import NarrationSettings

        with pytest.raises(Exception, match="words_per_minute"):
            NarrationSettings(words_per_minute=0)

    def test_narration_validate_tts_timeout_invalid(self) -> None:
        from src.config import NarrationSettings

        with pytest.raises(Exception, match="tts_timeout_seconds"):
            NarrationSettings(tts_timeout_seconds=0)

    def test_shotlist_validate_timeout_invalid(self) -> None:
        from src.config import ShotlistSettings

        with pytest.raises(Exception, match="timeout_seconds"):
            ShotlistSettings(timeout_seconds=0)


# ---------------------------------------------------------------------------
# auth/models: User.effective_tenant_id
# ---------------------------------------------------------------------------


class TestUserModel:
    def test_effective_tenant_id_with_tenant(self) -> None:
        from src.auth.models import User

        u = User(sub="sub-id", tenant_id=TENANT_A)
        assert u.effective_tenant_id == TENANT_A

    def test_effective_tenant_id_refuses_to_fall_back_to_sub(self) -> None:
        """A tenant-less User raises rather than self-naming a tenant from ``sub``.

        This asserted the fallback (``effective_tenant_id == "sub-id"``), which
        minted a tenant id that was never registered anywhere. w2v-common 0.67.0
        raises ``MissingTenantError``; the refusal is what the suite now pins.
        """
        from w2v_common.tenant import MissingTenantError

        from src.auth.models import User

        u = User(sub="sub-id", tenant_id=None)
        with pytest.raises(MissingTenantError):
            _ = u.effective_tenant_id


# ---------------------------------------------------------------------------
# shotlist_builder: interactive elements filtering
# ---------------------------------------------------------------------------


class TestShotListBuilderInteractiveElements:
    def test_interactive_elements_filtering(self) -> None:
        """Widget selectors are filtered out from key_elements."""
        from src.services.narrative.shotlist_builder import ShotListBuilder

        builder = ShotListBuilder()
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
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
                    interactive_elements=[
                        {"selector": "#main-cta-button"},
                        {"selector": ".chatbot-widget"},  # Should be filtered
                        {"selector": ".cookie-consent"},  # Should be filtered
                        {"selector": "#signup-form"},
                    ],
                )
            ],
            site=SiteInput(description="Test"),
        )
        result = builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        key_elements = browser_parts[0].get("key_elements", [])
        assert "#main-cta-button" in key_elements
        assert "#signup-form" in key_elements
        assert ".chatbot-widget" not in key_elements
        assert ".cookie-consent" not in key_elements

    def test_chapter_with_no_pages_gets_fallback(self) -> None:
        """Chapters with no pages get a fallback browser_recording part."""
        from src.services.narrative.shotlist_builder import ShotListBuilder

        builder = ShotListBuilder()
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "No Pages",
                    "pages": [],
                    "estimated_duration_seconds": 40,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        analysis = AnalysisInput(pages=[], site=SiteInput(description="Test"))
        result = builder.build(storyline, analysis)
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert "Navigate to relevant page" in browser_parts[0]["actions"]
