"""Tests for narrative_routes.py endpoint handlers.

Covers: start_storyline, get_storyline, build_shotlist, extract_narrative,
plan_storyline, start_narration, get_narration, start_narration_shotlist,
enhance_shotlist, storyline_from_findings, shotlist_from_findings.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.narrative_routes import (
    TaskContext,
    _cache_enabled,
    build_shotlist,
    enhance_shotlist,
    extract_narrative,
    get_narration,
    get_storyline,
    plan_storyline,
    shotlist_from_findings,
    start_narration,
    start_narration_shotlist,
    start_storyline,
    storyline_from_findings,
)
from src.api.narrative_schemas import (
    AnalysisInput,
    ChapterPlanRequest,
    FindingsToShotListRequest,
    FindingsToStorylineRequest,
    NarrationForShotListRequest,
    NarrationRequest,
    NarrativeExtractRequest,
    PageInput,
    ShotListEnhanceRequest,
    ShotListRequest,
    StorylineRequest,
)
from src.api.narrative_types import CritiqueFinding
from tests.conftest import TENANT_A, TENANT_A_STR, TENANT_B_STR, TENANT_C, TENANT_C_STR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    *,
    redis_client=None,
    storyline_generator=None,
    shotlist_builder=None,
    narration_builder=None,
    narrative_extractor=None,
    shotlist_enhancer=None,
    tenant_id=TENANT_A_STR,
):
    """Build a mock Request with app state for endpoint tests."""
    request = MagicMock()
    request.state.tenant_id = tenant_id
    request.app.state.redis_client = redis_client
    if storyline_generator:
        request.app.state.storyline_generator = storyline_generator
    if shotlist_builder:
        request.app.state.shotlist_builder = shotlist_builder
    if narration_builder:
        request.app.state.narration_builder = narration_builder
    if narrative_extractor:
        request.app.state.narrative_extractor = narrative_extractor
    if shotlist_enhancer:
        request.app.state.shotlist_enhancer = shotlist_enhancer
    return request


def _make_db_session():
    """Build a mock async DB session."""
    db = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=mock_result)
    return db


def _make_analysis():
    """Build a minimal AnalysisInput."""
    return AnalysisInput(pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")])


@pytest.fixture(autouse=True)
def _stub_background_pipelines():
    """Route-handler tests assert the HTTP response, not the background pipeline.

    The POST handlers fire ``_process_*`` via ``asyncio.create_task``; left
    unpatched they run the real pipeline against mock app-state (e.g. a bare
    ``AsyncMock`` redis whose ``_ensure_connected`` returns a coroutine), leaking
    an unretrieved task exception. Stub them to harmless no-op coroutines so each
    test exercises only the route it targets. Direct ``_process_*`` tests live in
    test_routes_background_tasks.py.
    """
    with (
        patch("src.api.narrative.storyline._process_storyline", new=AsyncMock()),
        patch("src.api.narrative.narration._process_narration", new=AsyncMock()),
        patch("src.api.narrative.narration._process_narration_shotlist", new=AsyncMock()),
    ):
        yield


# ---------------------------------------------------------------------------
# TaskContext
# ---------------------------------------------------------------------------


class TestTaskContext:
    def test_task_context_defaults(self) -> None:
        ctx = TaskContext(job_id="j1", tenant_id="t1")
        assert ctx.job_id == "j1"
        assert ctx.tenant_id == "t1"
        assert ctx.request_id == ""

    def test_task_context_with_request_id(self) -> None:
        ctx = TaskContext(job_id="j1", tenant_id="t1", request_id="r1")
        assert ctx.request_id == "r1"


# ---------------------------------------------------------------------------
# _cache_enabled
# ---------------------------------------------------------------------------


class TestCacheEnabled:
    def test_cache_enabled_true(self) -> None:
        mock_settings = MagicMock()
        mock_settings.cache.enabled = True
        with patch("src.api.narrative._shared.get_settings", return_value=mock_settings):
            assert _cache_enabled() is True

    def test_cache_enabled_false(self) -> None:
        mock_settings = MagicMock()
        mock_settings.cache.enabled = False
        with patch("src.api.narrative._shared.get_settings", return_value=mock_settings):
            assert _cache_enabled() is False


# ---------------------------------------------------------------------------
# start_storyline
# ---------------------------------------------------------------------------


class TestStartStoryline:
    @pytest.mark.asyncio
    async def test_start_storyline_no_cache(self) -> None:
        """start_storyline creates a processing job when cache misses."""
        db = _make_db_session()
        request = _make_request(
            storyline_generator=MagicMock(),
        )
        body = StorylineRequest(
            analysis=_make_analysis(),
            duration_minutes=3.0,
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=False):
            resp = await start_storyline(body, request, db)

        assert resp.status == "processing"
        assert resp.job_id  # should be a uuid7 string
        db.add.assert_called_once()
        db.flush.assert_awaited()
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_start_storyline_propagates_correlation_and_tenant_to_bg_task(self) -> None:
        """The dispatch must carry the request's cost-correlation + effective tenant
        into the detached _process_storyline task, or its WM/TTS cost events go out
        with a NULL correlation_id and the wrong tenant. Guards the create_task
        context-inheritance contract against a future regression (e.g. moving the
        work to a thread/process pool or a blank context)."""
        from w2v_common.auth import (
            clear_identity_context,
            get_correlation_id_from_context,
            get_stage_from_context,
            get_tenant_id_from_context,
            set_correlation_context,
            set_identity_context,
        )

        from src.api.narrative_routes import _background_tasks

        seen: dict[str, object] = {}

        async def _spy(*_args, **_kwargs) -> None:
            seen["correlation_id"] = get_correlation_id_from_context()
            seen["stage"] = get_stage_from_context()
            # w2v-common 0.67.0: the identity ContextVar carries a ``uuid.UUID``,
            # not a str. Typing ``seen`` as ``dict[str, object]`` keeps the
            # assertion on the real propagated value instead of stringifying it.
            seen["tenant_id"] = get_tenant_id_from_context()

        db = _make_db_session()
        request = _make_request(storyline_generator=MagicMock(), tenant_id=TENANT_C_STR)
        body = StorylineRequest(analysis=_make_analysis(), duration_minutes=3.0)

        set_identity_context(TENANT_C, "lic-1", "subj-1")
        set_correlation_context("run-xyz", "narrative_storyline")
        try:
            with (
                patch("src.api.narrative._shared._cache_enabled", return_value=False),
                patch("src.api.narrative.storyline._process_storyline", new=_spy),
            ):
                await start_storyline(body, request, db)
                # Let the scheduled background task run to completion.
                await asyncio.gather(*list(_background_tasks))
        finally:
            clear_identity_context()
            set_correlation_context(None, None)

        assert seen["correlation_id"] == "run-xyz"
        assert seen["stage"] == "narrative_storyline"
        assert seen["tenant_id"] == TENANT_C

    @pytest.mark.asyncio
    async def test_start_storyline_cache_hit(self) -> None:
        """start_storyline returns completed when cache hits."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value={"video_title": "Cached", "chapters": []})

        db = _make_db_session()
        request = _make_request(
            redis_client=redis,
            storyline_generator=MagicMock(),
        )
        body = StorylineRequest(
            analysis=_make_analysis(),
            duration_minutes=3.0,
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await start_storyline(body, request, db)

        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_start_storyline_redis_check_fails(self) -> None:
        """start_storyline is fail-open when redis cache check raises."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        db = _make_db_session()
        request = _make_request(
            redis_client=redis,
            storyline_generator=MagicMock(),
        )
        body = StorylineRequest(
            analysis=_make_analysis(),
            duration_minutes=3.0,
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await start_storyline(body, request, db)

        # Should still proceed to create a processing job
        assert resp.status == "processing"


# ---------------------------------------------------------------------------
# get_storyline
# ---------------------------------------------------------------------------


class TestGetStorylineTenantScoping:
    """Cross-tenant isolation for the storyline GET path (RLS-bypass guard)."""

    @pytest.mark.asyncio
    async def test_cross_tenant_redis_cache_is_not_served(self) -> None:
        """Tenant B must NOT read tenant A's cached storyline body.

        The Redis key is tenant-namespaced, so a dict-backed fake redis returns a
        MISS for tenant B even though tenant A's body is present.
        """
        from fastapi import HTTPException

        from src.cache.cache_keys import job_status_key

        store: dict[str, object] = {
            job_status_key(str(TENANT_A), "shared-job"): {
                "status": "completed",
                "storyline": {"video_title": "TENANT-A-SECRET"},
            }
        }

        async def _get(key):
            return store.get(key)

        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=_get)
        redis.set = AsyncMock()

        # DB miss as well → tenant B sees a 404, never tenant A's body.
        db = _make_db_session()
        request = _make_request(redis_client=redis, tenant_id=TENANT_B_STR)

        with pytest.raises(HTTPException) as exc_info:
            await get_storyline("shared-job", request, db)
        assert exc_info.value.status_code == 404

        # And tenant A still gets its own cached body.
        request_a = _make_request(redis_client=redis, tenant_id=TENANT_A_STR)
        resp = await get_storyline("shared-job", request_a, _make_db_session())
        assert resp.status == "completed"
        assert resp.storyline is not None
        assert resp.storyline.video_title == "TENANT-A-SECRET"

    @pytest.mark.asyncio
    async def test_db_query_is_tenant_scoped(self) -> None:
        """The storyline DB SELECT filters on the caller's tenant_id."""
        from fastapi import HTTPException

        captured: list[tuple[str, dict]] = []
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        async def _execute(stmt):
            # Compile WITHOUT ``literal_binds``. The tenant arrives as the canonical
            # uuid STRING -- that is the production shape, because w2v-common's
            # AuthMiddleware assigns ``request.state.tenant_id = str(tenant_id)`` --
            # and SQLAlchemy's ``Uuid`` literal processor calls ``value.hex``, which a
            # ``str`` does not have. Inlining literals therefore crashes on exactly the
            # shape production uses. Reading the bound params instead is both
            # dialect-independent and strictly stronger: it proves the caller's tenant
            # is the value bound into the WHERE clause, not merely a substring of the
            # rendered SQL.
            compiled = stmt.compile()
            captured.append((str(compiled), dict(compiled.params)))
            return mock_result

        db = AsyncMock()
        db.execute = _execute
        request = _make_request(tenant_id=TENANT_C_STR)

        with pytest.raises(HTTPException):
            await get_storyline("any-job", request, db)

        assert captured
        assert any(
            "tenant_id" in sql and any(str(v) == TENANT_C_STR for v in params.values())
            for sql, params in captured
        ), f"the caller's tenant is not bound into any WHERE clause: {captured!r}"


class TestGetStoryline:
    @pytest.mark.asyncio
    async def test_get_storyline_redis_cache_hit(self) -> None:
        """get_storyline returns from Redis cache when available."""
        cached_data = {
            "status": "completed",
            "storyline": {
                "video_title": "Demo",
                "video_description": "Desc",
                "target_duration_minutes": 3.0,
                "tone": "professional",
                "intro": {},
                "chapters": [],
                "outro": {},
            },
        }
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached_data)

        db = _make_db_session()
        request = _make_request(redis_client=redis)

        resp = await get_storyline("job-123", request, db)

        assert resp.job_id == "job-123"
        assert resp.status == "completed"
        assert resp.storyline is not None
        assert resp.storyline.video_title == "Demo"

    @pytest.mark.asyncio
    async def test_get_storyline_redis_cache_hit_with_token_usage(self) -> None:
        """get_storyline extracts token_usage from cached storyline."""
        cached_data = {
            "status": "completed",
            "storyline": {
                "video_title": "Demo",
                "video_description": "",
                "target_duration_minutes": 3.0,
                "tone": "professional",
                "intro": {},
                "chapters": [],
                "outro": {},
                "token_usage": {"total_tokens": 100},
            },
        }
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached_data)

        db = _make_db_session()
        request = _make_request(redis_client=redis)

        resp = await get_storyline("job-tok", request, db)
        assert resp.token_usage == {"total_tokens": 100}

    @pytest.mark.asyncio
    async def test_get_storyline_from_db(self) -> None:
        """get_storyline falls back to DB when no Redis."""
        row = MagicMock()
        row.status = "completed"
        row.storyline = {
            "video_title": "DB Story",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "professional",
            "intro": {},
            "chapters": [],
            "outro": {},
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        resp = await get_storyline("db-job", request, db)

        assert resp.job_id == "db-job"
        assert resp.status == "completed"
        assert resp.storyline is not None
        assert resp.storyline.video_title == "DB Story"

    @pytest.mark.asyncio
    async def test_get_storyline_from_db_with_token_usage(self) -> None:
        """get_storyline extracts token_usage from DB row storyline."""
        row = MagicMock()
        row.status = "completed"
        row.storyline = {
            "video_title": "DB",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "pro",
            "intro": {},
            "chapters": [],
            "outro": {},
            "token_usage": {"total_tokens": 50},
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        request = _make_request(redis_client=redis)

        resp = await get_storyline("db-tok-job", request, db)
        assert resp.token_usage == {"total_tokens": 50}

    @pytest.mark.asyncio
    async def test_get_storyline_not_found(self) -> None:
        """get_storyline raises 404 when job not in DB."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_storyline("missing-job", request, db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_storyline_processing_no_storyline(self) -> None:
        """get_storyline returns status with no storyline when still processing."""
        row = MagicMock()
        row.status = "processing"
        row.storyline = None
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        resp = await get_storyline("proc-job", request, db)
        assert resp.status == "processing"
        assert resp.storyline is None

    @pytest.mark.asyncio
    async def test_get_storyline_redis_read_fails(self) -> None:
        """get_storyline is fail-open when Redis read fails."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        row = MagicMock()
        row.status = "completed"
        row.storyline = {
            "video_title": "Fallback",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "professional",
            "intro": {},
            "chapters": [],
            "outro": {},
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request(redis_client=redis)

        resp = await get_storyline("redis-fail", request, db)
        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_get_storyline_redis_writeback_fails(self) -> None:
        """get_storyline is fail-open when Redis writeback fails."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        row = MagicMock()
        row.status = "completed"
        row.storyline = {
            "video_title": "WB",
            "video_description": "",
            "target_duration_minutes": 3.0,
            "tone": "professional",
            "intro": {},
            "chapters": [],
            "outro": {},
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request(redis_client=redis)

        # Should not raise
        resp = await get_storyline("wb-fail", request, db)
        assert resp.status == "completed"


# ---------------------------------------------------------------------------
# build_shotlist
# ---------------------------------------------------------------------------


class TestBuildShotlist:
    @pytest.mark.asyncio
    async def test_build_shotlist_no_cache(self) -> None:
        """build_shotlist calls builder and returns result."""
        builder = MagicMock()
        builder.build.return_value = {
            "parts": [],
            "recording_order": [],
            "total_parts": 0,
            "estimated_total_duration_seconds": 0.0,
        }
        db = _make_db_session()
        request = _make_request(shotlist_builder=builder)

        body = ShotListRequest(
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=False):
            resp = await build_shotlist(body, request, db)

        assert resp.total_parts == 0
        builder.build.assert_called_once()

    @pytest.mark.asyncio
    async def test_build_shotlist_malformed_storyline_returns_422(self) -> None:
        """A malformed free-form storyline makes the builder raise → 422, not a 500."""
        from fastapi import HTTPException

        builder = MagicMock()
        builder.build.side_effect = ValueError("could not convert string to float: 'x'")
        db = _make_db_session()
        request = _make_request(shotlist_builder=builder)
        body = ShotListRequest(
            storyline={"chapters": [{"estimated_duration_seconds": "x"}]},
            analysis=_make_analysis(),
        )

        with (
            patch("src.api.narrative._shared._cache_enabled", return_value=False),
            pytest.raises(HTTPException) as exc,
        ):
            await build_shotlist(body, request, db)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_build_shotlist_cache_hit(self) -> None:
        """build_shotlist returns cached result."""
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value={
                "parts": [],
                "recording_order": [],
                "total_parts": 5,
                "estimated_total_duration_seconds": 60.0,
            }
        )
        request = _make_request(
            redis_client=redis,
            shotlist_builder=MagicMock(),
        )
        db = _make_db_session()
        body = ShotListRequest(
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await build_shotlist(body, request, db)

        assert resp.total_parts == 5

    @pytest.mark.asyncio
    async def test_build_shotlist_backfills_job(self) -> None:
        """build_shotlist backfills shot_list on StorylineJob when job_id provided."""
        builder = MagicMock()
        result_dict = {
            "parts": [],
            "recording_order": [],
            "total_parts": 2,
            "estimated_total_duration_seconds": 30.0,
        }
        builder.build.return_value = result_dict

        row = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request(shotlist_builder=builder)

        body = ShotListRequest(
            storyline={"chapters": []},
            analysis=_make_analysis(),
            job_id="storyline-job-1",
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=False):
            resp = await build_shotlist(body, request, db)

        assert resp.total_parts == 2
        assert row.shot_list == result_dict

    @pytest.mark.asyncio
    async def test_build_shotlist_cache_check_fails(self) -> None:
        """build_shotlist is fail-open when Redis cache check raises."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        builder = MagicMock()
        builder.build.return_value = {
            "parts": [],
            "recording_order": [],
            "total_parts": 1,
            "estimated_total_duration_seconds": 10.0,
        }
        db = _make_db_session()
        request = _make_request(redis_client=redis, shotlist_builder=builder)

        body = ShotListRequest(
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await build_shotlist(body, request, db)

        assert resp.total_parts == 1

    @pytest.mark.asyncio
    async def test_build_shotlist_cache_write_fails(self) -> None:
        """build_shotlist is fail-open when Redis cache write raises."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        builder = MagicMock()
        builder.build.return_value = {
            "parts": [],
            "recording_order": [],
            "total_parts": 1,
            "estimated_total_duration_seconds": 10.0,
        }
        db = _make_db_session()
        request = _make_request(redis_client=redis, shotlist_builder=builder)

        body = ShotListRequest(
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await build_shotlist(body, request, db)

        assert resp.total_parts == 1


# ---------------------------------------------------------------------------
# extract_narrative
# ---------------------------------------------------------------------------


class TestExtractNarrative:
    @pytest.mark.asyncio
    async def test_extract_narrative(self) -> None:
        """extract_narrative calls extractor and returns result."""
        extractor = MagicMock()
        extractor.extract.return_value = {
            "hooks": ["hook1"],
            "key_quotes": ["quote1"],
            "feature_highlights": ["highlight1"],
            "total_pages": 1,
            "pages_with_forms": 0,
            "pages_with_media": 0,
        }
        request = _make_request(narrative_extractor=extractor)

        body = NarrativeExtractRequest(pages=[PageInput(url="https://example.com/", title="Home")])

        resp = await extract_narrative(body, request)

        assert resp.hooks == ["hook1"]
        assert resp.key_quotes == ["quote1"]
        assert resp.feature_highlights == ["highlight1"]
        extractor.extract.assert_called_once()


# ---------------------------------------------------------------------------
# plan_storyline
# ---------------------------------------------------------------------------


class TestPlanStoryline:
    @pytest.mark.asyncio
    async def test_plan_storyline_no_warnings(self) -> None:
        """plan_storyline returns empty warnings for valid storyline."""
        request = _make_request()

        body = ChapterPlanRequest(
            storyline={
                "chapters": [
                    {"number": 1, "title": "Ch1", "estimated_duration_seconds": 60},
                ],
                "target_duration_minutes": 3.0,
            },
            target_duration_minutes=3.0,
        )

        resp = await plan_storyline(body, request)

        assert isinstance(resp.warnings, list)
        assert isinstance(resp.storyline, dict)

    @pytest.mark.asyncio
    async def test_plan_storyline_with_warnings(self) -> None:
        """plan_storyline returns warnings for overlong storyline."""
        request = _make_request()

        body = ChapterPlanRequest(
            storyline={
                "chapters": [
                    {"number": 1, "title": "Ch1", "estimated_duration_seconds": 600},
                ],
                "target_duration_minutes": 1.0,
            },
            target_duration_minutes=1.0,
            duration_overage_threshold=1.1,
        )

        resp = await plan_storyline(body, request)
        # An overlong chapter should produce warnings
        assert isinstance(resp.warnings, list)


# ---------------------------------------------------------------------------
# start_narration
# ---------------------------------------------------------------------------


class TestStartNarration:
    @pytest.mark.asyncio
    async def test_start_narration_no_cache(self) -> None:
        """start_narration creates a processing job when cache misses."""
        builder = AsyncMock()
        db = _make_db_session()
        request = _make_request(narration_builder=builder)

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=False):
            resp = await start_narration(body, request, db)

        assert resp.status == "processing"
        assert resp.job_id

    @pytest.mark.asyncio
    async def test_start_narration_cache_hit(self) -> None:
        """start_narration returns completed when cache hits."""
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value={
                "parts": [],
                "total_words": 0,
                "tone": "professional",
            }
        )
        builder = AsyncMock()
        db = _make_db_session()
        request = _make_request(redis_client=redis, narration_builder=builder)

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await start_narration(body, request, db)

        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_start_narration_redis_check_fails(self) -> None:
        """start_narration is fail-open when redis cache check raises."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))
        builder = AsyncMock()
        db = _make_db_session()
        request = _make_request(redis_client=redis, narration_builder=builder)

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative._shared._cache_enabled", return_value=True):
            resp = await start_narration(body, request, db)

        assert resp.status == "processing"


# ---------------------------------------------------------------------------
# get_narration
# ---------------------------------------------------------------------------


class TestGetNarration:
    @pytest.mark.asyncio
    async def test_get_narration_redis_cache_hit(self) -> None:
        """get_narration returns from Redis cache."""
        cached = {
            "status": "completed",
            "narration": {
                "parts": [
                    {
                        "part_id": "intro",
                        "text": "Hello",
                        "estimated_duration_seconds": 1.0,
                        "word_count": 1,
                    }
                ],
                "total_words": 1,
                "estimated_total_duration_seconds": 1.0,
                "tone": "professional",
            },
        }
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached)
        db = _make_db_session()
        request = _make_request(redis_client=redis)

        resp = await get_narration("narr-job", request, db)
        assert resp.status == "completed"
        assert resp.narration is not None
        assert resp.narration.total_words == 1

    @pytest.mark.asyncio
    async def test_get_narration_redis_cache_hit_with_token_usage(self) -> None:
        """get_narration extracts token_usage from cached narration."""
        cached = {
            "status": "completed",
            "narration": {
                "parts": [],
                "total_words": 0,
                "estimated_total_duration_seconds": 0,
                "tone": "professional",
                "token_usage": {"total_tokens": 42},
            },
        }
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=cached)
        db = _make_db_session()
        request = _make_request(redis_client=redis)

        resp = await get_narration("narr-tok", request, db)
        assert resp.token_usage == {"total_tokens": 42}

    @pytest.mark.asyncio
    async def test_get_narration_from_db(self) -> None:
        """get_narration falls back to DB when no Redis."""
        row = MagicMock()
        row.status = "completed"
        row.narration = {
            "parts": [
                {
                    "part_id": "ch01",
                    "text": "Text",
                    "estimated_duration_seconds": 2.0,
                    "word_count": 1,
                }
            ],
            "total_words": 1,
            "estimated_total_duration_seconds": 2.0,
            "tone": "professional",
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        resp = await get_narration("db-narr", request, db)
        assert resp.status == "completed"
        assert resp.narration is not None

    @pytest.mark.asyncio
    async def test_get_narration_not_found(self) -> None:
        """get_narration raises 404 when job not in DB."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await get_narration("missing-narr", request, db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_narration_processing_no_narration(self) -> None:
        """get_narration returns processing status with no narration data."""
        row = MagicMock()
        row.status = "processing"
        row.narration = None
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request()

        resp = await get_narration("proc-narr", request, db)
        assert resp.status == "processing"
        assert resp.narration is None

    @pytest.mark.asyncio
    async def test_get_narration_redis_read_fails(self) -> None:
        """get_narration is fail-open when Redis read fails."""
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("Redis down"))

        row = MagicMock()
        row.status = "completed"
        row.narration = {
            "parts": [],
            "total_words": 0,
            "estimated_total_duration_seconds": 0,
            "tone": "professional",
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request(redis_client=redis)

        resp = await get_narration("rf-narr", request, db)
        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_get_narration_redis_writeback_fails(self) -> None:
        """get_narration is fail-open when Redis writeback fails."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock(side_effect=ConnectionError("Redis down"))

        row = MagicMock()
        row.status = "completed"
        row.narration = {
            "parts": [],
            "total_words": 0,
            "estimated_total_duration_seconds": 0,
            "tone": "professional",
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)
        request = _make_request(redis_client=redis)

        resp = await get_narration("wb-narr", request, db)
        assert resp.status == "completed"

    @pytest.mark.asyncio
    async def test_get_narration_from_db_with_token_usage(self) -> None:
        """get_narration extracts token_usage from DB narration data."""
        row = MagicMock()
        row.status = "completed"
        row.narration = {
            "parts": [],
            "total_words": 0,
            "estimated_total_duration_seconds": 0,
            "tone": "professional",
            "token_usage": {"total_tokens": 77},
        }
        row.error = None
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = row

        db = AsyncMock()
        db.execute = AsyncMock(return_value=mock_result)

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        request = _make_request(redis_client=redis)

        resp = await get_narration("db-narr-tok", request, db)
        assert resp.token_usage == {"total_tokens": 77}


# ---------------------------------------------------------------------------
# start_narration_shotlist
# ---------------------------------------------------------------------------


class TestStartNarrationShotlist:
    @pytest.mark.asyncio
    async def test_start_narration_shotlist(self) -> None:
        """start_narration_shotlist creates a processing job."""
        builder = AsyncMock()
        db = _make_db_session()
        request = _make_request(narration_builder=builder)

        body = NarrationForShotListRequest(
            shot_list={"parts": [{"id": "ch01"}], "recording_order": ["ch01"]},
            storyline={"chapters": [], "tone": "professional"},
        )

        resp = await start_narration_shotlist(body, request, db)

        assert resp.status == "processing"
        assert resp.job_id
        db.add.assert_called_once()
        db.flush.assert_awaited()
        db.commit.assert_awaited()


# ---------------------------------------------------------------------------
# enhance_shotlist
# ---------------------------------------------------------------------------


class TestEnhanceShotlist:
    @pytest.mark.asyncio
    async def test_enhance_shotlist_disabled(self) -> None:
        """enhance_shotlist raises 501 when AI creative is disabled."""
        mock_settings = MagicMock()
        mock_settings.shotlist.ai_creative = False
        request = _make_request()

        body = ShotListEnhanceRequest(
            shot_list={"parts": []},
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        from fastapi import HTTPException

        with (
            patch("src.api.narrative.shotlist.get_settings", return_value=mock_settings),
            pytest.raises(HTTPException) as exc_info,
        ):
            await enhance_shotlist(body, request)

        assert exc_info.value.status_code == 501

    @pytest.mark.asyncio
    async def test_enhance_shotlist_success(self) -> None:
        """enhance_shotlist calls enhancer and returns result."""
        enhancer = AsyncMock()
        enhancer.enhance = AsyncMock(return_value={"parts": [{"id": "enhanced"}]})

        mock_settings = MagicMock()
        mock_settings.shotlist.ai_creative = True

        request = _make_request(shotlist_enhancer=enhancer)

        body = ShotListEnhanceRequest(
            shot_list={"parts": []},
            storyline={"chapters": []},
            analysis=_make_analysis(),
        )

        with patch("src.api.narrative.shotlist.get_settings", return_value=mock_settings):
            resp = await enhance_shotlist(body, request)

        assert resp.shot_list == {"parts": [{"id": "enhanced"}]}
        enhancer.enhance.assert_awaited_once()


# ---------------------------------------------------------------------------
# storyline_from_findings
# ---------------------------------------------------------------------------


class TestStorylineFromFindings:
    @pytest.mark.asyncio
    async def test_critique_to_storyline(self) -> None:
        """storyline_from_findings with critique type converts findings."""
        body = FindingsToStorylineRequest(
            findings=[CritiqueFinding(category="seo", severity="high", title="Thin titles")],
            scores={"seo": 0.8},
            analysis={"pages": []},
            target_minutes=3.0,
            conversion_type="critique",
        )

        resp = await storyline_from_findings(body)
        assert resp.video_title  # should produce some title
        assert resp.chapters  # a real conversion always yields >= 1 chapter

    @pytest.mark.asyncio
    async def test_empty_findings_returns_422_not_a_zero_chapter_200(self) -> None:
        """`findings: []` must be a 422, not a 200 carrying intro + outro + no chapters.

        #119's zero-chapter guard existed only on the LLM parse path; this route
        shipped the empty storyline as success, and downstream a zero-chapter
        storyline becomes an intro+outro-only ~11-second video reported
        `completed`.
        """
        from fastapi import HTTPException

        body = FindingsToStorylineRequest(
            findings=[],
            scores={},
            analysis={"pages": []},
            target_minutes=3.0,
            conversion_type="critique",
        )

        with pytest.raises(HTTPException) as exc_info:
            await storyline_from_findings(body)
        assert exc_info.value.status_code == 422
        assert "zero-chapter storyline" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_empty_reviews_returns_422_not_a_zero_chapter_200(self) -> None:
        """Same guard down the `reviews` branch of the same route."""
        from fastapi import HTTPException

        body = FindingsToStorylineRequest(
            findings=[],
            # `reviews` is typed `dict[str, dict]`, so the prod failure shape that
            # reaches here is an EMPTY review dict, not a string error blob.
            reviews={"design_review": {}},
            analysis={"pages": []},
            target_minutes=2.0,
            conversion_type="reviews",
        )

        with pytest.raises(HTTPException) as exc_info:
            await storyline_from_findings(body)
        assert exc_info.value.status_code == 422
        assert "zero-chapter storyline" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_reviews_to_storyline(self) -> None:
        """storyline_from_findings with reviews type converts reviews."""
        body = FindingsToStorylineRequest(
            findings=[],
            reviews={"app-store": {"rating": 4.5, "summary": "Great app"}},
            analysis={"pages": []},
            target_minutes=2.0,
            conversion_type="reviews",
        )

        resp = await storyline_from_findings(body)
        assert resp.video_title
        assert isinstance(resp.chapters, list)

    @pytest.mark.asyncio
    async def test_storyline_from_findings_malformed_analysis_returns_422(self) -> None:
        """A malformed free-form analysis makes the converter raise → 422, not a raw 500.

        `analysis` is an unvalidated dict; the sync converter does
        `analysis["description"].strip()` deep inside, so a non-string value
        raises AttributeError. That is a BAD REQUEST (422), not a 500.
        """
        from fastapi import HTTPException

        body = FindingsToStorylineRequest(
            findings=[],
            scores={"seo": 0.8},
            analysis={"description": 123},  # .strip() on int → AttributeError
            target_minutes=3.0,
            conversion_type="critique",
        )

        with pytest.raises(HTTPException) as exc:
            await storyline_from_findings(body)
        assert exc.value.status_code == 422


# ---------------------------------------------------------------------------
# shotlist_from_findings
# ---------------------------------------------------------------------------


class TestShotlistFromFindings:
    @pytest.mark.asyncio
    async def test_critique_to_shotlist(self) -> None:
        """shotlist_from_findings with critique type."""
        body = FindingsToShotListRequest(
            storyline={
                "video_title": "Test",
                "chapters": [{"number": 1, "title": "Ch1", "pages": ["https://example.com/"]}],
                "intro": {"title": "Intro"},
                "outro": {"title": "Outro"},
            },
            findings=[],
            scores={"seo": 0.8},
            conversion_type="critique",
        )

        resp = await shotlist_from_findings(body)
        assert isinstance(resp.parts, list)

    @pytest.mark.asyncio
    async def test_reviews_to_shotlist(self) -> None:
        """shotlist_from_findings with reviews type."""
        body = FindingsToShotListRequest(
            storyline={
                "video_title": "Test",
                "chapters": [{"number": 1, "title": "Ch1", "pages": ["https://example.com/"]}],
                "intro": {"title": "Intro"},
                "outro": {"title": "Outro"},
            },
            findings=[],
            reviews={"app-store": {"rating": 4.5, "summary": "Great app"}},
            conversion_type="reviews",
        )

        resp = await shotlist_from_findings(body)
        assert isinstance(resp.parts, list)

    @pytest.mark.asyncio
    async def test_shotlist_from_findings_malformed_storyline_returns_422(self) -> None:
        """A malformed free-form storyline makes the converter raise → 422, not a raw 500.

        `storyline` is an unvalidated dict; the sync converter formats
        `f"ch{chapter['number']:02d}..."` deep inside, so a string `number`
        raises ValueError. That is a BAD REQUEST (422), not a 500.
        """
        from fastapi import HTTPException

        body = FindingsToShotListRequest(
            storyline={
                "chapters": [{"number": "1", "title": "X"}]
            },  # 'd' format on str → ValueError
            findings=[],
            scores={"seo": 0.8},
            conversion_type="critique",
        )

        with pytest.raises(HTTPException) as exc:
            await shotlist_from_findings(body)
        assert exc.value.status_code == 422


@pytest.mark.parametrize(
    "bad_storyline",
    [
        {"chapters": [{"estimated_duration_seconds": "soon"}]},  # float("soon") -> ValueError
        {"chapters": {"ch1": {}}},  # non-empty dict → iterated as str keys → str.get AttributeError
        {"chapters": [{}, "not-a-dict"]},  # a non-dict chapter row
    ],
)
async def test_plan_storyline_malformed_storyline_returns_422(bad_storyline: dict) -> None:
    """A malformed free-form storyline must surface as 422 (bad input), not a raw 500."""
    from fastapi import HTTPException

    from src.api.narrative_schemas import ChapterPlanRequest

    body = ChapterPlanRequest(storyline=bad_storyline)
    with pytest.raises(HTTPException) as exc:
        await plan_storyline(body, _make_request())
    assert exc.value.status_code == 422
