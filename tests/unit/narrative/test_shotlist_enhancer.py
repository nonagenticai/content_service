"""Tests for ShotListEnhancer service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.config import Settings
from src.services.narrative.shotlist_enhancer import ShotListEnhancer, _parse_json_from_response

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enhancer(
    settings: Settings | None = None,
    wm_client: AsyncMock | None = None,
) -> ShotListEnhancer:
    """Create a ShotListEnhancer with real template files on disk."""
    s = settings or Settings(environment="test", app_name="Test")
    return ShotListEnhancer(settings=s, mode="webapp_commercial", wm_client=wm_client)


def _wm_response(text: str) -> WMResponse:
    return WMResponse(
        output=text,
        output_type="string",
        output_schema_used=None,
        metadata=None,
        usage_metrics=None,
    )


def _make_wm_client(responses: list[WMResponse] | None = None, side_effect=None) -> AsyncMock:
    """Build a mock WMClient for shotlist tests."""
    client = AsyncMock()
    if side_effect is not None:
        client.message = AsyncMock(side_effect=side_effect)
    elif responses is not None and len(responses) == 1:
        client.message = AsyncMock(return_value=responses[0])
    elif responses is not None:
        client.message = AsyncMock(side_effect=responses)
    else:
        client.message = AsyncMock(return_value=_wm_response(""))
    client.aclose = AsyncMock()
    return client


def _make_analysis() -> AnalysisInput:
    return AnalysisInput(
        pages=[
            PageInput(
                url="https://example.com/",
                title="Home",
                page_type="homepage",
                page_id="p1",
                headings=["Welcome"],
            ),
            PageInput(
                url="https://example.com/pricing",
                title="Pricing",
                page_type="pricing",
                page_id="p2",
            ),
        ],
        site=SiteInput(description="A demo site", tone="professional"),
    )


def _make_shot_list() -> dict:
    return {
        "parts": [
            {
                "id": "part-1",
                "part_type": "chapter",
                "chapter": 1,
                "title": "Getting Started",
                "pages": ["https://example.com/"],
                "actions": [
                    "Navigate to https://example.com/",
                    "Scroll down to features section",
                    "Click signup button",
                ],
            },
            {
                "id": "part-2",
                "part_type": "chapter",
                "chapter": 2,
                "title": "Pricing",
                "pages": ["https://example.com/pricing"],
                "actions": [
                    "Navigate to https://example.com/pricing",
                    "Highlight the Pro plan",
                ],
            },
        ],
    }


def _make_storyline() -> dict:
    return {
        "tone": "professional",
        "chapters": [
            {
                "number": 1,
                "title": "Getting Started",
                "narrative_arc": "Introduction to the product",
                "key_moments": ["hero section"],
            },
            {
                "number": 2,
                "title": "Pricing",
                "narrative_arc": "Explore pricing options",
                "key_moments": ["pricing tiers"],
            },
        ],
    }


# ===========================================================================
# _parse_json_from_response
# ===========================================================================


class TestParseJsonFromResponse:
    def test_valid_json_object(self) -> None:
        raw = '{"actions": ["Scroll down", "Click button"]}'
        result = _parse_json_from_response(raw)
        assert result == {"actions": ["Scroll down", "Click button"]}

    def test_json_wrapped_in_markdown_fences(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        result = _parse_json_from_response(raw)
        assert result == {"key": "value"}

    def test_json_embedded_in_text(self) -> None:
        raw = 'Here is the result:\n{"actions": ["a1"]}\nHope that helps!'
        result = _parse_json_from_response(raw)
        assert result == {"actions": ["a1"]}

    def test_invalid_json_returns_none(self) -> None:
        raw = "This is not JSON at all."
        result = _parse_json_from_response(raw)
        assert result is None

    def test_valid_json_array(self) -> None:
        raw = '["action one", "action two"]'
        result = _parse_json_from_response(raw)
        assert result == ["action one", "action two"]

    def test_empty_string_returns_none(self) -> None:
        result = _parse_json_from_response("")
        assert result is None


# ===========================================================================
# _replace_actions
# ===========================================================================


class TestReplaceActions:
    def test_preserves_navigate_to_action_at_start(self) -> None:
        part = {
            "actions": [
                "Navigate to https://example.com/",
                "Scroll down",
            ],
        }
        new_actions = ["Highlight hero banner", "Click CTA"]
        ShotListEnhancer._replace_actions(part, new_actions)
        assert part["actions"][0] == "Navigate to https://example.com/"
        assert "Highlight hero banner" in part["actions"]
        assert "Click CTA" in part["actions"]

    def test_empty_new_actions_does_nothing(self) -> None:
        part = {
            "actions": ["Navigate to https://example.com/", "Scroll down"],
        }
        ShotListEnhancer._replace_actions(part, [])
        assert part["actions"] == ["Navigate to https://example.com/", "Scroll down"]

    def test_actions_without_original_navigation(self) -> None:
        part = {"actions": ["Scroll down", "Click button"]}
        new_actions = ["Hover over logo", "Click signup"]
        ShotListEnhancer._replace_actions(part, new_actions)
        assert part["actions"] == ["Hover over logo", "Click signup"]

    def test_navigation_already_present_in_new_actions(self) -> None:
        part = {
            "actions": ["Navigate to https://example.com/", "Scroll down"],
        }
        new_actions = ["Navigate to https://example.com/new", "Click CTA"]
        ShotListEnhancer._replace_actions(part, new_actions)
        # Should NOT prepend original nav because new actions already contain one
        assert part["actions"] == [
            "Navigate to https://example.com/new",
            "Click CTA",
        ]


# ===========================================================================
# _apply_enhanced_actions
# ===========================================================================


class TestApplyEnhancedActions:
    def test_single_page_format(self) -> None:
        parts = [
            {"actions": ["Navigate to https://example.com/", "Old action"]},
        ]
        parsed = {"actions": ["New creative action", "Another action"]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "New creative action" in parts[0]["actions"]
        assert "Another action" in parts[0]["actions"]

    def test_multi_page_format(self) -> None:
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = {
            "parts": [
                {"id": "ch01-part1", "actions": ["Enhanced A1", "Enhanced A2"]},
                {"id": "ch01-part2", "actions": ["Enhanced B1"]},
            ],
        }
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "Enhanced A1" in parts[0]["actions"]
        assert "Enhanced B1" in parts[1]["actions"]

    def test_multi_page_reordered_ai_parts_map_by_id_not_position(self) -> None:
        """Regression (@clip misalignment): if the LLM reorders parts, actions

        must follow the echoed ``id``, NOT list position, so an enhanced action
        never lands on the wrong shot.
        """
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        # AI returns the parts in the OPPOSITE order.
        parsed = {
            "parts": [
                {"id": "ch01-part2", "actions": ["Pricing enhanced"]},
                {"id": "ch01-part1", "actions": ["Home enhanced"]},
            ],
        }
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "Home enhanced" in parts[0]["actions"]
        assert "Pricing enhanced" in parts[1]["actions"]
        # No cross-contamination.
        assert "Pricing enhanced" not in parts[0]["actions"]
        assert "Home enhanced" not in parts[1]["actions"]

    def test_multi_page_omitted_ai_part_keeps_original(self) -> None:
        """If the LLM omits a part, the omitted part keeps its ORIGINAL actions

        and the returned part is applied to the correct shot (never shifted up).
        """
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        # Only the SECOND part is returned.
        parsed = {"parts": [{"id": "ch01-part2", "actions": ["Only pricing enhanced"]}]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        # Part 1 untouched.
        assert parts[0]["actions"] == ["Navigate to https://example.com/", "Old 1"]
        # Part 2 correctly enhanced — NOT shifted onto part 1.
        assert "Only pricing enhanced" in parts[1]["actions"]

    def test_toplevel_array_of_part_objects_aligns_by_key(self) -> None:
        """A top-level ARRAY of per-part objects must be aligned by echoed id
        like {"parts": [...]}, not str()-ified onto parts[0]. Regression: the old
        code did str(a) on each dict, producing "{'id': ...}" garbage actions on
        the (single) first shot and dropping the rest."""
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = [
            {"id": "ch01-part1", "actions": ["Home enhanced"]},
            {"id": "ch01-part2", "actions": ["Pricing enhanced"]},
        ]
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "Home enhanced" in parts[0]["actions"]
        assert "Pricing enhanced" in parts[1]["actions"]
        # No str(dict) garbage leaked onto any shot.
        joined = " ".join(parts[0]["actions"] + parts[1]["actions"])
        assert "{" not in joined and "'id'" not in joined

    def test_toplevel_array_of_strings_multipart_not_broadcast(self) -> None:
        """A flat top-level list of action strings carries no per-part structure,
        so a multi-part chapter must treat it as unparseable (originals kept),
        never broadcast onto parts[0] — mirroring the {"actions": [...]} guard."""
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = ["Flat action A", "Flat action B"]
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert parts[0]["actions"] == ["Navigate to https://example.com/", "Old 1"]
        assert parts[1]["actions"] == ["Navigate to https://example.com/pricing", "Old 2"]

    def test_toplevel_array_of_strings_singlepart_applied(self) -> None:
        """A flat top-level list on a SINGLE-part chapter is still applied (like
        the single-part {"actions": [...]} case)."""
        parts = [{"actions": ["Navigate to https://example.com/", "Old"]}]
        parsed = ["Flat creative action"]
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "Flat creative action" in parts[0]["actions"]

    def test_multi_page_unknown_id_keeps_all_originals(self) -> None:
        """An AI part echoing an id that matches no real part is dropped."""
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = {"parts": [{"id": "hallucinated-id", "actions": ["Wrong"]}]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert parts[0]["actions"] == ["Navigate to https://example.com/", "Old 1"]
        assert parts[1]["actions"] == ["Navigate to https://example.com/pricing", "Old 2"]
        assert "Wrong" not in parts[0]["actions"]
        assert "Wrong" not in parts[1]["actions"]

    def test_multi_page_missing_key_keeps_originals(self) -> None:
        """AI parts with NO echoed key resolve to no part — originals kept

        (the old code applied these positionally, corrupting the mapping).
        """
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = {"parts": [{"actions": ["A"]}, {"actions": ["B"]}]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert parts[0]["actions"] == ["Navigate to https://example.com/", "Old 1"]
        assert parts[1]["actions"] == ["Navigate to https://example.com/pricing", "Old 2"]

    def test_multi_page_duplicate_id_first_wins_no_double_apply(self) -> None:
        """A duplicated echoed id claims its target once; the duplicate is dropped."""
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to https://example.com/", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to https://example.com/pricing", "Old 2"]},
        ]
        parsed = {
            "parts": [
                {"id": "ch01-part1", "actions": ["First wins"]},
                {"id": "ch01-part1", "actions": ["Second dropped"]},
            ],
        }
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "First wins" in parts[0]["actions"]
        assert "Second dropped" not in parts[0]["actions"]
        # Part 2 never claimed -> original kept.
        assert parts[1]["actions"] == ["Navigate to https://example.com/pricing", "Old 2"]

    def test_multi_page_title_fallback_when_unique(self) -> None:
        """When no id is echoed, a UNIQUE echoed title is an acceptable key."""
        parts = [
            {"id": "ch01-part1", "title": "Home", "actions": ["Navigate to /", "Old 1"]},
            {"id": "ch01-part2", "title": "Pricing", "actions": ["Navigate to /pricing", "Old 2"]},
        ]
        parsed = {"parts": [{"title": "Pricing", "actions": ["Pricing enhanced"]}]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert parts[0]["actions"] == ["Navigate to /", "Old 1"]
        assert "Pricing enhanced" in parts[1]["actions"]

    def test_flat_actions_with_multiple_parts_kept_original(self) -> None:
        """Bug 2: a bare {"actions": [...]} must NOT be broadcast to every part

        of a multi-part chapter — that would collapse per-part distinction.
        """
        parts = [
            {"id": "ch01-part1", "actions": ["Navigate to /", "Old 1"]},
            {"id": "ch01-part2", "actions": ["Navigate to /pricing", "Old 2"]},
        ]
        parsed = {"actions": ["Broadcast me everywhere"]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert parts[0]["actions"] == ["Navigate to /", "Old 1"]
        assert parts[1]["actions"] == ["Navigate to /pricing", "Old 2"]

    def test_flat_actions_with_single_part_applies(self) -> None:
        """Bug 2: a flat {"actions": [...]} is unambiguous for a single-part chapter."""
        parts = [{"id": "ch01-part1", "actions": ["Navigate to /", "Old"]}]
        parsed = {"actions": ["Enhanced single"]}
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "Enhanced single" in parts[0]["actions"]

    def test_direct_list_format(self) -> None:
        parts = [
            {"actions": ["Navigate to https://example.com/", "Old action"]},
        ]
        parsed = ["List action 1", "List action 2"]
        ShotListEnhancer._apply_enhanced_actions(parts, parsed)
        assert "List action 1" in parts[0]["actions"]
        assert "List action 2" in parts[0]["actions"]

    def test_empty_parsed_data(self) -> None:
        parts = [
            {"actions": ["Navigate to https://example.com/", "Original"]},
        ]
        ShotListEnhancer._apply_enhanced_actions(parts, {})
        # No changes -- original actions preserved
        assert parts[0]["actions"] == [
            "Navigate to https://example.com/",
            "Original",
        ]


# ===========================================================================
# enhance() -- async, mock OpenAI
# ===========================================================================


class TestEnhance:
    @pytest.mark.asyncio
    async def test_empty_template_returns_original(self) -> None:
        """When no template is found, enhance() returns shot_list unchanged."""
        settings = Settings(environment="test", app_name="Test")
        enhancer = ShotListEnhancer(settings=settings, mode="nonexistent_mode_xyz")
        # Force template to be missing
        enhancer._jinja_template = None
        enhancer._template_source = ""

        shot_list = _make_shot_list()
        original_parts = json.loads(json.dumps(shot_list))

        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())
        assert result["parts"] == original_parts["parts"]

    @pytest.mark.asyncio
    async def test_valid_llm_response_enhances_actions(self) -> None:
        """A successful LLM response replaces the chapter actions."""
        enhancer = _make_enhancer()
        shot_list = _make_shot_list()
        storyline = _make_storyline()
        analysis = _make_analysis()

        ai_response_ch1 = json.dumps({"actions": ["AI action 1", "AI action 2"]})
        ai_response_ch2 = json.dumps({"actions": ["AI pricing action"]})
        wm_client = _make_wm_client(
            responses=[_wm_response(ai_response_ch1), _wm_response(ai_response_ch2)]
        )
        enhancer._wm = wm_client

        result = await enhancer.enhance(shot_list, storyline, analysis)

        # Chapter 1 actions should contain the AI-generated ones
        ch1_part = result["parts"][0]
        assert "AI action 1" in ch1_part["actions"]
        assert "AI action 2" in ch1_part["actions"]
        # Navigate should be preserved since AI actions lack it
        assert ch1_part["actions"][0].startswith("Navigate to")

        # Chapter 2
        ch2_part = result["parts"][1]
        assert "AI pricing action" in ch2_part["actions"]

    @pytest.mark.asyncio
    async def test_invalid_llm_response_preserves_originals(self) -> None:
        """If the LLM returns unparseable text, original actions are kept."""
        enhancer = _make_enhancer()
        shot_list = _make_shot_list()
        original_actions_ch1 = list(shot_list["parts"][0]["actions"])
        original_actions_ch2 = list(shot_list["parts"][1]["actions"])

        wm_client = _make_wm_client(responses=[_wm_response("Sorry, I cannot help with that.")])
        enhancer._wm = wm_client

        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())

        assert result["parts"][0]["actions"] == original_actions_ch1
        assert result["parts"][1]["actions"] == original_actions_ch2

    @pytest.mark.asyncio
    async def test_title_card_parts_are_skipped(self) -> None:
        """Parts with part_type='title_card' should not be sent to the LLM."""
        enhancer = _make_enhancer()
        shot_list = {
            "parts": [
                {
                    "id": "tc-1",
                    "part_type": "title_card",
                    "chapter": 1,
                    "title": "Chapter 1 Title Card",
                    "actions": ["Show title card"],
                },
                {
                    "id": "part-1",
                    "part_type": "chapter",
                    "chapter": 1,
                    "title": "Getting Started",
                    "pages": ["https://example.com/"],
                    "actions": ["Navigate to https://example.com/", "Scroll down"],
                },
            ],
        }
        storyline = {
            "tone": "professional",
            "chapters": [
                {
                    "number": 1,
                    "title": "Getting Started",
                    "narrative_arc": "Intro",
                    "key_moments": ["hero"],
                },
            ],
        }

        ai_response = json.dumps({"actions": ["Enhanced action"]})
        wm_client = _make_wm_client(responses=[_wm_response(ai_response)])
        enhancer._wm = wm_client

        result = await enhancer.enhance(shot_list, storyline, _make_analysis())

        # Title card actions untouched
        assert result["parts"][0]["actions"] == ["Show title card"]
        # Chapter part got enhanced
        assert "Enhanced action" in result["parts"][1]["actions"]
        # WM called exactly once (only for the chapter part, not title card)
        assert wm_client.message.call_count == 1

    @pytest.mark.asyncio
    async def test_llm_exception_preserves_originals(self) -> None:
        """If the LLM raises, original actions for that chapter are kept."""
        enhancer = _make_enhancer()
        shot_list = _make_shot_list()
        original_actions_ch1 = list(shot_list["parts"][0]["actions"])
        original_actions_ch2 = list(shot_list["parts"][1]["actions"])

        wm_client = _make_wm_client(side_effect=RuntimeError("API rate limit exceeded"))
        enhancer._wm = wm_client

        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())

        # Both chapters should keep their originals since both LLM calls failed
        assert result["parts"][0]["actions"] == original_actions_ch1
        assert result["parts"][1]["actions"] == original_actions_ch2

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("WM connection refused"),
            httpx.ReadError("WM read failed"),
            httpx.RemoteProtocolError("WM server disconnected"),
            httpx.HTTPStatusError(
                "WM 503",
                request=httpx.Request("POST", "http://wm/workers/shotlist-enhancer/message"),
                response=httpx.Response(
                    503,
                    request=httpx.Request("POST", "http://wm/workers/shotlist-enhancer/message"),
                ),
            ),
        ],
    )
    async def test_httpx_error_preserves_originals(self, exc: httpx.HTTPError) -> None:
        """Regression: a raw httpx error from WM (5xx / dropped connection) must

        be caught and degrade to the original actions, NOT escape enhance() and
        surface as an unhandled 500 on the route. Mirrors the content #80 fix.
        """
        enhancer = _make_enhancer()
        shot_list = _make_shot_list()
        original_actions_ch1 = list(shot_list["parts"][0]["actions"])
        original_actions_ch2 = list(shot_list["parts"][1]["actions"])

        wm_client = _make_wm_client(side_effect=exc)
        enhancer._wm = wm_client

        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())

        assert result["parts"][0]["actions"] == original_actions_ch1
        assert result["parts"][1]["actions"] == original_actions_ch2


class TestMalformedInputDegrades:
    """Caller-supplied ``shot_list``/``storyline`` are unvalidated raw dicts.

    A non-dict part, an unhashable chapter key, or a non-dict storyline chapter
    must DEGRADE (skip the bad element, keep valid ones) rather than escape
    enhance() as an uncaught AttributeError/TypeError -> 500 on the route.
    """

    @pytest.mark.asyncio
    async def test_non_dict_part_does_not_crash(self) -> None:
        """A string in ``parts`` previously hit ``str.get`` -> AttributeError."""
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        shot_list = {"parts": ["x", *_make_shot_list()["parts"]]}
        # Must not raise; valid parts survive in the returned dict.
        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())
        assert isinstance(result, dict)
        assert any(isinstance(p, dict) and p.get("id") == "part-1" for p in result["parts"])

    @pytest.mark.asyncio
    async def test_unhashable_chapter_key_does_not_crash(self) -> None:
        """A list/dict ``chapter`` previously hit ``setdefault`` -> TypeError."""
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        parts = _make_shot_list()["parts"]
        parts.append(
            {"id": "bad", "part_type": "chapter", "chapter": ["unhashable"], "actions": ["a"]}
        )
        result = await enhancer.enhance({"parts": parts}, _make_storyline(), _make_analysis())
        assert isinstance(result, dict)
        assert any(isinstance(p, dict) and p.get("id") == "part-1" for p in result["parts"])

    @pytest.mark.asyncio
    async def test_non_dict_storyline_chapter_does_not_crash(self) -> None:
        """A non-dict element in storyline['chapters'] previously hit ``ch.get``."""
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        storyline = _make_storyline()
        storyline["chapters"] = ["not-a-dict", *storyline["chapters"]]
        result = await enhancer.enhance(_make_shot_list(), storyline, _make_analysis())
        assert isinstance(result, dict)
        assert any(isinstance(p, dict) and p.get("id") == "part-1" for p in result["parts"])

    @pytest.mark.asyncio
    async def test_non_iterable_actions_degrades(self) -> None:
        """A part whose ``actions`` is a non-iterable (int) previously hit
        ``for action in 5`` -> TypeError INSIDE ``_enhance_chapter``, which
        escapes the chapter-level ``except`` (no TypeError there) -> 500.
        """
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        shot_list = {
            "parts": [
                {
                    "id": "part-1",
                    "part_type": "chapter",
                    "chapter": 1,
                    "title": "Getting Started",
                    "pages": ["https://example.com/"],
                    "actions": 5,  # non-iterable
                },
            ],
        }
        # Must not raise; the malformed part degrades and survives in the result.
        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())
        assert isinstance(result, dict)
        assert result["parts"][0]["id"] == "part-1"

    @pytest.mark.asyncio
    async def test_non_iterable_pages_degrades(self) -> None:
        """A part whose ``pages`` is a non-iterable (int) previously hit
        ``for page_url in 7`` -> TypeError, escaping the chapter-level except.
        """
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        shot_list = {
            "parts": [
                {
                    "id": "part-1",
                    "part_type": "chapter",
                    "chapter": 1,
                    "title": "Getting Started",
                    "pages": 7,  # non-iterable
                    "actions": ["Scroll"],
                },
            ],
        }
        result = await enhancer.enhance(shot_list, _make_storyline(), _make_analysis())
        assert isinstance(result, dict)
        assert result["parts"][0]["id"] == "part-1"

    @pytest.mark.asyncio
    async def test_non_list_key_moments_degrades(self) -> None:
        """A storyline chapter whose ``key_moments`` is a non-list (int)
        previously hit ``", ".join(42)`` -> TypeError, escaping the
        chapter-level except -> 500. Non-str elements must also be coerced.
        """
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        storyline = _make_storyline()
        storyline["chapters"][0]["key_moments"] = 42  # non-list
        result = await enhancer.enhance(_make_shot_list(), storyline, _make_analysis())
        assert isinstance(result, dict)
        assert any(isinstance(p, dict) and p.get("id") == "part-1" for p in result["parts"])

    @pytest.mark.asyncio
    async def test_key_moments_with_non_str_elements_degrades(self) -> None:
        """A ``key_moments`` list carrying non-str elements previously hit
        ``", ".join([1, {}])`` -> TypeError. Elements are coerced to str.
        """
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        storyline = _make_storyline()
        storyline["chapters"][0]["key_moments"] = ["ok", 1, {"nested": True}]
        result = await enhancer.enhance(_make_shot_list(), storyline, _make_analysis())
        assert isinstance(result, dict)
        assert any(isinstance(p, dict) and p.get("id") == "part-1" for p in result["parts"])

    @pytest.mark.asyncio
    async def test_concrete_repro_all_bad_returns_partial(self) -> None:
        """POST-body concrete repro: {"shot_list":{"parts":["x"]}} + bad chapters."""
        enhancer = _make_enhancer(wm_client=_make_wm_client())
        result = await enhancer.enhance(
            {"parts": ["x"]}, {"chapters": ["not-a-dict"], "tone": "t"}, _make_analysis()
        )
        assert isinstance(result, dict)
        assert result["parts"] == ["x"]
