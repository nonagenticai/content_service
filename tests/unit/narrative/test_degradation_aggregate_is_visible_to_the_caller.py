"""Fabricated content must announce itself at the TOP of the envelope.

Every fail-open fallback already marks its own dict with ``degraded: True``.
That marker is honest but buried — the orchestrator would have to walk
``narration.parts[*]``, ``storyline.degraded`` and ``shot_list.parts[*]`` to
find it, and it does not. So one worker-management hiccup ships a complete,
confident-looking "Product Tour" built entirely from templates, reported 100%
green.

These tests pin the aggregate that fixes that: ``degradation`` on the job/API
envelope, a boolean plus stable reason codes with counts.

The false-positive guard (``TestHealthyPathsReportNothing``) is the load-bearing
half: it protects every job that is working today. A signal that fires on a
healthy job is worse than no signal.

Nothing here asserts a status change. A degraded job is still ``completed``.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from w2v_common.clients.wm_client import WMResponse

from src.api.narrative_schemas import (
    AnalysisInput,
    DegradationReport,
    NarrationResultResponse,
    NarrationScriptResponse,
    PageInput,
    ShotListResponse,
    SiteInput,
    StorylineResponse,
    StorylineResultResponse,
)
from src.config import Settings
from src.services.narrative.degradation import (
    NARRATION_FALLBACK,
    SHOT_DEGRADED,
    SHOT_MISSING_URL,
    STORYLINE_FABRICATED,
    build_report,
    narration_report,
    shotlist_report,
    storyline_report,
)
from src.services.narrative.narration_builder import NarrationBuilder
from src.services.narrative.shotlist_builder import ShotListBuilder
from src.services.narrative.storyline_generator import StorylineGenerator

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _wm_returning(text: str) -> AsyncMock:
    client = AsyncMock()
    client.message = AsyncMock(
        return_value=WMResponse(
            output=text,
            output_type="string",
            output_schema_used=None,
            metadata=None,
            usage_metrics=None,
        )
    )
    client.aclose = AsyncMock()
    return client


def _wm_raising(exc: BaseException) -> AsyncMock:
    client = AsyncMock()
    client.message = AsyncMock(side_effect=exc)
    client.aclose = AsyncMock()
    return client


def _analysis() -> AnalysisInput:
    return AnalysisInput(
        pages=[
            PageInput(url="https://example.com/", title="Acme | Home", page_type="homepage"),
            PageInput(url="https://example.com/pricing", title="Pricing", page_type="pricing"),
        ],
        site=SiteInput(description="Acme does things", tone="professional"),
    )


_VALID_STORYLINE_JSON = json.dumps(
    {
        "video_title": "Acme in 3 Minutes",
        "video_description": "A real storyline",
        "target_duration_minutes": 3.0,
        "tone": "professional",
        "intro": {"title": "Intro", "subtitle": "S", "duration_seconds": 8},
        "chapters": [
            {
                "number": 1,
                "title": "First Look",
                "pages": ["https://example.com/"],
                "narrative_arc": "Introduction",
                "key_moments": ["signup form"],
                "estimated_duration_seconds": 80,
            },
            {
                "number": 2,
                "title": "Pricing",
                "pages": ["https://example.com/pricing"],
                "narrative_arc": "Plans",
                "key_moments": ["tiers"],
                "estimated_duration_seconds": 80,
            },
        ],
        "outro": {"title": "Thanks", "subtitle": "Visit us", "duration_seconds": 6},
    }
)


@pytest.fixture()
def settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# 1. A forced storyline JSON error -> fallback AND the aggregate code
# ---------------------------------------------------------------------------


class TestFabricatedStorylineAnnouncesItself:
    @pytest.mark.asyncio
    async def test_json_error_produces_the_fallback_and_the_aggregate_code(self) -> None:
        """Unparseable WM output: the boilerplate ships, and it SAYS SO."""
        gen = StorylineGenerator(settings=None, wm_client=_wm_returning("not json at all {{{"))

        result = await gen.generate(analysis=_analysis(), duration_minutes=3.0)

        # the fallback itself
        assert result["degraded"] is True
        assert result["video_title"].endswith("Product Tour")

        # the aggregate a caller can read without touching the content JSON
        report = storyline_report(result)
        assert report["degraded"] is True
        assert report["reasons"] == [{"code": STORYLINE_FABRICATED, "count": 1}]

    @pytest.mark.asyncio
    async def test_the_signal_reaches_the_polling_envelope(self) -> None:
        """The exact construction ``get_storyline`` performs on the DB row."""
        gen = StorylineGenerator(settings=None, wm_client=_wm_raising(TimeoutError("WM timeout")))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)

        envelope = StorylineResultResponse(
            job_id="j1",
            status="completed",
            storyline=StorylineResponse(**stored),
            degradation=DegradationReport(**storyline_report(stored)),
        )

        dumped = envelope.model_dump()
        assert dumped["status"] == "completed", "visibility must not turn a job red"
        assert dumped["degradation"]["degraded"] is True
        assert dumped["degradation"]["reasons"] == [{"code": STORYLINE_FABRICATED, "count": 1}]


# ---------------------------------------------------------------------------
# 2. FALSE-POSITIVE GUARD: healthy jobs must report nothing at all
# ---------------------------------------------------------------------------


class TestHealthyPathsReportNothing:
    @pytest.mark.asyncio
    async def test_healthy_storyline_signal_is_empty(self) -> None:
        gen = StorylineGenerator(settings=None, wm_client=_wm_returning(_VALID_STORYLINE_JSON))

        result = await gen.generate(analysis=_analysis(), duration_minutes=3.0)

        assert result.get("degraded") is not True
        assert storyline_report(result) == {"degraded": False, "reasons": []}

    @pytest.mark.asyncio
    async def test_healthy_storyline_envelope_is_not_degraded(self) -> None:
        gen = StorylineGenerator(settings=None, wm_client=_wm_returning(_VALID_STORYLINE_JSON))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)

        envelope = StorylineResultResponse(
            job_id="j1",
            status="completed",
            storyline=StorylineResponse(**stored),
            degradation=DegradationReport(**storyline_report(stored)),
        )
        assert envelope.degradation.degraded is False
        assert envelope.degradation.reasons == []

    def test_healthy_shotlist_signal_is_empty(self) -> None:
        storyline = json.loads(_VALID_STORYLINE_JSON)
        result = ShotListBuilder().build(storyline, _analysis())

        assert any(p["part_type"] == "browser_recording" for p in result["parts"])
        assert shotlist_report(result) == {"degraded": False, "reasons": []}

    def test_a_zero_count_never_appears_as_a_reason(self) -> None:
        """``reasons`` is empty exactly when ``degraded`` is false."""
        assert build_report({NARRATION_FALLBACK: 0, STORYLINE_FABRICATED: 0}) == {
            "degraded": False,
            "reasons": [],
        }

    def test_the_default_envelope_is_not_degraded(self) -> None:
        assert DegradationReport().degraded is False
        assert DegradationReport().reasons == []
        assert ShotListResponse().degradation.degraded is False
        assert (
            StorylineResultResponse(job_id="j", status="processing").degradation.degraded is False
        )


# ---------------------------------------------------------------------------
# 3. A narration fallback increments its OWN code
# ---------------------------------------------------------------------------


class TestNarrationFallbackHasItsOwnCode:
    @pytest.mark.asyncio
    async def test_wm_failure_counts_every_boilerplate_part(self, settings: Settings) -> None:
        builder = NarrationBuilder(
            settings=settings, wm_client=_wm_raising(TimeoutError("WM call timed out"))
        )

        result = await builder.generate(
            storyline=json.loads(_VALID_STORYLINE_JSON), analysis=_analysis()
        )

        degraded_parts = [p for p in result["parts"] if p.get("degraded")]
        assert degraded_parts, "the fallback must mark the parts it substitutes"

        report = narration_report(result)
        assert report["degraded"] is True
        assert report["reasons"] == [{"code": NARRATION_FALLBACK, "count": len(degraded_parts)}], (
            "narration degradation must count under its own code, not the storyline's"
        )

    def test_the_signal_reaches_the_narration_polling_envelope(self) -> None:
        """The exact construction ``get_narration`` performs on the DB row."""
        stored = {
            "parts": [
                {"part_id": "intro", "text": "Real voiceover.", "degraded": False},
                {"part_id": "ch01-narration", "text": "Boilerplate.", "degraded": True},
                {"part_id": "ch02-narration", "text": "Boilerplate.", "degraded": True},
            ],
            "total_words": 5,
            "estimated_total_duration_seconds": 3.0,
            "tone": "professional",
        }

        envelope = NarrationResultResponse(
            job_id="n1",
            status="completed",
            narration=NarrationScriptResponse(**stored),
            degradation=DegradationReport(**narration_report(stored)),
        )

        dumped = envelope.model_dump()
        assert dumped["status"] == "completed"
        assert dumped["degradation"]["reasons"] == [{"code": NARRATION_FALLBACK, "count": 2}]


# ---------------------------------------------------------------------------
# 4. A ``pages: []`` chapter marks the recording part -- with a NAMED reason
# ---------------------------------------------------------------------------


_PAGELESS_STORYLINE = {
    "video_title": "Acme — Product Tour",
    "intro": {"title": "I", "duration_seconds": 5},
    "chapters": [
        {"number": 1, "title": "No Pages", "pages": [], "estimated_duration_seconds": 240.0}
    ],
    "outro": {"title": "O", "duration_seconds": 5},
}


class TestPagelessChapterIsNamedNotJustFlagged:
    def test_the_recording_part_names_its_reason(self) -> None:
        result = ShotListBuilder().build(
            _PAGELESS_STORYLINE, AnalysisInput(pages=[], site=SiteInput(description="T"))
        )
        parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(parts) == 1
        assert parts[0]["pages"] == []
        assert parts[0]["degraded"] is True
        assert parts[0]["degraded_reason"] == SHOT_MISSING_URL, (
            "'degraded' alone cannot tell 'nothing to record' from 'recorded fine'"
        )

    def test_the_named_reason_survives_the_response_model(self) -> None:
        result = ShotListBuilder().build(
            _PAGELESS_STORYLINE, AnalysisInput(pages=[], site=SiteInput(description="T"))
        )
        payload = {k: v for k, v in result.items() if k != "degradation"}
        resp = ShotListResponse(**payload, degradation=DegradationReport(**shotlist_report(result)))

        dumped = resp.model_dump()
        recordings = [p for p in dumped["parts"] if p["part_type"] == "browser_recording"]
        assert recordings[0]["degraded_reason"] == SHOT_MISSING_URL
        assert dumped["degradation"]["degraded"] is True
        assert dumped["degradation"]["reasons"] == [{"code": SHOT_MISSING_URL, "count": 1}]

    def test_an_unnamed_degraded_part_still_counts_under_a_generic_code(self) -> None:
        """A future/foreign producer that flags without naming is not swallowed."""
        report = shotlist_report(
            {"parts": [{"id": "x", "part_type": "browser_recording", "degraded": True}]}
        )
        assert report["reasons"] == [{"code": SHOT_DEGRADED, "count": 1}]


# ---------------------------------------------------------------------------
# 5. Shape invariants of the aggregate itself
# ---------------------------------------------------------------------------


class TestTheRouteHelperItself:
    """``_with_degradation`` is what BOTH shot-list routes and the cached path
    actually call — the construction tested above only mirrors it."""

    def test_it_attaches_the_aggregate_to_a_builder_dict(self) -> None:
        from src.api.narrative.shotlist import _with_degradation

        result = ShotListBuilder().build(
            _PAGELESS_STORYLINE, AnalysisInput(pages=[], site=SiteInput(description="T"))
        )
        resp = _with_degradation(result)
        assert resp.degradation.degraded is True
        assert [r.model_dump() for r in resp.degradation.reasons] == [
            {"code": SHOT_MISSING_URL, "count": 1}
        ]

    def test_it_reports_nothing_for_a_healthy_shot_list(self) -> None:
        from src.api.narrative.shotlist import _with_degradation

        result = ShotListBuilder().build(json.loads(_VALID_STORYLINE_JSON), _analysis())
        assert _with_degradation(result).degradation.degraded is False

    def test_it_derives_rather_than_trusts_a_stale_cached_value(self) -> None:
        """A cache entry written before this field existed (or with a stale one)
        must still report correctly — the aggregate is always recomputed."""
        from src.api.narrative.shotlist import _with_degradation

        stale = {
            "parts": [
                {
                    "id": "ch01-part1",
                    "part_type": "browser_recording",
                    "degraded": True,
                    "degraded_reason": SHOT_MISSING_URL,
                }
            ],
            "degradation": {"degraded": False, "reasons": []},
        }
        assert _with_degradation(stale).degradation.degraded is True

    def test_the_storyline_cache_predicate_still_reads_the_marker(self) -> None:
        """``_is_degraded`` gates the hour-long result cache; it now delegates."""
        from src.api.narrative.storyline import _is_degraded

        assert _is_degraded({"video_title": "X", "degraded": True}) is True
        assert _is_degraded({"video_title": "X"}) is False

    def test_the_narration_cache_predicate_still_reads_the_markers(self) -> None:
        from src.api.narrative.narration import _has_degraded_parts

        assert _has_degraded_parts({"parts": [{"part_id": "p", "degraded": True}]}) is True
        assert _has_degraded_parts({"parts": [{"part_id": "p"}]}) is False


class TestTheGetRoutesEmitTheAggregate:
    """The poll routes are where the orchestrator ACTUALLY reads this.

    Driven with a stub session rather than a TestClient so the wiring is proved
    without a live database (the local CI postgres is not required for it).
    """

    @staticmethod
    def _request() -> MagicMock:
        request = MagicMock()
        request.app.state.redis_client = None  # force the DB branch
        request.state.tenant_id = "aaaaaaaa-0000-4000-8000-00000000000a"
        return request

    @staticmethod
    def _db_returning(row: object) -> AsyncMock:
        db = AsyncMock()
        execute_result = MagicMock()
        execute_result.scalar_one_or_none = MagicMock(return_value=row)
        db.execute = AsyncMock(return_value=execute_result)
        return db

    @pytest.mark.asyncio
    async def test_get_storyline_reports_the_fabricated_code(self) -> None:
        from src.api.narrative.storyline import get_storyline

        gen = StorylineGenerator(settings=None, wm_client=_wm_returning("not json {{{"))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)
        row = SimpleNamespace(status="completed", storyline=stored, error=None)

        result = await get_storyline("j1", self._request(), self._db_returning(row))

        assert result.status == "completed", "visibility must not turn a job red"
        assert result.degradation.degraded is True
        assert [r.model_dump() for r in result.degradation.reasons] == [
            {"code": STORYLINE_FABRICATED, "count": 1}
        ]

    @pytest.mark.asyncio
    async def test_get_storyline_reports_nothing_for_a_healthy_job(self) -> None:
        from src.api.narrative.storyline import get_storyline

        gen = StorylineGenerator(settings=None, wm_client=_wm_returning(_VALID_STORYLINE_JSON))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)
        row = SimpleNamespace(status="completed", storyline=stored, error=None)

        result = await get_storyline("j1", self._request(), self._db_returning(row))

        assert result.degradation.degraded is False
        assert result.degradation.reasons == []

    @pytest.mark.asyncio
    async def test_get_narration_counts_the_boilerplate_parts(self) -> None:
        from src.api.narrative.narration import get_narration

        stored = {
            "parts": [
                {"part_id": "intro", "text": "Real voiceover."},
                {"part_id": "ch01-narration", "text": "Boilerplate.", "degraded": True},
            ],
            "total_words": 4,
            "estimated_total_duration_seconds": 2.0,
            "tone": "professional",
        }
        row = SimpleNamespace(status="completed", narration=stored, error=None)

        result = await get_narration("n1", self._request(), self._db_returning(row))

        assert result.status == "completed"
        assert [r.model_dump() for r in result.degradation.reasons] == [
            {"code": NARRATION_FALLBACK, "count": 1}
        ]

    @pytest.mark.asyncio
    async def test_get_narration_reports_nothing_for_a_healthy_job(self) -> None:
        from src.api.narrative.narration import get_narration

        stored = {
            "parts": [{"part_id": "intro", "text": "Real voiceover."}],
            "total_words": 2,
            "estimated_total_duration_seconds": 1.0,
            "tone": "professional",
        }
        row = SimpleNamespace(status="completed", narration=stored, error=None)

        result = await get_narration("n1", self._request(), self._db_returning(row))

        assert result.degradation.degraded is False
        assert result.degradation.reasons == []


class TestTheCachedPollBranchEmitsItToo:
    """A signal only ONE of two branches emits is not a signal.

    Both poll routes answer from the Redis job-status key when it is warm and
    never touch the DB — which is the common case for a poll loop. The aggregate
    has to be computed there as well.
    """

    @staticmethod
    def _request(cached: dict) -> MagicMock:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached)
        redis.set = AsyncMock()
        request = MagicMock()
        request.app.state.redis_client = redis
        request.state.tenant_id = "aaaaaaaa-0000-4000-8000-00000000000a"
        return request

    @pytest.mark.asyncio
    async def test_cached_storyline_poll_reports_the_fabricated_code(self) -> None:
        from src.api.narrative.storyline import get_storyline

        gen = StorylineGenerator(settings=None, wm_client=_wm_returning("not json {{{"))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)
        request = self._request({"status": "completed", "storyline": stored})

        result = await get_storyline("j1", request, AsyncMock())

        assert result.degradation.degraded is True
        assert [r.model_dump() for r in result.degradation.reasons] == [
            {"code": STORYLINE_FABRICATED, "count": 1}
        ]

    @pytest.mark.asyncio
    async def test_cached_healthy_storyline_poll_reports_nothing(self) -> None:
        from src.api.narrative.storyline import get_storyline

        gen = StorylineGenerator(settings=None, wm_client=_wm_returning(_VALID_STORYLINE_JSON))
        stored = await gen.generate(analysis=_analysis(), duration_minutes=3.0)
        request = self._request({"status": "completed", "storyline": stored})

        result = await get_storyline("j1", request, AsyncMock())

        assert result.degradation.degraded is False
        assert result.degradation.reasons == []

    @pytest.mark.asyncio
    async def test_cached_narration_poll_counts_the_boilerplate_parts(self) -> None:
        from src.api.narrative.narration import get_narration

        cached = {
            "status": "completed",
            "narration": {
                "parts": [
                    {"part_id": "intro", "text": "Real voiceover."},
                    {"part_id": "ch01-narration", "text": "Boilerplate.", "degraded": True},
                ],
                "tone": "professional",
            },
        }

        result = await get_narration("n1", self._request(cached), AsyncMock())

        assert [r.model_dump() for r in result.degradation.reasons] == [
            {"code": NARRATION_FALLBACK, "count": 1}
        ]

    @pytest.mark.asyncio
    async def test_cached_healthy_narration_poll_reports_nothing(self) -> None:
        from src.api.narrative.narration import get_narration

        cached = {
            "status": "completed",
            "narration": {
                "parts": [{"part_id": "intro", "text": "Real voiceover."}],
                "tone": "professional",
            },
        }

        result = await get_narration("n1", self._request(cached), AsyncMock())

        assert result.degradation.degraded is False
        assert result.degradation.reasons == []


class TestReportShape:
    def test_reasons_are_sorted_by_code_for_a_deterministic_envelope(self) -> None:
        report = build_report({SHOT_MISSING_URL: 1, NARRATION_FALLBACK: 2, STORYLINE_FABRICATED: 1})
        assert [r["code"] for r in report["reasons"]] == sorted(
            [SHOT_MISSING_URL, NARRATION_FALLBACK, STORYLINE_FABRICATED]
        )
        assert report["degraded"] is True

    def test_non_dict_inputs_do_not_invent_degradation(self) -> None:
        for junk in (None, [], "storyline", 0):
            assert storyline_report(junk) == {"degraded": False, "reasons": []}
            assert narration_report(junk) == {"degraded": False, "reasons": []}
            assert shotlist_report(junk) == {"degraded": False, "reasons": []}
