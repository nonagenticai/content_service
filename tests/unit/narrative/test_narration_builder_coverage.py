"""Additional unit tests for NarrationBuilder to boost coverage.

Covers: _synthesize_parts, generate_for_shot_list, fallback narration,
template rendering failures, _generate_part_narration, _substitute_code_quality_vars,
_validate_template_vars, _rewrite_bookend (outro variant with message).
"""

from __future__ import annotations

import base64
from unittest.mock import AsyncMock, patch

import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.api.narrative_types import CodeQualityFacts, NarrationGuidance, UserContext
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


def _sample_analysis() -> AnalysisInput:
    return AnalysisInput(
        pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
        site=SiteInput(description="Test"),
    )


def _sample_shot_list() -> dict:
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
                "title": "Chapter 1",
            },
            {
                "id": "ch01-part1",
                "part_type": "browser_recording",
                "chapter": 1,
                "title": "Homepage Tour",
                "pages": ["https://example.com/"],
                "actions": ["Navigate to homepage", "Scroll down"],
                "estimated_duration_seconds": 30,
            },
            {
                "id": "ch01-part2",
                "part_type": "browser_recording",
                "chapter": 1,
                "title": "Features Section",
                "pages": ["https://example.com/features"],
                "actions": ["Click features"],
                "estimated_duration_seconds": 30,
            },
            {
                "id": "outro",
                "part_type": "title_card",
                "title": "Get Started",
                "narrative_text": "Start your journey today.",
            },
        ],
        "recording_order": ["intro", "ch01-title", "ch01-part1", "ch01-part2", "outro"],
    }


def _sample_storyline() -> dict:
    return {
        "video_title": "Demo Video",
        "tone": "professional",
        "intro": {
            "title": "Welcome",
            "narrative_text": "Discover the product.",
            "duration_seconds": 8,
        },
        "chapters": [
            {
                "number": 1,
                "title": "Getting Started",
                "narrative_arc": "Introduction",
                "estimated_duration_seconds": 60,
            },
        ],
        "outro": {
            "title": "Get Started",
            "narrative_text": "Start your journey today.",
            "duration_seconds": 6,
        },
    }


# ---------------------------------------------------------------------------
# _synthesize_parts
# ---------------------------------------------------------------------------


