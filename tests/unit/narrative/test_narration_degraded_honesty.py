"""Placeholder narration must be distinguishable from real narration.

Four coupled halves are pinned here:

1. ``NarrationPartResponse`` must carry ``degraded`` -- otherwise every
   producer that sets it (chapter fallback, part fallback, storyline) has the
   flag silently stripped by pydantic on the way out of the API.
2. The chapter path's own fallbacks (LLM exception AND empty-text) must set
   ``degraded`` in the dict they return.
3. The narrative WM timeout default must be realistic for a thinking-on call.
4. A degraded narration must not be cached for an hour and replayed.
5. The shot-list path's own fallbacks must set ``degraded`` at the point of
   substitution -- its inner absorb makes the caller's ``except`` unreachable
   for the dominant failure (WM timeout / HTTPError).
6. A degraded storyline must not be cached for an hour and replayed either;
   every narration, shot list and video downstream is built on it.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.narrative_schemas import (
    AnalysisInput,
    NarrationPartResponse,
    NarrationRequest,
    NarrationScriptResponse,
    StorylineRequest,
)
from src.cache.cache_keys import NARRATION_CACHE_TTL, STORYLINE_CACHE_TTL
from src.config import Settings
from src.services.narrative.narration_builder import NarrationBuilder

# ── 1. the response schema must not strip ``degraded`` ─────────────────


class TestDegradedSurvivesTheResponseSchema:
    def test_part_response_has_degraded_field(self) -> None:
        assert NarrationPartResponse(part_id="p", degraded=True).degraded is True

    def test_part_response_defaults_to_not_degraded(self) -> None:
        assert NarrationPartResponse(part_id="p").degraded is False

    def test_builder_dict_round_trips_through_script_response(self) -> None:
        """The exact shape the GET route builds: NarrationScriptResponse(**row)."""
        raw: dict[str, Any] = {
            "parts": [
                {
                    "part_id": "ch01-narration",
                    "text": "In this section we take a look at the feature.",
                    "estimated_duration_seconds": 4.0,
                    "word_count": 9,
                    "degraded": True,
                }
            ],
            "total_words": 9,
            "estimated_total_duration_seconds": 4.0,
            "tone": "professional",
        }
        resp = NarrationScriptResponse.model_validate(raw)
        assert resp.parts[0].degraded is True, "degraded was stripped by the response schema"


# ── 2. the chapter fallbacks must mark themselves degraded ─────────────


def _builder(settings: Settings, wm: AsyncMock) -> NarrationBuilder:
    return NarrationBuilder(settings=settings, wm_client=wm)


@pytest.fixture()
def settings() -> Settings:
    return Settings()


class TestChapterFallbacksAreMarkedDegraded:
    @pytest.mark.asyncio
    async def test_llm_failure_returns_degraded_part(self, settings: Settings) -> None:
        wm = AsyncMock()
        wm.message = AsyncMock(side_effect=TimeoutError("WM call timed out"))
        wm.aclose = AsyncMock()

        part = await _builder(settings, wm)._generate_chapter_narration(
            chapter={"number": 1, "title": "Dashboard", "estimated_duration_seconds": 30},
            tone="professional",
        )

        assert part["text"], "fallback must still produce text"
        assert part.get("degraded") is True, (
            "boilerplate fallback narration is indistinguishable from real narration"
        )

    @pytest.mark.asyncio
    async def test_empty_llm_output_returns_degraded_part(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output="",
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()

        part = await _builder(settings, wm)._generate_chapter_narration(
            chapter={"number": 2, "title": "Reports", "estimated_duration_seconds": 30},
            tone="professional",
        )

        assert part.get("degraded") is True

    @pytest.mark.asyncio
    async def test_real_llm_output_is_not_degraded(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        text = (
            "Because the reporting surface consolidates every downstream signal, "
            "operators can consequently reconcile discrepancies without exporting "
            "intermediate artifacts, which is what sets this workflow apart."
        )
        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output=text,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()

        part = await _builder(settings, wm)._generate_chapter_narration(
            chapter={"number": 3, "title": "Reports", "estimated_duration_seconds": 30},
            tone="professional",
        )
        assert part.get("degraded") is False


# ── 3. the timeout default must fit a thinking-on WM call ──────────────


class TestNarrativeTimeoutDefault:
    def test_default_exceeds_measured_thinking_on_latency(self) -> None:
        """Thinking-on WM narration calls have been measured over 10 minutes."""
        assert Settings().narrative_llm_timeout_seconds >= 600

    def test_default_stays_under_wm_agent_run_timeout(self) -> None:
        """WM's own AGENT_RUN_TIMEOUT_S is 1200 -- let WM's error surface first."""
        assert Settings().narrative_llm_timeout_seconds <= 1200


# ── 4. degraded narration must not be cached for an hour ───────────────


class _AsyncCM:
    def __init__(self, obj: Any) -> None:
        self._obj = obj

    async def __aenter__(self) -> Any:
        return self._obj

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _narration_request() -> NarrationRequest:
    return NarrationRequest(storyline={"video_title": "T"}, analysis=AnalysisInput())


async def _run_process_narration(result: dict) -> AsyncMock:
    """Drive the real ``_process_narration`` with a mocked builder + redis."""
    from src.api.narrative import narration as narration_mod

    builder = AsyncMock()
    builder.generate = AsyncMock(return_value=result)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    app = MagicMock()
    app.state.redis_client = redis

    row = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))

    with (
        patch.object(narration_mod, "get_db_session", lambda: _AsyncCM(db)),
        patch.object(narration_mod._shared, "_cache_enabled", lambda: True),
    ):
        await narration_mod._process_narration(
            builder,
            "job-1",
            _narration_request(),
            app,
            "t1",
        )
    return redis


def _long_lived_cache_writes(redis: AsyncMock) -> list[Any]:
    return [c for c in redis.set.await_args_list if c.kwargs.get("ttl") == NARRATION_CACHE_TTL]


class TestDegradedNarrationIsNotCached:
    @pytest.mark.asyncio
    async def test_healthy_result_is_cached(self) -> None:
        redis = await _run_process_narration(
            {"parts": [{"part_id": "a", "text": "real", "degraded": False}], "tone": "t"}
        )
        assert _long_lived_cache_writes(redis), "healthy narration must still be cached"

    @pytest.mark.asyncio
    async def test_degraded_result_is_not_cached_for_an_hour(self) -> None:
        redis = await _run_process_narration(
            {"parts": [{"part_id": "a", "text": "boilerplate", "degraded": True}], "tone": "t"}
        )
        assert not _long_lived_cache_writes(redis), (
            "placeholder narration was cached and will be replayed for an hour"
        )


# ── 5. the shot-list fallbacks must mark themselves degraded ───────────


_SHOTLIST_PART = {
    "id": "p01",
    "title": "Dashboard",
    "actions": ["click"],
    "estimated_duration_seconds": 30,
}


async def _shotlist_part(settings: Settings, wm: AsyncMock) -> dict:
    """Drive the REAL caller path, so the inner absorb is exercised."""
    return await _builder(settings, wm)._handle_browser_recording_part(
        part=dict(_SHOTLIST_PART),
        part_id="p01",
        chapters_by_num={1: {"number": 1, "title": "Dashboard"}},
        chapter_recording_counts={1: 1},
        chapter_recording_seen={},
        tone="professional",
        mode="webapp_commercial",
        previous_narration="",
        message="",
        code_quality_facts=None,
        model_override=None,
    )


class TestShotListFallbacksAreMarkedDegraded:
    @pytest.mark.asyncio
    async def test_wm_timeout_returns_degraded_part(self, settings: Settings) -> None:
        """The dominant failure: WM times out inside ``_generate_part_narration``.

        The inner ``except`` absorbs it, so the caller's ``except`` never runs --
        the mark has to be set at the point of substitution.
        """
        wm = AsyncMock()
        wm.message = AsyncMock(side_effect=TimeoutError("WM call timed out"))
        wm.aclose = AsyncMock()

        part = await _shotlist_part(settings, wm)

        assert part["text"], "fallback must still produce text"
        assert part.get("degraded") is True, (
            "boilerplate shot-list narration is indistinguishable from real narration"
        )

    @pytest.mark.asyncio
    async def test_wm_http_error_returns_degraded_part(self, settings: Settings) -> None:
        import httpx

        wm = AsyncMock()
        wm.message = AsyncMock(side_effect=httpx.ConnectError("WM unreachable"))
        wm.aclose = AsyncMock()

        assert (await _shotlist_part(settings, wm)).get("degraded") is True

    @pytest.mark.asyncio
    async def test_empty_llm_output_returns_degraded_part(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output="",
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()

        assert (await _shotlist_part(settings, wm)).get("degraded") is True

    @pytest.mark.asyncio
    async def test_real_llm_output_is_not_degraded(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        text = (
            "Because the reporting surface consolidates every downstream signal, "
            "operators can consequently reconcile discrepancies without exporting "
            "intermediate artifacts, which is what sets this workflow apart."
        )
        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output=text,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()

        part = await _shotlist_part(settings, wm)
        assert part["text"] == text
        assert part.get("degraded") is False


# ── 6. degraded storylines must not be cached for an hour ──────────────


async def _run_process_storyline(result: dict) -> AsyncMock:
    """Drive the real ``_process_storyline`` with a mocked generator + redis."""
    from src.api.narrative import storyline as storyline_mod

    gen = AsyncMock()
    gen.generate = AsyncMock(return_value=result)

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    app = MagicMock()
    app.state.redis_client = redis

    row = MagicMock()
    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: row))

    with (
        patch.object(storyline_mod, "get_db_session", lambda: _AsyncCM(db)),
        patch.object(storyline_mod._shared, "_cache_enabled", lambda: True),
    ):
        await storyline_mod._process_storyline(
            gen,
            "job-1",
            StorylineRequest(analysis=AnalysisInput()),
            app,
            "t1",
        )
    return redis


def _long_lived_storyline_writes(redis: AsyncMock) -> list[Any]:
    return [c for c in redis.set.await_args_list if c.kwargs.get("ttl") == STORYLINE_CACHE_TTL]


class TestDegradedStorylineIsNotCached:
    @pytest.mark.asyncio
    async def test_healthy_storyline_is_cached(self) -> None:
        """The cache must keep doing its job for real storylines."""
        redis = await _run_process_storyline({"video_title": "Real", "chapters": []})
        assert _long_lived_storyline_writes(redis), "healthy storyline must still be cached"

    @pytest.mark.asyncio
    async def test_degraded_storyline_is_not_cached_for_an_hour(self) -> None:
        redis = await _run_process_storyline(
            {"degraded": True, "video_title": "Acme — Product Tour", "chapters": []}
        )
        assert not _long_lived_storyline_writes(redis), (
            "placeholder storyline was cached and will be replayed for an hour"
        )

    @pytest.mark.asyncio
    async def test_a_later_identical_request_regenerates_after_a_degrade(self) -> None:
        """The point of the guard: the next identical request must hit the generator.

        The POST handler serves a cache HIT without ever calling the generator,
        so an entry left behind by a degraded run is what replays boilerplate.
        Nothing long-lived is written, therefore the next request misses.
        """
        from src.api.narrative import storyline as storyline_mod

        redis = await _run_process_storyline(
            {"degraded": True, "video_title": "Acme — Product Tour", "chapters": []}
        )
        body = StorylineRequest(analysis=AnalysisInput())
        with patch.object(storyline_mod._shared, "_cache_enabled", lambda: True):
            cache_key = storyline_mod._storyline_cache_key_for(body, "t1")
        cached_keys = [c.args[0] for c in redis.set.await_args_list if c.args]
        assert cache_key not in cached_keys, (
            "the body-hash cache key was written, so the next identical request is "
            "served boilerplate as a cache HIT and the generator is never called"
        )


# ── 7. the intro/outro bookend fallbacks must mark themselves degraded ─


_HEALTHY = (
    "Because the reporting surface consolidates every downstream signal, "
    "operators can consequently reconcile discrepancies without exporting "
    "intermediate artifacts, which is what sets this workflow apart."
)

_STORYLINE_WITH_BOOKENDS: dict[str, Any] = {
    "video_title": "Acme",
    "tone": "professional",
    "intro": {"narrative_text": "Open on the dashboard, camera pans across the KPI row."},
    "outro": {"narrative_text": "Fade to the logo lockup."},
    "chapters": [{"number": 1, "title": "Dashboard", "estimated_duration_seconds": 30}],
}


def _bookend_only_failure_wm() -> AsyncMock:
    """WM that fails ONLY the intro/outro rewrite; chapters succeed normally.

    This is the real production shape: one transient WM failure on a single
    bookend call while every chapter comes back fine.
    """
    from w2v_common.clients.wm_client import WMResponse

    async def _message(*_: Any, **kwargs: Any) -> WMResponse:
        # Only the bookend prompt has this exact phrasing; the chapter
        # templates say "voice-over narration for a video chapter".
        if "voice-over for a professional demo video" in str(kwargs.get("message", "")):
            raise TimeoutError("WM call timed out")
        return WMResponse(
            output=_HEALTHY,
            output_type="string",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )

    wm = AsyncMock()
    wm.message = AsyncMock(side_effect=_message)
    wm.aclose = AsyncMock()
    return wm


class TestBookendFallbacksAreMarkedDegraded:
    @pytest.mark.asyncio
    async def test_wm_failure_returns_the_stage_direction_marked_degraded(
        self, settings: Settings
    ) -> None:
        text, degraded = await _builder(settings, _bookend_only_failure_wm())._rewrite_bookend(
            stage_direction="Open on the dashboard, camera pans across the KPI row.",
            section="intro",
            video_title="Acme",
            tone="professional",
        )
        assert text == "Open on the dashboard, camera pans across the KPI row."
        assert degraded is True, (
            "an un-rewritten stage direction is a director's instruction, not "
            "voiceover -- TTS will read it out loud"
        )

    @pytest.mark.asyncio
    async def test_real_rewrite_is_not_degraded(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output=_HEALTHY,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()
        _, degraded = await _builder(settings, wm)._rewrite_bookend(
            stage_direction="Open on the dashboard.",
            section="intro",
            video_title="Acme",
            tone="professional",
        )
        assert degraded is False, "a healthy rewrite must not be marked degraded"

    @pytest.mark.asyncio
    async def test_generate_marks_the_bookend_parts_and_the_result_is_seen_as_degraded(
        self, settings: Settings
    ) -> None:
        """End-to-end: the flag must reach ``_has_degraded_parts``.

        That predicate is the ONLY thing standing between a bookend failure and
        an hour of replaying stage directions out of the result cache.
        """
        from src.api.narrative.narration import _has_degraded_parts

        result = await _builder(settings, _bookend_only_failure_wm()).generate(
            storyline=_STORYLINE_WITH_BOOKENDS,
            analysis=AnalysisInput(),
        )

        by_id = {p["part_id"]: p for p in result["parts"]}
        assert by_id["intro"].get("degraded") is True
        assert by_id["outro"].get("degraded") is True
        assert _has_degraded_parts(result) is True, (
            "a bookend-only WM failure is invisible to the cache guard, so the "
            "stage direction is cached and replayed for NARRATION_CACHE_TTL"
        )

    @pytest.mark.asyncio
    async def test_generate_with_healthy_bookends_is_not_degraded(self, settings: Settings) -> None:
        """The guard must not fire on healthy output, or nothing is ever cached."""
        from w2v_common.clients.wm_client import WMResponse

        from src.api.narrative.narration import _has_degraded_parts

        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output=_HEALTHY,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()

        result = await _builder(settings, wm).generate(
            storyline=_STORYLINE_WITH_BOOKENDS,
            analysis=AnalysisInput(),
        )
        assert _has_degraded_parts(result) is False

    @pytest.mark.asyncio
    async def test_shotlist_intro_part_is_marked_degraded(self, settings: Settings) -> None:
        """The shot-list path has its own bookend caller -- it must mark too.

        ``_handle_intro_outro_part`` builds the part dict by hand rather than
        going through ``_make_part``, so it is the classic guarded-beside-
        unguarded twin of ``_build_bookend_part``.
        """
        part = await _builder(settings, _bookend_only_failure_wm())._handle_intro_outro_part(
            part={"id": "intro", "narrative_text": "Open on the dashboard."},
            part_id="intro",
            storyline=_STORYLINE_WITH_BOOKENDS,
            tone="professional",
            message="",
            model_override=None,
        )
        assert part is not None
        assert part["text"] == "Open on the dashboard."
        assert part.get("degraded") is True

    @pytest.mark.asyncio
    async def test_shotlist_intro_part_healthy_is_not_degraded(self, settings: Settings) -> None:
        from w2v_common.clients.wm_client import WMResponse

        wm = AsyncMock()
        wm.message = AsyncMock(
            return_value=WMResponse(
                output=_HEALTHY,
                output_type="string",
                output_schema_used=None,
                metadata=None,
                usage_metrics=None,
            )
        )
        wm.aclose = AsyncMock()
        part = await _builder(settings, wm)._handle_intro_outro_part(
            part={"id": "intro", "narrative_text": "Open on the dashboard."},
            part_id="intro",
            storyline=_STORYLINE_WITH_BOOKENDS,
            tone="professional",
            message="",
            model_override=None,
        )
        assert part is not None
        assert part.get("degraded") is False


# ── 7. a part whose AUDIO failed is degraded too ───────────────────────
#
# ``_synthesize_parts`` is fail-open per part: a TTS outage leaves the part
# with its text but WITHOUT ``audio_base64``. Unmarked, that part is shaped
# exactly like a healthy one, ``_has_degraded_parts`` sees nothing, and the
# whole narration is written to the hour-long result cache -- so every
# identical re-request for the next NARRATION_CACHE_TTL seconds replays a
# narration with silent segments. A retry cannot heal it.


def _tts_builder(settings: Settings) -> NarrationBuilder:
    return NarrationBuilder(settings=settings, wm_client=AsyncMock())


async def _synthesize(settings: Settings, parts: list[dict], tts: AsyncMock) -> list[dict]:
    with patch("src.services.narrative.tts_client.TTSClient", return_value=tts):
        await _tts_builder(settings)._synthesize_parts(parts)
    return parts


class TestAudioFailureIsMarkedDegraded:
    @pytest.mark.asyncio
    async def test_tts_failure_marks_the_part_degraded(self, settings: Settings) -> None:
        tts = AsyncMock()
        tts.generate = AsyncMock(side_effect=RuntimeError("TTS down"))
        parts = await _synthesize(
            settings, [{"part_id": "intro", "text": "Welcome.", "word_count": 1}], tts
        )
        assert "audio_base64" not in parts[0]
        assert parts[0].get("degraded") is True, (
            "a part with text but NO audio is a silent segment in the render; "
            "unmarked it is cached as clean for an hour"
        )

    @pytest.mark.asyncio
    async def test_successful_synthesis_is_not_marked_degraded(self, settings: Settings) -> None:
        tts = AsyncMock()
        tts.generate = AsyncMock(return_value=b"audio")
        parts = await _synthesize(
            settings, [{"part_id": "intro", "text": "Welcome.", "word_count": 1}], tts
        )
        assert "audio_base64" in parts[0]
        assert parts[0].get("degraded") is not True

    @pytest.mark.asyncio
    async def test_empty_text_part_is_not_marked_degraded(self, settings: Settings) -> None:
        """No synthesis was ever attempted for a blank part -- nothing degraded."""
        tts = AsyncMock()
        tts.generate = AsyncMock(side_effect=RuntimeError("TTS down"))
        parts = await _synthesize(settings, [{"part_id": "blank", "text": "  "}], tts)
        assert parts[0].get("degraded") is not True
        tts.generate.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_text_degrade_is_not_cleared_by_a_successful_synthesis(
        self, settings: Settings
    ) -> None:
        """Audio marking must never overwrite a text fallback's own True."""
        tts = AsyncMock()
        tts.generate = AsyncMock(return_value=b"audio")
        parts = await _synthesize(
            settings, [{"part_id": "intro", "text": "Boilerplate.", "degraded": True}], tts
        )
        assert parts[0]["degraded"] is True

    @pytest.mark.asyncio
    async def test_only_the_failing_part_is_marked(self, settings: Settings) -> None:
        tts = AsyncMock()
        tts.generate = AsyncMock(side_effect=[b"audio", RuntimeError("TTS down")])
        parts = await _synthesize(
            settings,
            [
                {"part_id": "a", "text": "One.", "word_count": 1},
                {"part_id": "b", "text": "Two.", "word_count": 1},
            ],
            tts,
        )
        assert parts[0].get("degraded") is not True
        assert parts[1].get("degraded") is True


