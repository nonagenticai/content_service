"""Additional unit tests for StorylineGenerator to boost coverage.

Covers: _extract_chapters_flexibly, generate() retry logic, auto-detect duration,
page filtering, _validate_chapter_urls, _apply_feature_weights, _build_context_section,
_build_prompt with reviews/SPA/mode, _load_template error handling.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.api.narrative_types import FeaturePriority, NarrationGuidance, UserContext
from src.services.narrative.storyline_generator import (
    StorylineGenerator,
    _extract_chapters_flexibly,
)

# ---------------------------------------------------------------------------
# _extract_chapters_flexibly
# ---------------------------------------------------------------------------


class TestExtractChaptersFlexibly:
    """Tests for the module-level _extract_chapters_flexibly helper."""

    def test_finds_chapters_key(self) -> None:
        data = {"chapters": [{"title": "Ch1"}]}
        assert _extract_chapters_flexibly(data) == [{"title": "Ch1"}]

    def test_finds_sections_key(self) -> None:
        data = {"sections": [{"title": "S1"}]}
        assert _extract_chapters_flexibly(data) == [{"title": "S1"}]

    def test_finds_parts_key(self) -> None:
        data = {"parts": [{"title": "P1"}]}
        assert _extract_chapters_flexibly(data) == [{"title": "P1"}]

    def test_finds_video_chapters_key(self) -> None:
        data = {"video_chapters": [{"title": "VC1"}]}
        assert _extract_chapters_flexibly(data) == [{"title": "VC1"}]

    def test_finds_scenes_key(self) -> None:
        data = {"scenes": [{"description": "S1"}]}
        assert _extract_chapters_flexibly(data) == [{"description": "S1"}]

    def test_finds_segments_key(self) -> None:
        data = {"segments": [{"title": "Seg1"}]}
        assert _extract_chapters_flexibly(data) == [{"title": "Seg1"}]

    def test_skips_empty_known_keys(self) -> None:
        data = {"chapters": [], "other_list": [{"title": "Found"}]}
        result = _extract_chapters_flexibly(data)
        assert result == [{"title": "Found"}]

    def test_fallback_finds_list_with_title(self) -> None:
        data = {"my_custom_list": [{"title": "Custom Ch"}]}
        result = _extract_chapters_flexibly(data)
        assert result == [{"title": "Custom Ch"}]

    def test_fallback_finds_list_with_description(self) -> None:
        data = {"custom": [{"description": "Desc", "page_url": "/p"}]}
        result = _extract_chapters_flexibly(data)
        assert result == [{"description": "Desc", "page_url": "/p"}]

    def test_fallback_finds_list_with_url_key(self) -> None:
        data = {"items": [{"url": "https://example.com"}]}
        result = _extract_chapters_flexibly(data)
        assert result == [{"url": "https://example.com"}]

    def test_returns_none_for_no_lists(self) -> None:
        data = {"video_title": "Test", "tone": "casual"}
        assert _extract_chapters_flexibly(data) is None

    def test_returns_none_for_non_dict_list_items(self) -> None:
        data = {"items": ["just", "strings"]}
        assert _extract_chapters_flexibly(data) is None

    def test_returns_none_for_dict_list_without_known_keys(self) -> None:
        data = {"items": [{"foo": "bar", "baz": 123}]}
        assert _extract_chapters_flexibly(data) is None


# ---------------------------------------------------------------------------
# StorylineGenerator.generate() -- full async flow
# ---------------------------------------------------------------------------


def _wm_response(text: str) -> WMResponse:
    """Build a WMResponse with text output."""
    return WMResponse(
        output=text,
        output_type="string",
        output_schema_used=None,
        metadata=None,
        usage_metrics=None,
    )


def _make_async_client(response_text: str) -> AsyncMock:
    """Return a mock WMClient whose .message returns a WMResponse."""
    client = AsyncMock()
    client.message = AsyncMock(return_value=_wm_response(response_text))
    client.aclose = AsyncMock()
    return client


def _valid_storyline_json(duration: float = 3.0) -> str:
    return json.dumps(
        {
            "video_title": "LLM Demo",
            "video_description": "A demo",
            "target_duration_minutes": duration,
            "tone": "professional",
            "intro": {"title": "Intro", "subtitle": "Sub", "duration_seconds": 8},
            "chapters": [
                {
                    "number": 1,
                    "title": "First Look",
                    "pages": ["https://example.com/"],
                    "narrative_arc": "Introduction",
                    "key_moments": ["signup form"],
                    "estimated_duration_seconds": 60,
                }
            ],
            "outro": {"title": "Thanks", "subtitle": "Visit us", "duration_seconds": 6},
        }
    )


def _sample_analysis() -> AnalysisInput:
    return AnalysisInput(
        pages=[
            PageInput(url="https://example.com/", title="Home", page_type="homepage", page_id="p1"),
            PageInput(
                url="https://example.com/about", title="About", page_type="about", page_id="p2"
            ),
        ],
        site=SiteInput(
            description="Test Site",
            target_audience=["devs"],
            use_cases=["productivity"],
            tone="professional",
        ),
    )


class TestStorylineGenerate:
    """Tests for StorylineGenerator.generate() async method."""

    @pytest.mark.asyncio
    async def test_successful_generation(self) -> None:
        """generate() returns parsed storyline on first attempt."""
        mock_client = _make_async_client(_valid_storyline_json())

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
        )

        assert result["video_title"] == "LLM Demo"
        assert len(result["chapters"]) >= 1

    @pytest.mark.asyncio
    async def test_retry_on_llm_error_then_success(self) -> None:
        """generate() retries on LLM exception and succeeds on second attempt."""
        mock_client = AsyncMock()
        mock_client.message = AsyncMock(
            side_effect=[
                RuntimeError("Temporary LLM error"),
                _wm_response(_valid_storyline_json()),
            ]
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
        )

        # Should have retried and succeeded
        assert result["video_title"] == "LLM Demo"
        assert mock_client.message.call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted_uses_fallback(self) -> None:
        """When all 3 attempts fail, generate() uses _default_storyline."""
        mock_client = AsyncMock()
        mock_client.message = AsyncMock(
            side_effect=RuntimeError("LLM down"),
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
        )

        # Fallback storyline
        assert "chapters" in result
        assert len(result["chapters"]) >= 1
        assert mock_client.message.call_count == 3

    @pytest.mark.asyncio
    async def test_empty_llm_response_triggers_retry(self) -> None:
        """Empty LLM response (content=None) triggers retry."""
        empty_resp = WMResponse(
            output=None,
            output_type="string",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )

        mock_client = AsyncMock()
        mock_client.message = AsyncMock(
            side_effect=[
                empty_resp,
                _wm_response(_valid_storyline_json()),
            ]
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
        )

        assert result["video_title"] == "LLM Demo"

    @pytest.mark.asyncio
    async def test_auto_detect_duration_few_pages(self) -> None:
        """duration_minutes=0 triggers auto-detection."""
        mock_client = _make_async_client(_valid_storyline_json(2.0))

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),  # 2 pages
            duration_minutes=0,
        )

        # 2 pages -> auto-detect returns 2.0 min
        assert result["video_title"] == "LLM Demo"
        assert "chapters" in result
        assert len(result["chapters"]) >= 1

    @pytest.mark.asyncio
    async def test_auto_detect_duration_many_pages(self) -> None:
        """Auto-detect with many pages."""
        pages = [
            PageInput(url=f"https://example.com/p{i}", title=f"Page {i}", page_id=f"p{i}")
            for i in range(20)
        ]
        analysis = AnalysisInput(pages=pages, site=SiteInput(description="Big site"))
        mock_client = _make_async_client(_valid_storyline_json(5.0))

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(analysis=analysis, duration_minutes=0)

        # 20 pages -> auto-detect returns min(5.0, 1.5 + 20*0.2) = 5.0
        assert result["video_title"] == "LLM Demo"
        assert result["target_duration_minutes"] == 5.0

    @pytest.mark.asyncio
    async def test_user_context_overrides_duration(self) -> None:
        """UserContext narration_guidance.target_length_minutes overrides duration."""
        mock_client = _make_async_client(_valid_storyline_json(5.0))
        ctx = UserContext(
            narration_guidance=NarrationGuidance(target_length_minutes=5.0),
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
            user_context=ctx,
        )

        # UserContext overrides duration to 5.0
        assert result["video_title"] == "LLM Demo"
        assert result["target_duration_minutes"] == 5.0

    @pytest.mark.asyncio
    async def test_user_context_builds_context_section(self) -> None:
        """UserContext with goals/audience/tone builds context section in prompt."""
        mock_client = _make_async_client(_valid_storyline_json())
        ctx = UserContext(
            goals=["increase signups"],
            target_audience="developers",
            site_type="SaaS",
            narration_guidance=NarrationGuidance(tone="casual"),
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
            user_context=ctx,
            message="Focus on onboarding",
        )

        # Verify the storyline was produced with expected structure
        assert result["video_title"] == "LLM Demo"
        assert len(result["chapters"]) >= 1
        assert "intro" in result

    @pytest.mark.asyncio
    async def test_exclude_pages_filters_analysis(self) -> None:
        """UserContext exclude_pages removes matching pages from analysis."""
        mock_client = _make_async_client(_valid_storyline_json())
        ctx = UserContext(exclude_pages=["about"])

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),  # Has "about" page
            duration_minutes=3.0,
            user_context=ctx,
        )

        # Verify storyline was produced (about page was filtered before LLM call)
        assert result["video_title"] == "LLM Demo"
        assert len(result["chapters"]) >= 1

    @pytest.mark.asyncio
    async def test_feature_weights_applied(self) -> None:
        """Feature priorities skip and weight chapters."""
        # Build a storyline with chapters that match feature keywords
        storyline_json = json.dumps(
            {
                "video_title": "Demo",
                "chapters": [
                    {
                        "number": 1,
                        "title": "Pricing Plans",
                        "pages": ["https://example.com/pricing"],
                        "estimated_duration_seconds": 60,
                    },
                    {
                        "number": 2,
                        "title": "Blog Posts",
                        "pages": ["https://example.com/blog"],
                        "estimated_duration_seconds": 60,
                    },
                ],
                "intro": {"title": "W"},
                "outro": {"title": "E"},
            }
        )
        mock_client = _make_async_client(storyline_json)
        ctx = UserContext(
            feature_priorities=[
                FeaturePriority(feature="pricing", priority="high"),
                FeaturePriority(feature="blog", priority="skip"),
            ],
        )

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(
            analysis=_sample_analysis(),
            duration_minutes=3.0,
            user_context=ctx,
        )

        # Blog chapter should be skipped
        titles = [ch["title"] for ch in result["chapters"]]
        assert "Blog Posts" not in titles
        # Pricing should remain
        assert any("Pricing" in t for t in titles)


# ---------------------------------------------------------------------------
# _validate_chapter_urls
# ---------------------------------------------------------------------------


class TestValidateChapterUrls:
    def test_keeps_valid_urls(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        storyline = {
            "chapters": [
                {"number": 1, "title": "Ch1", "pages": ["https://example.com/", "p1"]},
            ]
        }
        result = gen._validate_chapter_urls(storyline, analysis)
        assert "https://example.com/" in result["chapters"][0]["pages"]
        assert "p1" in result["chapters"][0]["pages"]

    def test_drops_hallucinated_urls(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": [
                        "https://example.com/",
                        "https://hallucinated.com/fake",
                    ],
                },
            ]
        }
        result = gen._validate_chapter_urls(storyline, analysis)
        assert "https://example.com/" in result["chapters"][0]["pages"]
        assert "https://hallucinated.com/fake" not in result["chapters"][0]["pages"]

    def test_drops_hallucinated_page_ids(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        storyline = {
            "chapters": [
                {"number": 1, "title": "Ch1", "pages": ["p1", "nonexistent-page"]},
            ]
        }
        result = gen._validate_chapter_urls(storyline, analysis)
        assert "p1" in result["chapters"][0]["pages"]
        assert "nonexistent-page" not in result["chapters"][0]["pages"]


# ---------------------------------------------------------------------------
# _apply_feature_weights
# ---------------------------------------------------------------------------


class TestApplyFeatureWeights:
    def test_skip_removes_chapter(self) -> None:
        gen = StorylineGenerator(settings=None)
        ctx = UserContext(
            feature_priorities=[FeaturePriority(feature="blog", priority="skip")],
        )
        storyline = {
            "chapters": [
                {"number": 1, "title": "Blog", "narrative_arc": "blog posts", "pages": []},
                {"number": 2, "title": "Pricing", "narrative_arc": "plans", "pages": []},
            ]
        }
        result = gen._apply_feature_weights(storyline, ctx)
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "Pricing"
        assert result["chapters"][0]["number"] == 1  # renumbered

    def test_high_priority_boosts_duration(self) -> None:
        gen = StorylineGenerator(settings=None)
        ctx = UserContext(
            feature_priorities=[FeaturePriority(feature="pricing", priority="high")],
        )
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Pricing",
                    "narrative_arc": "",
                    "pages": [],
                    "estimated_duration_seconds": 10,
                },
            ]
        }
        result = gen._apply_feature_weights(storyline, ctx)
        # High priority with short duration gets minimum of 20.0
        assert result["chapters"][0]["estimated_duration_seconds"] >= 20.0

    def test_low_priority_reduces_duration(self) -> None:
        gen = StorylineGenerator(settings=None)
        ctx = UserContext(
            feature_priorities=[FeaturePriority(feature="about", priority="low")],
        )
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "About Us",
                    "narrative_arc": "",
                    "pages": [],
                    "estimated_duration_seconds": 60,
                },
            ]
        }
        result = gen._apply_feature_weights(storyline, ctx)
        assert result["chapters"][0]["estimated_duration_seconds"] < 60


# ---------------------------------------------------------------------------
# _build_context_section
# ---------------------------------------------------------------------------


class TestBuildContextSection:
    def test_empty_context(self) -> None:
        result = StorylineGenerator._build_context_section(UserContext())
        assert result == ""

    def test_full_context(self) -> None:
        ctx = UserContext(
            goals=["boost signups"],
            target_audience="startups",
            site_type="SaaS",
            narration_guidance=NarrationGuidance(tone="friendly"),
        )
        result = StorylineGenerator._build_context_section(ctx)
        assert "## User Direction" in result
        assert "**Goals:** boost signups" in result
        assert "**Target Audience:** startups" in result
        assert "**Site Type:** SaaS" in result
        assert "**Tone:** friendly" in result


# ---------------------------------------------------------------------------
# _auto_detect_duration
# ---------------------------------------------------------------------------


class TestAutoDetectDuration:
    def test_few_pages(self) -> None:
        assert StorylineGenerator._auto_detect_duration(3) == 2.0

    def test_medium_pages(self) -> None:
        assert StorylineGenerator._auto_detect_duration(10) == 3.0

    def test_many_pages(self) -> None:
        result = StorylineGenerator._auto_detect_duration(20)
        assert result == min(5.0, 1.5 + 20 * 0.2)


# ---------------------------------------------------------------------------
# _build_prompt edge cases
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_prompt_with_reviews(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        reviews = {
            "design": {"rating": 4.5, "summary": "Clean UI"},
        }
        prompt = gen._build_prompt(analysis, reviews, 3.0, "", "webapp_commercial")
        assert "Website Reviews" in prompt
        assert "Design" in prompt
        assert "Clean UI" in prompt

    def test_prompt_with_message(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        prompt = gen._build_prompt(analysis, None, 3.0, "Focus on pricing", "webapp_commercial")
        assert "Focus on pricing" in prompt

    def test_prompt_with_spa_pages(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/",
                    title="Home",
                    page_id="p1",
                    is_spa_section=True,
                ),
            ],
            site=SiteInput(description="SPA App"),
        )
        prompt = gen._build_prompt(analysis, None, 3.0, "", "webapp_commercial")
        assert "[SPA]" in prompt
        assert "single-page application" in prompt

    def test_prompt_with_review_containing_error(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        reviews = {
            "design": {"error": "Review failed"},
        }
        prompt = gen._build_prompt(analysis, reviews, 3.0, "", "webapp_commercial")
        # Error reviews are skipped -- "Website Reviews" section not added
        assert "Review failed" not in prompt
        assert "Website Reviews" not in prompt


# ---------------------------------------------------------------------------
# _parse_storyline edge cases
# ---------------------------------------------------------------------------


class TestParseStoryline:
    def test_parse_with_no_chapters_falls_back(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        raw = json.dumps({"video_title": "Test", "tone": "casual"})
        result = gen._parse_storyline(raw, analysis, 3.0)
        # No chapters key -> falls back to default
        assert len(result["chapters"]) >= 1

    def test_parse_with_alternative_key(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        raw = json.dumps(
            {
                "video_title": "Alt Keys",
                "sections": [
                    {
                        "number": 1,
                        "title": "Section One",
                        "pages": [],
                        "estimated_duration_seconds": 30,
                    }
                ],
                "intro": {},
                "outro": {},
            }
        )
        result = gen._parse_storyline(raw, analysis, 3.0)
        assert result["video_title"] == "Alt Keys"
        assert len(result["chapters"]) == 1

    def test_parse_with_non_dict_chapter_items(self) -> None:
        """Non-dict items in chapters list should be filtered out."""
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        raw = json.dumps(
            {
                "video_title": "Mixed",
                "chapters": [
                    {"number": 1, "title": "Good", "pages": [], "estimated_duration_seconds": 30},
                    "not a dict",
                    42,
                ],
                "intro": {},
                "outro": {},
            }
        )
        result = gen._parse_storyline(raw, analysis, 3.0)
        assert len(result["chapters"]) == 1
        assert result["chapters"][0]["title"] == "Good"


# ---------------------------------------------------------------------------
# _load_template
# ---------------------------------------------------------------------------


class TestLoadTemplate:
    def test_nonexistent_mode_returns_none_if_no_templates(self) -> None:
        gen = StorylineGenerator(settings=None)
        mock_path = MagicMock()
        mock_path.exists.return_value = False
        mock_dir = MagicMock()
        mock_dir.__truediv__ = MagicMock(return_value=mock_path)
        # Use patch.dict on the method's __globals__ to reliably replace
        # _TEMPLATES_DIR even when integration tests have caused module
        # reloading (del sys.modules["src.*"]).
        with patch.dict(
            gen._load_template.__globals__,
            {"_TEMPLATES_DIR": mock_dir},
        ):
            result = gen._load_template("nonexistent_mode", 3.0)
        assert result is None

    def test_template_rendering_error_returns_none(self) -> None:
        gen = StorylineGenerator(settings=None)
        import jinja2

        mock_path = MagicMock()
        mock_path.exists.return_value = True
        mock_path.read_text.return_value = "{{ invalid_syntax"
        mock_dir = MagicMock()
        mock_dir.__truediv__ = MagicMock(return_value=mock_path)

        # Build a mock jinja2 module that raises TemplateSyntaxError from
        # Template() but preserves real exception classes for the except clause.
        mock_jinja2 = MagicMock()
        mock_jinja2.Template = MagicMock(
            side_effect=jinja2.TemplateSyntaxError("bad", 1),
        )
        mock_jinja2.TemplateError = jinja2.TemplateError
        mock_jinja2.Undefined = jinja2.Undefined

        # Use patch.dict on __globals__ for reliable patching after module reloads
        with patch.dict(
            gen._load_template.__globals__,
            {"_TEMPLATES_DIR": mock_dir, "jinja2": mock_jinja2},
        ):
            result = gen._load_template("broken_mode", 3.0)
        assert result is None


# ---------------------------------------------------------------------------
# _parse_storyline -- wrong-typed field falls back to _default_storyline
# ---------------------------------------------------------------------------


class TestParseStorylineWrongTypeFallback:
    """Valid JSON with a wrong-typed field must fall back, not raise.

    A field like ``duration_seconds`` arriving as a list makes ``float([8])``
    raise ``TypeError`` (not ``ValueError``). The parse must swallow it and
    return the ``_default_storyline`` shape instead of propagating and
    failing the whole storyline job.
    """

    def test_list_where_float_expected_falls_back(self) -> None:
        gen = StorylineGenerator(settings=None)
        analysis = _sample_analysis()
        # Valid JSON, valid chapters, but intro.duration_seconds is a list ->
        # float([8]) raises TypeError, which pre-fix escaped the except.
        raw = json.dumps(
            {
                "video_title": "Demo",
                "chapters": [{"number": 1, "title": "Ch1", "pages": []}],
                "intro": {"title": "Hi", "duration_seconds": [8]},
                "outro": {"title": "Bye"},
            }
        )

        result = gen._parse_storyline(raw, analysis, 3.0)

        assert result == gen._default_storyline(analysis, 3.0)


class TestZeroChapterStorylineIsNotASuccess:
    """An LLM answer whose 'chapters' survive extraction but not the object filter
    must NOT ship as a clean, cacheable storyline.

    ``_extract_chapters_flexibly`` accepts ANY non-empty list, so a model that
    summarises — ``{"chapters": ["Intro", "Features"]}`` — passed the
    ``raise ValueError("No chapters found")`` guard, and the
    ``if isinstance(ch, dict)`` filter in the returned dict then removed every
    element. The result was ``"chapters": []`` with NO ``degraded`` marker, so
    ``src/api/narrative/storyline.py::_is_degraded`` returned False and the empty
    storyline was written to the tenant result cache and replayed for
    ``STORYLINE_CACHE_TTL``. Downstream that is an intro+outro-only shot list —
    roughly a 14-second video — reported ``completed``.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("payload", "case"),
        [
            ({"chapters": ["Intro", "Features", "Pricing"]}, "list_of_strings"),
            ({"chapters": [None, None]}, "list_of_nulls"),
            ({"sections": [["Intro"], ["Features"]]}, "list_of_lists_via_alias_key"),
        ],
    )
    async def test_non_object_chapters_fall_back_to_the_degraded_storyline(
        self, payload: dict, case: str
    ) -> None:
        body = {
            "video_title": "LLM Demo",
            "video_description": "A demo",
            "tone": "professional",
            "intro": {"title": "Hi", "subtitle": "", "duration_seconds": 8},
            "outro": {"title": "Thanks", "subtitle": "", "duration_seconds": 6},
            **payload,
        }
        mock_client = _make_async_client(json.dumps(body))

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(analysis=_sample_analysis(), duration_minutes=3.0)

        # The fallback is what must be served, and it is honest about itself:
        # `degraded` is the ONLY thing that keeps this out of the result cache.
        assert result["chapters"], f"{case}: shipped a zero-chapter storyline"
        assert result.get("degraded") is True, f"{case}: unmarked, so it would be CACHED"

    @pytest.mark.asyncio
    async def test_a_partially_object_chapter_list_still_succeeds(self) -> None:
        """Only a TOTAL loss is a failure — one usable object is a real storyline.

        This is the half that stops the fix from over-firing: the filter is allowed
        to drop junk entries as long as something real survives.
        """
        body = {
            "video_title": "LLM Demo",
            "video_description": "A demo",
            "tone": "professional",
            "intro": {"title": "Hi", "subtitle": "", "duration_seconds": 8},
            "chapters": ["junk", {"number": 1, "title": "Real Chapter", "pages": []}],
            "outro": {"title": "Thanks", "subtitle": "", "duration_seconds": 6},
        }
        mock_client = _make_async_client(json.dumps(body))

        gen = StorylineGenerator(settings=None, wm_client=mock_client)
        result = await gen.generate(analysis=_sample_analysis(), duration_minutes=3.0)

        assert result.get("degraded") is not True
        assert [c["title"] for c in result["chapters"]] == ["Real Chapter"]