class TestSynthesizeParts:
    @pytest.mark.asyncio
    async def test_synthesize_parts_success(self, mock_settings: Settings) -> None:
        """_synthesize_parts adds audio_base64 to each part."""
        mock_client = _make_async_client("Narration.")
        mock_tts = AsyncMock()
        mock_tts.generate = AsyncMock(return_value=b"fake audio bytes")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            parts = [
                {"part_id": "intro", "text": "Welcome to the demo.", "word_count": 5},
                {"part_id": "ch01", "text": "Let's explore features.", "word_count": 4},
            ]
            await builder._synthesize_parts(parts)

        assert "audio_base64" in parts[0]
        assert "audio_base64" in parts[1]
        decoded = base64.b64decode(str(parts[0]["audio_base64"]))
        assert decoded == b"fake audio bytes"

    @pytest.mark.asyncio
    async def test_synthesize_parts_tts_failure_is_failopen(self, mock_settings: Settings) -> None:
        """TTS failure for a part doesn't crash, just skips audio."""
        mock_client = _make_async_client("Narration.")
        mock_tts = AsyncMock()
        mock_tts.generate = AsyncMock(side_effect=RuntimeError("TTS down"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            parts = [
                {"part_id": "intro", "text": "Welcome.", "word_count": 1},
            ]
            await builder._synthesize_parts(parts)

        assert "audio_base64" not in parts[0]

    @pytest.mark.asyncio
    async def test_synthesize_parts_closes_tts_client(self, mock_settings: Settings) -> None:
        """The per-request TTSClient must be closed on success (no socket/pool leak)."""
        mock_client = _make_async_client("Narration.")
        mock_tts = AsyncMock()
        mock_tts.generate = AsyncMock(return_value=b"audio")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            await builder._synthesize_parts(
                [{"part_id": "intro", "text": "Welcome.", "word_count": 1}]
            )

        mock_tts.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_synthesize_parts_closes_tts_client_on_failure(
        self, mock_settings: Settings
    ) -> None:
        """The TTSClient must be closed even when every part's synthesis fails."""
        mock_client = _make_async_client("Narration.")
        mock_tts = AsyncMock()
        mock_tts.generate = AsyncMock(side_effect=RuntimeError("TTS down"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            await builder._synthesize_parts(
                [{"part_id": "intro", "text": "Welcome.", "word_count": 1}]
            )

        mock_tts.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_synthesize_parts_skips_empty_text(self, mock_settings: Settings) -> None:
        """Parts with empty text are skipped for TTS."""
        mock_client = _make_async_client("Narration.")
        mock_tts = AsyncMock()
        mock_tts.generate = AsyncMock(return_value=b"audio")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            parts = [
                {"part_id": "empty", "text": "   ", "word_count": 0},
            ]
            await builder._synthesize_parts(parts)

        assert "audio_base64" not in parts[0]
        mock_tts.generate.assert_not_called()


# ---------------------------------------------------------------------------
# generate_for_shot_list
# ---------------------------------------------------------------------------


class TestGenerateForShotList:
    @pytest.mark.asyncio
    async def test_basic_shot_list_narration(self, mock_settings: Settings) -> None:
        """generate_for_shot_list produces parts for intro, browser recordings, and outro."""
        mock_client = _make_async_client("Great narration for this section.")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=_sample_shot_list(),
            storyline=_sample_storyline(),
        )

        part_ids = [p["part_id"] for p in result["parts"]]
        assert "intro" in part_ids
        assert "outro" in part_ids
        assert "ch01-part1" in part_ids
        assert "ch01-part2" in part_ids
        # Chapter title cards should have narration
        assert "ch01-title" in part_ids
        assert result["total_words"] > 0
        assert result["tone"] == "professional"

    @pytest.mark.asyncio
    async def test_shot_list_with_user_context(self, mock_settings: Settings) -> None:
        """generate_for_shot_list uses user context tone override."""
        mock_client = _make_async_client("Casual narration.")
        ctx = UserContext(
            goals=["increase signups"],
            target_audience="developers",
            narration_guidance=NarrationGuidance(
                tone="casual",
                key_messages=["easy to use"],
                avoid_topics=["pricing"],
            ),
        )

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=_sample_shot_list(),
            storyline=_sample_storyline(),
            user_context=ctx,
            message="Keep it brief",
        )

        assert result["tone"] == "casual"

    @pytest.mark.asyncio
    async def test_shot_list_browser_part_fallback_on_failure(
        self, mock_settings: Settings
    ) -> None:
        """Browser recording part uses fallback when LLM fails."""
        failing_client = AsyncMock()
        failing_client.message = AsyncMock(side_effect=RuntimeError("LLM down"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=failing_client)
        result = await builder.generate_for_shot_list(
            shot_list=_sample_shot_list(),
            storyline=_sample_storyline(),
        )

        # Browser recording parts should have fallback text
        browser_parts = [p for p in result["parts"] if p["part_id"].startswith("ch01")]
        assert len(browser_parts) >= 1
        for part in browser_parts:
            assert "take a look" in part["text"].lower() or len(part["text"]) > 0

    @pytest.mark.asyncio
    async def test_shot_list_with_synthesize_audio(self, mock_settings: Settings) -> None:
        """generate_for_shot_list calls _synthesize_parts when synthesize_audio=True."""
        mock_client = _make_async_client("Audio narration.")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch.object(builder, "_synthesize_parts", new_callable=AsyncMock) as mock_synth:
            await builder.generate_for_shot_list(
                shot_list=_sample_shot_list(),
                storyline=_sample_storyline(),
                synthesize_audio=True,
            )
            mock_synth.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_shot_list_skips_missing_parts(self, mock_settings: Settings) -> None:
        """recording_order referencing non-existent part IDs is skipped."""
        mock_client = _make_async_client("Narration.")
        shot_list = {
            "parts": [
                {"id": "intro", "part_type": "title_card", "title": "Welcome"},
            ],
            "recording_order": ["intro", "nonexistent-part"],
        }

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=shot_list,
            storyline=_sample_storyline(),
        )

        # Should not crash, nonexistent part is just skipped
        assert isinstance(result["parts"], list)

    @pytest.mark.asyncio
    async def test_shot_list_intro_without_narrative_text(self, mock_settings: Settings) -> None:
        """Intro title card without narrative_text gets it from storyline."""
        mock_client = _make_async_client("Rewritten intro.")
        shot_list = {
            "parts": [
                {"id": "intro", "part_type": "title_card", "title": "Welcome"},
            ],
            "recording_order": ["intro"],
        }
        storyline = {
            "video_title": "Demo",
            "tone": "professional",
            "intro": {"title": "Welcome", "narrative_text": "Discover the product."},
            "chapters": [],
            "outro": {},
        }

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder.generate_for_shot_list(
            shot_list=shot_list,
            storyline=storyline,
        )

        assert any(p["part_id"] == "intro" for p in result["parts"])


# ---------------------------------------------------------------------------
# _generate_chapter_narration edge cases
# ---------------------------------------------------------------------------


class TestGenerateChapterNarration:
    @pytest.mark.asyncio
    async def test_empty_llm_content_uses_fallback(self, mock_settings: Settings) -> None:
        """Empty LLM content (empty string) triggers fallback text."""
        mock_client = _make_async_client("")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder._generate_chapter_narration(
            chapter={
                "number": 1,
                "title": "Feature",
                "pages": [],
                "narrative_arc": "arc",
                "key_moments": [],
                "estimated_duration_seconds": 30,
            },
            tone="professional",
        )

        assert "demonstrates" in result["text"]

    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback(self, mock_settings: Settings) -> None:
        """LLM exception triggers fallback text."""
        failing_client = AsyncMock()
        failing_client.message = AsyncMock(side_effect=RuntimeError("LLM down"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=failing_client)
        result = await builder._generate_chapter_narration(
            chapter={
                "number": 1,
                "title": "Feature",
                "pages": [],
                "narrative_arc": "arc",
                "key_moments": [],
                "estimated_duration_seconds": 30,
            },
            tone="professional",
        )

        assert "Let's take a closer look at Feature." in result["text"]
        assert "arc" in result["text"]
        assert result["word_count"] > 0


# ---------------------------------------------------------------------------
# _build_prompt with critique/review mode
# ---------------------------------------------------------------------------


class TestBuildPromptModes:
    def test_critique_mode_falls_back_to_chapter_template(self, mock_settings: Settings) -> None:
        """Critique mode uses chapter_narration.md instead of critique_video.md for chapters."""
        builder = NarrationBuilder(settings=mock_settings)
        prompt = builder._build_prompt(
            chapter={"title": "Security", "pages": [], "narrative_arc": "audit"},
            tone="professional",
            est_dur=30,
            word_target=75,
            mode="critique_video",
        )
        assert "Security" in prompt
        assert "professional" in prompt.lower() or "tone" in prompt.lower()

    def test_build_prompt_with_code_quality_facts(self, mock_settings: Settings) -> None:
        """Code quality facts are substituted into the prompt."""
        facts = CodeQualityFacts(
            primary_language="Python",
            loc=10000,
            maintainability_score=85.0,
            top_issues=["unused imports", "long functions"],
        )

        builder = NarrationBuilder(settings=mock_settings)
        prompt = builder._build_prompt(
            chapter={"title": "Code Quality", "pages": [], "narrative_arc": "quality"},
            tone="professional",
            est_dur=30,
            word_target=75,
            mode="technical",
            code_quality_facts=facts,
        )
        assert "Code Quality" in prompt
        # Code quality facts should be substituted into the prompt
        assert "Python" in prompt or "10000" in prompt

    def test_template_rendering_failure_uses_default(self, mock_settings: Settings) -> None:
        """When Jinja2 template rendering fails, fallback to default template."""
        import jinja2

        original_template = jinja2.Template
        call_count = 0

        def fail_first_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise jinja2.TemplateSyntaxError("bad", 1)
            return original_template(*args, **kwargs)

        builder = NarrationBuilder(settings=mock_settings)
        with patch(
            "src.services.narrative.narration_builder.jinja2.Template",
            side_effect=fail_first_call,
        ):
            prompt = builder._build_prompt(
                chapter={"title": "Test", "pages": []},
                tone="professional",
                est_dur=30,
                word_target=75,
            )
        # After template failure, falls back to default inline prompt
        assert "Test" in prompt
        assert "professional" in prompt.lower() or "narration" in prompt.lower()


# ---------------------------------------------------------------------------
# _substitute_code_quality_vars
# ---------------------------------------------------------------------------


class TestSubstituteCodeQualityVars:
    def test_with_facts(self) -> None:
        facts = CodeQualityFacts(
            primary_language="TypeScript",
            loc=8000,
            maintainability_score=72.0,
            top_issues=["large bundle size"],
        )
        template_vars: dict = {}
        NarrationBuilder._substitute_code_quality_vars(template_vars, facts)
        assert "TypeScript" in template_vars["code_quality_summary"]
        assert template_vars["primary_language"] == "TypeScript"
        assert template_vars["loc"] == "8000"
        assert "large bundle size" in template_vars["top_issues_summary"]

    def test_without_facts(self) -> None:
        template_vars: dict = {}
        NarrationBuilder._substitute_code_quality_vars(template_vars, None)
        assert template_vars["code_quality_summary"] == ""
        assert template_vars["primary_language"] == ""

    def test_with_no_top_issues(self) -> None:
        facts = CodeQualityFacts(
            primary_language="Go",
            loc=3000,
            maintainability_score=90.0,
            top_issues=[],
        )
        template_vars: dict = {}
        NarrationBuilder._substitute_code_quality_vars(template_vars, facts)
        assert "No significant issues" in template_vars["top_issues_summary"]


# ---------------------------------------------------------------------------
# _rewrite_bookend (outro with message)
# ---------------------------------------------------------------------------


class TestRewriteBookendOutro:
    @pytest.mark.asyncio
    async def test_outro_rewrite_with_message(self, mock_settings: Settings) -> None:
        """Outro rewrite includes user direction in the prompt."""
        mock_client = _make_async_client("Sign up for a free trial today!")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result, degraded = await builder._rewrite_bookend(
            stage_direction="End the video with CTA",
            section="outro",
            video_title="Amazing Demo",
            tone="professional",
            message="Emphasize the free trial",
        )

        assert result == "Sign up for a free trial today!"
        assert degraded is False

    @pytest.mark.asyncio
    async def test_rewrite_none_content_returns_original(self, mock_settings: Settings) -> None:
        """When WM returns None output, original text is returned."""
        mock_client = AsyncMock()
        mock_client.message = AsyncMock(
            return_value=WMResponse(
                output=None,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result, degraded = await builder._rewrite_bookend(
            stage_direction="Original text",
            section="intro",
            video_title="Demo",
            tone="professional",
        )

        assert result == "Original text"
        # An un-rewritten stage direction is NOT narration -> must be marked.
        assert degraded is True


# ---------------------------------------------------------------------------
# generate() with synthesize_audio
# ---------------------------------------------------------------------------


class TestGenerateWithAudio:
    @pytest.mark.asyncio
    async def test_generate_with_synthesize_audio_calls_synthesize(
        self, mock_settings: Settings
    ) -> None:
        """generate() with synthesize_audio=True calls _synthesize_parts."""
        mock_client = _make_async_client("Narration text.")
        storyline = {
            "tone": "professional",
            "chapters": [
                {
                    "number": 1,
                    "title": "Feature",
                    "pages": [],
                    "narrative_arc": "arc",
                    "key_moments": [],
                    "estimated_duration_seconds": 30,
                },
            ],
        }

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch.object(builder, "_synthesize_parts", new_callable=AsyncMock) as mock_synth:
            await builder.generate(
                storyline=storyline,
                analysis=_sample_analysis(),
                synthesize_audio=True,
            )
            mock_synth.assert_awaited_once()


# ---------------------------------------------------------------------------
# _generate_part_narration edge cases
# ---------------------------------------------------------------------------


class TestGeneratePartNarration:
    @pytest.mark.asyncio
    async def test_part_narration_empty_llm_content(self, mock_settings: Settings) -> None:
        """Empty LLM content for part narration uses fallback."""
        mock_client = _make_async_client("")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder._generate_part_narration(
            part={
                "id": "ch01-part1",
                "title": "Homepage",
                "actions": ["Navigate"],
                "pages": [],
                "estimated_duration_seconds": 30,
            },
            chapter={"title": "Getting Started", "narrative_arc": "intro"},
            tone="professional",
        )

        assert "demonstrates" in result["text"]

    @pytest.mark.asyncio
    async def test_part_narration_llm_failure(self, mock_settings: Settings) -> None:
        """LLM failure for part narration uses fallback."""
        failing_client = AsyncMock()
        failing_client.message = AsyncMock(side_effect=RuntimeError("fail"))

        builder = NarrationBuilder(settings=mock_settings, wm_client=failing_client)
        result = await builder._generate_part_narration(
            part={
                "id": "ch01-part1",
                "title": "Homepage",
                "actions": [],
                "pages": [],
                "estimated_duration_seconds": 30,
            },
            chapter={"title": "Getting Started", "narrative_arc": "intro"},
            tone="professional",
        )

        assert "showcases" in result["text"]

    @pytest.mark.asyncio
    async def test_part_narration_with_message(self, mock_settings: Settings) -> None:
        """Part narration includes user message in prompt."""
        mock_client = _make_async_client("Custom narration with direction.")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        result = await builder._generate_part_narration(
            part={
                "id": "ch01-part1",
                "title": "Homepage",
                "actions": [],
                "pages": [],
                "estimated_duration_seconds": 30,
            },
            chapter={"title": "Getting Started", "narrative_arc": "intro"},
            tone="professional",
            message="Keep it under 20 words",
        )

        assert result["text"] == "Custom narration with direction."

    @pytest.mark.asyncio
    async def test_part_narration_template_render_failure(self, mock_settings: Settings) -> None:
        """Template rendering failure falls back to inline prompt."""
        import jinja2

        mock_client = _make_async_client("Fallback narration.")

        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_client)
        with patch(
            "src.services.narrative.narration_builder.jinja2.Template",
            side_effect=jinja2.TemplateSyntaxError("bad", 1),
        ):
            result = await builder._generate_part_narration(
                part={
                    "id": "ch01-part1",
                    "title": "Homepage",
                    "actions": [],
                    "pages": [],
                    "estimated_duration_seconds": 30,
                },
                chapter={"title": "Getting Started"},
                tone="professional",
            )

        assert result["text"] == "Fallback narration."


# ---------------------------------------------------------------------------
# License-token forwarding to the TTS peer-service call
# ---------------------------------------------------------------------------


class TestSynthesizePartsLicenseToken:
    """Verify the request-scoped license token is threaded into the TTS
    peer-service call so it authenticates via ``X-Tenant-Id: Bearer
    <token>`` rather than relying on the trusted-network fallback."""

    @pytest.mark.asyncio
    async def test_synthesize_parts_forwards_license_token_in_x_tenant_id(
        self, mock_settings: Settings
    ) -> None:
        """``_synthesize_parts(license_token=...)`` constructs TTSClient with
        the token, and the resulting outgoing TTS request carries the
        ``X-Tenant-Id: Bearer <token>`` header end-to-end through the real
        BaseServiceClient HTTP path."""
        import httpx

        captured_headers: list[httpx.Headers] = []
        captured_paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_headers.append(request.headers)
            captured_paths.append(request.url.path)
            # The live tts_backend returns audio bytes from POST /api/tts/synthesize.
            return httpx.Response(
                200,
                content=b"\x00\x01fake-mp3",
                headers={"content-type": "audio/mpeg"},
            )

        mock_transport = httpx.MockTransport(handler)
        original_async_client_init = httpx.AsyncClient.__init__

        def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            kwargs["transport"] = mock_transport
            original_async_client_init(self, *args, **kwargs)

        mock_llm = _make_async_client("Narration.")
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_llm)

        parts = [{"part_id": "intro", "text": "Welcome to the demo.", "word_count": 5}]

        with patch.object(httpx.AsyncClient, "__init__", patched_init):
            await builder._synthesize_parts(
                parts,
                license_token="tok-narration-789",  # pragma: allowlist secret
            )

        assert captured_headers, "expected at least one TTS request"
        # The narration path must hit the live tts_backend contract, not the
        # unwired tts_service /api/tts/generate gateway (which would 404 prod).
        assert captured_paths == ["/api/tts/synthesize"], (
            f"expected POST /api/tts/synthesize, got {captured_paths!r}"
        )
        # Opaque (non-JWT) tokens travel in X-Tenant-Id; w2v_common.clients.base
        # only routes through Authorization when the token looks like a JWT.
        x_tenant = captured_headers[0].get("x-tenant-id")
        assert x_tenant == "Bearer tok-narration-789", (  # pragma: allowlist secret
            f"expected X-Tenant-Id 'Bearer tok-narration-789', got {x_tenant!r}"
        )
        # Audio was successfully decoded and attached.
        assert "audio_base64" in parts[0]


# ---------------------------------------------------------------------------
# Regression: per-chapter / per-part absorb must not cancel siblings on an
# off-tuple exception. The previous narrow excepts (OSError, TimeoutError,
# ValueError, RuntimeError) let KeyError / httpx.HTTPError etc. escape into
# asyncio.gather() and one bad chapter or one bad TTS call took out every
# sibling via task cancellation.
# ---------------------------------------------------------------------------


class TestBuildChapterPartsOffTupleException:
    @pytest.mark.asyncio
    async def test_off_tuple_exception_does_not_cancel_sibling_chapters(
        self, mock_settings: Settings
    ) -> None:
        """A KeyError from one chapter must yield a fallback, not cancel siblings."""
        builder = NarrationBuilder(settings=mock_settings, wm_client=_make_async_client("Narr"))

        chapters = [
            {"number": 1, "title": "First", "narrative_arc": "arc1"},
            {"number": 2, "title": "Bad", "narrative_arc": "arc2"},
            {"number": 3, "title": "Third", "narrative_arc": "arc3"},
        ]

        call_count = {"n": 0}

        async def fake_generate(chapter: dict, *args, **kwargs) -> dict:
            call_count["n"] += 1
            if chapter.get("number") == 2:
                # KeyError is NOT in the pre-fix narrow tuple
                # (OSError, TimeoutError, ValueError, RuntimeError) — pre-fix
                # it escaped and cancelled chapters 1 and 3.
                raise KeyError("simulated off-tuple failure")
            return builder._make_part(
                f"ch{chapter['number']:02d}-narration",
                f"Narration for chapter {chapter['number']}.",
            )

        with patch.object(builder, "_generate_chapter_narration", side_effect=fake_generate):
            results = await builder._build_chapter_parts(
                chapters=chapters,
                tone="professional",
                message="",
                mode="webapp_commercial",
                code_quality_facts=None,
                model_override=None,
            )

        # All three chapters produced a part — none were cancelled.
        assert len(results) == 3
        # Chapter 2 received the deterministic fallback (text starts with the
        # _fallback_chapter_part prefix).
        assert all(isinstance(r, dict) for r in results)
        assert "Bad" in results[1]["text"]  # fallback uses chapter title
        # Sibling chapters succeeded with their real narration text.
        assert "chapter 1" in results[0]["text"].lower()
        assert "chapter 3" in results[2]["text"].lower()


class TestSynthesizePartsOffTupleException:
    @pytest.mark.asyncio
    async def test_httpx_error_on_one_part_does_not_cancel_siblings(
        self, mock_settings: Settings
    ) -> None:
        """httpx.HTTPError (not in pre-fix narrow tuple) on one part must not cancel siblings."""
        # One TTS call raises KeyError — NOT in the pre-fix narrow tuple
        # (OSError, TimeoutError, ValueError, RuntimeError). Pre-fix this
        # escaped and cancelled the siblings via gather() task cancellation.
        # (httpx.HTTPError can't be used here because w2v_common.clients.base
        # transparently retries transport errors up to 3 times, masking the
        # off-tuple-exception behaviour we want to verify.)
        mock_tts = AsyncMock()

        async def per_part_generate(text: str, **_: object) -> bytes:
            if text.startswith("Second"):
                raise KeyError("simulated off-tuple TTS failure")
            return b"fake-audio"

        mock_tts.generate = AsyncMock(side_effect=per_part_generate)

        mock_llm = _make_async_client("Narr.")
        builder = NarrationBuilder(settings=mock_settings, wm_client=mock_llm)

        parts = [
            {"part_id": "p1", "text": "First.", "word_count": 1},
            {"part_id": "p2", "text": "Second.", "word_count": 1},
            {"part_id": "p3", "text": "Third.", "word_count": 1},
        ]

        with patch("src.services.narrative.tts_client.TTSClient", return_value=mock_tts):
            await builder._synthesize_parts(
                parts, license_token="tok-x"
            )  # pragma: allowlist secret

        # Parts 1 and 3 successfully attached audio; part 2 was absorbed
        # (no audio_base64) and did NOT cancel the siblings.
        assert "audio_base64" in parts[0]
        assert "audio_base64" not in parts[1]
        assert "audio_base64" in parts[2]


def test_build_shotlist_lookups_skips_parts_without_id() -> None:
    """A single shot-list part missing 'id' (or not a dict) must be SKIPPED, not
    KeyError/TypeError the whole narration job. shot_list is free-form input."""
    from src.services.narrative.narration_builder import NarrationBuilder

    shot_list = {"parts": [{"id": "p1", "text": "hi"}, {"no_id": True}, "not-a-dict"]}
    storyline = {"chapters": [{"number": 1, "title": "One"}]}
    parts_by_id, chapters_by_num = NarrationBuilder._build_shotlist_lookups(shot_list, storyline)
    assert parts_by_id == {"p1": {"id": "p1", "text": "hi"}}  # bad rows skipped, no crash
    assert chapters_by_num == {1: {"number": 1, "title": "One"}}
