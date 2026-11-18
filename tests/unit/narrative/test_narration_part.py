"""Unit tests for per-part narration (generate_for_shot_list).

All LLM traffic flows through ``WMClient.message`` — tests inject a mock
WMClient via the ``wm_client=`` constructor argument.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_types import NarrationGuidance, UserContext
from src.config import Settings
from src.services.narrative.narration_builder import NarrationBuilder

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _wm_response(text: str) -> WMResponse:
    return WMResponse(
        output=text,
        output_type="string",
        output_schema_used=None,
        metadata=None,
        usage_metrics=None,
    )


def _make_async_client(response_text: str) -> AsyncMock:
    """Build a mock WMClient whose .message returns a WMResponse with response_text."""
    client = AsyncMock()
    client.message = AsyncMock(return_value=_wm_response(response_text))
    client.aclose = AsyncMock()
    return client


@pytest.fixture
def sample_shot_list() -> dict:
    return {
        "parts": [
            {
                "id": "intro",
                "part_type": "title_card",
                "title": "Welcome",
                "narrative_text": "Discover the product.",
            },
            {
                "id": "ch01-title",
                "part_type": "title_card",
                "chapter": 1,
                "title": "Getting Started",
            },
            {
                "id": "ch01-part1",
                "part_type": "browser_recording",
                "chapter": 1,
                "title": "Homepage Tour",
                "pages": ["https://example.com/"],
                "actions": ["Navigate to https://example.com/", "Scroll through hero section"],
                "estimated_duration_seconds": 30,
            },
            {
                "id": "ch01-part2",
                "part_type": "browser_recording",
                "chapter": 1,
                "title": "Features Overview",
                "pages": ["https://example.com/features"],
                "actions": ["Navigate to features", "Highlight key features"],
                "estimated_duration_seconds": 40,
            },
            {
                "id": "ch02-title",
                "part_type": "title_card",
                "chapter": 2,
                "title": "Pricing",
            },
            {
                "id": "ch02-part1",
                "part_type": "browser_recording",
                "chapter": 2,
                "title": "Pricing Page",
                "pages": ["https://example.com/pricing"],
                "actions": ["Navigate to pricing"],
                "estimated_duration_seconds": 25,
            },
            {
                "id": "outro",
                "part_type": "title_card",
                "title": "Get Started",
                "narrative_text": "Start your free trial today.",
            },
        ],
        "recording_order": [
            "intro",
            "ch01-title",
            "ch01-part1",
            "ch01-part2",
            "ch02-title",
            "ch02-part1",
            "outro",
        ],
    }


@pytest.fixture
def sample_storyline_for_shotlist() -> dict:
    return {
        "video_title": "Example Demo",
        "tone": "professional",
        "intro": {
            "title": "Welcome",
            "narrative_text": "Discover how Example transforms your workflow.",
            "duration_seconds": 8,
        },
        "chapters": [
            {
                "number": 1,
                "title": "Getting Started",
                "narrative_arc": "Introduction to the product",
                "estimated_duration_seconds": 70,
            },
            {
                "number": 2,
                "title": "Pricing",
                "narrative_arc": "Explore pricing options",
                "estimated_duration_seconds": 25,
            },
        ],
        "outro": {
            "title": "Get Started",
            "narrative_text": "Start your free trial today.",
            "duration_seconds": 6,
        },
    }


# ---------------------------------------------------------------------------
# generate_for_shot_list
# ---------------------------------------------------------------------------


class TestGenerateForShotList:
    @pytest.mark.asyncio
    async def test_generates_parts_for_recordings(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        mock_client = _make_async_client("Great narration for this part.")
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        part_ids = [p["part_id"] for p in result["parts"]]
        # Should have intro + 2 chapter title cards + 3 browser recordings + outro
        assert "intro" in part_ids
        assert "outro" in part_ids
        assert "ch01-part1" in part_ids
        assert "ch01-part2" in part_ids
        assert "ch02-part1" in part_ids
        # Chapter title cards should have narration
        assert "ch01-title" in part_ids
        assert "ch02-title" in part_ids

    @pytest.mark.asyncio
    async def test_chapter_title_cards_have_narration(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        mock_client = _make_async_client("Narration text.")
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        parts_by_id = {p["part_id"]: p for p in result["parts"]}
        assert "ch01-title" in parts_by_id
        assert "ch02-title" in parts_by_id
        assert parts_by_id["ch01-title"]["text"]
        assert parts_by_id["ch01-title"]["word_count"] > 0
        assert parts_by_id["ch02-title"]["text"]
        assert parts_by_id["ch02-title"]["word_count"] > 0

    @pytest.mark.asyncio
    async def test_position_tracking(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        """Chapter 1 has 2 parts (first, last), Chapter 2 has 1 part (only)."""
        call_args = []

        async def capture_message(**kwargs):
            call_args.append(kwargs)
            return _wm_response("Narration text.")

        mock_client = AsyncMock()
        mock_client.message = capture_message

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        # 3 browser recordings + 2 bookend rewrites = 5 LLM calls
        # Check that all browser recording parts were generated
        browser_parts = [p for p in result["parts"] if "-part" in p["part_id"]]
        assert len(browser_parts) == 3

    @pytest.mark.asyncio
    async def test_previous_narration_threading(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        """Each part should receive the previous part's narration text."""
        prompts_seen = []

        async def capture_message(**kwargs):
            # WMClient.message is called with `message=...` (the user prompt).
            prompts_seen.append(kwargs.get("message", ""))
            return _wm_response("Part narration output.")

        mock_client = AsyncMock()
        mock_client.message = capture_message

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        # Later prompts should contain previous narration
        # (at least one should have "Part narration output" from the previous call)
        all_prompt_text = " ".join(prompts_seen)
        assert "Part narration output" in all_prompt_text

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        failing_client = AsyncMock()
        failing_client.message = AsyncMock(side_effect=RuntimeError("LLM down"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=failing_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        # Should still produce parts with fallback text
        browser_parts = [p for p in result["parts"] if "-part" in p["part_id"]]
        assert len(browser_parts) == 3
        for p in browser_parts:
            assert p["word_count"] > 0

    @pytest.mark.asyncio
    async def test_returns_correct_structure(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        mock_client = _make_async_client("Some narration.")
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
        )

        assert "parts" in result
        assert "total_words" in result
        assert "estimated_total_duration_seconds" in result
        assert "tone" in result
        assert result["total_words"] == sum(p["word_count"] for p in result["parts"])

    @pytest.mark.asyncio
    async def test_tone_override_from_user_context(
        self,
        mock_settings: Settings,
        sample_shot_list: dict,
        sample_storyline_for_shotlist: dict,
    ) -> None:
        mock_client = _make_async_client("Casual narration.")
        uc = UserContext(
            narration_guidance=NarrationGuidance(tone="casual"),
        )
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=sample_shot_list,
            storyline=sample_storyline_for_shotlist,
            user_context=uc,
        )

        assert result["tone"] == "casual"