class TestAudioFailureReachesTheCacheGate:
    @pytest.mark.asyncio
    async def test_an_audioless_result_is_not_cached_for_an_hour(self, settings: Settings) -> None:
        """The two halves joined: TTS outage -> degraded -> cache skipped."""
        tts = AsyncMock()
        tts.generate = AsyncMock(side_effect=RuntimeError("TTS down"))
        parts = await _synthesize(
            settings, [{"part_id": "intro", "text": "Welcome.", "word_count": 1}], tts
        )
        redis = await _run_process_narration({"parts": parts, "tone": "t"})
        assert not _long_lived_cache_writes(redis), (
            "narration with silent segments was cached and will be replayed for an hour"
        )

    @pytest.mark.asyncio
    async def test_a_base_exception_escaping_the_inner_absorb_also_marks(
        self, settings: Settings
    ) -> None:
        """``except Exception`` cannot catch a BaseException; gather returns it.

        DEFENCE-IN-DEPTH, not a demonstrated production path. An earlier version
        of this docstring called the outer handler "the second no-audio exit",
        which overclaims: ``return_exceptions=True`` only captures a BaseException
        raised by a *child*, and the realistic one -- ``CancelledError`` from a
        cancel of the whole task -- propagates out of ``gather`` before this
        result loop ever runs. ``MemoryError`` is an ``Exception`` and is taken by
        the inner absorb. So this pins the contract for a synthetic BaseException
        subclass; the handler is kept because it is free and the hole it closes is
        real if anything ever does reach it, not because it is known to fire.
        """

        class _Boom(BaseException):
            pass

        tts = AsyncMock()
        tts.generate = AsyncMock(side_effect=_Boom("hard stop"))
        parts = await _synthesize(
            settings, [{"part_id": "intro", "text": "Welcome.", "word_count": 1}], tts
        )
        assert "audio_base64" not in parts[0]
        assert parts[0].get("degraded") is True
