"""Unit tests for narrative_routes.py background task functions.

Covers: _process_storyline (success + failure), _process_narration (success + failure),
_process_narration_shotlist (success + failure), and various helper functions.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

# NOTE: Integration tests delete and re-import all src.* modules
# (via `del sys.modules[...]`), which creates a split between
# the module objects from which these functions were imported and
# the module objects that now live in sys.modules. Patching
# "src.api.narrative_routes.get_db_session" only affects the NEW module,
# while the imported functions still use the ORIGINAL module's
# globals. To reliably intercept get_db_session, we patch the
# function's __globals__ dict directly via patch.dict.
import pytest
from fastapi import HTTPException

from src.api.narrative_routes import (
    _get_redis,
    _get_shotlist_builder,
    _get_storyline_gen,
    _get_tenant_id,
    _process_narration,
    _process_narration_shotlist,
    _process_storyline,
)
from src.api.narrative_schemas import AnalysisInput, NarrationForShotListRequest, NarrationRequest
from tests.conftest import TENANT_A_STR, TENANT_C_STR

# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    def test_get_redis_returns_none_when_not_set(self) -> None:
        """_get_redis returns None when redis_client is not on app state."""
        mock_request = MagicMock()
        del mock_request.app.state.redis_client
        result = _get_redis(mock_request)
        assert result is None

    def test_get_redis_returns_client(self) -> None:
        """_get_redis returns the redis client from app state."""
        mock_request = MagicMock()
        mock_redis = AsyncMock()
        mock_request.app.state.redis_client = mock_redis
        result = _get_redis(mock_request)
        assert result is mock_redis

    def test_get_tenant_id_refuses_when_absent(self) -> None:
        """No tenant on request state is a 401 — never a substituted default.

        This test used to assert ``result == "anonymous"``. That sentinel is gone
        (w2v-common 0.67.0 deleted ``AuthSettings.default_tenant_id`` and the nil-UUID
        substitution behind it), and the assertion is inverted rather than deleted:
        the behaviour it pinned was the BUG. An unattributed request pooled every
        anonymous caller into one shared pseudo-tenant whose rows are mutually
        visible; refusing at the boundary is the fix, so that refusal is what the
        suite must now hold in place.
        """
        mock_request = MagicMock(spec=[])
        mock_request.state = MagicMock(spec=[])
        with pytest.raises(HTTPException) as exc_info:
            _get_tenant_id(mock_request)
        assert exc_info.value.status_code == 401

    @pytest.mark.parametrize(
        "nil_spelling",
        [
            "00000000-0000-0000-0000-000000000000",
            "00000000000000000000000000000000",
            "{00000000-0000-0000-0000-000000000000}",
            "urn:uuid:00000000-0000-0000-0000-000000000000",
        ],
    )
    def test_get_tenant_id_refuses_every_nil_spelling(self, nil_spelling: str) -> None:
        """The nil UUID is refused by VALUE, in all four spellings ``uuid.UUID`` accepts.

        ``uuid.UUID`` normalises the dashed, undashed, braced and ``urn:uuid:`` forms to
        the SAME nil value, and w2v-common's retired trusted-network path wrote exactly
        that value as its ``default_tenant_id``. A guard that only rejected ``None``, or
        that string-matched the dashed spelling, would leave three doors open — so this
        parametrisation is the mutation-proof for ``require_tenant``'s ``parsed.int == 0``
        check rather than a restatement of it.
        """
        mock_request = MagicMock()
        mock_request.state.tenant_id = nil_spelling
        with pytest.raises(HTTPException) as exc_info:
            _get_tenant_id(mock_request)
        assert exc_info.value.status_code == 401

    def test_get_tenant_id_refuses_legacy_slug(self) -> None:
        """A non-UUID slug tenant is refused, not coerced into a ``uuid`` column."""
        mock_request = MagicMock()
        mock_request.state.tenant_id = "anonymous"
        with pytest.raises(HTTPException) as exc_info:
            _get_tenant_id(mock_request)
        assert exc_info.value.status_code == 401

    def test_get_tenant_id_returns_from_state(self) -> None:
        """A real registered tenant passes through in its canonical string spelling."""
        mock_request = MagicMock()
        mock_request.state.tenant_id = TENANT_A_STR
        result = _get_tenant_id(mock_request)
        assert result == TENANT_A_STR

    def test_get_storyline_gen(self) -> None:
        """_get_storyline_gen returns storyline_generator from app state."""
        mock_request = MagicMock()
        mock_gen = MagicMock()
        mock_request.app.state.storyline_generator = mock_gen
        result = _get_storyline_gen(mock_request)
        assert result is mock_gen

    def test_get_shotlist_builder(self) -> None:
        """_get_shotlist_builder returns shotlist_builder from app state."""
        mock_request = MagicMock()
        mock_builder = MagicMock()
        mock_request.app.state.shotlist_builder = mock_builder
        result = _get_shotlist_builder(mock_request)
        assert result is mock_builder


# ---------------------------------------------------------------------------
# DB session mock helper
# ---------------------------------------------------------------------------


def _mock_redis():
    """Create a mock Redis client whose _ensure_connected returns a sync MagicMock.

    Any helper that calls redis_client._ensure_connected() does so
    synchronously.  If redis_client is an AsyncMock, the call produces an
    unawaited coroutine → RuntimeWarning.  Using MagicMock for the outer
    client and AsyncMock only for the inner async methods avoids this.

    The outer client also needs async set/get methods because _process_storyline
    and _process_narration call redis.set() / redis.get() directly.
    """
    inner = MagicMock()
    inner.set = AsyncMock()
    inner.eval = AsyncMock()
    client = MagicMock()
    client._ensure_connected = MagicMock(return_value=inner)
    # The route handlers call redis.set() and redis.get() directly on the client
    client.set = AsyncMock()
    client.get = AsyncMock(return_value=None)
    return client, inner


def _mock_db_row():
    """Create a mock DB row and session."""
    row = MagicMock()
    row.status = "processing"
    row.storyline = None
    row.narration = None
    row.error = None
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = row

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    session.execute = AsyncMock(return_value=mock_result)
    return session, row


def _make_db_session_mock(session):
    """Create an async context manager mock for get_db_session."""

    @asynccontextmanager
    async def fake_get_db_session():
        yield session

    return fake_get_db_session


# ---------------------------------------------------------------------------
# _process_storyline
# ---------------------------------------------------------------------------


class TestProcessStoryline:
    @pytest.mark.asyncio
    async def test_successful_processing(self) -> None:
        """_process_storyline updates DB row on success and writes to Redis cache."""
        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value={"video_title": "Demo", "chapters": []})

        mock_session, mock_row = _mock_db_row()

        mock_redis, _ = _mock_redis()
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        from src.api.narrative_schemas import StorylineRequest

        body = StorylineRequest(
            analysis=AnalysisInput(pages=[]),
            duration_minutes=3.0,
        )

        with patch.dict(
            _process_storyline.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_storyline(mock_gen, "test-job-id", body, mock_app, "tenant-a")

        mock_gen.generate.assert_awaited_once()
        assert mock_row.status == "completed"
        assert mock_row.storyline == {"video_title": "Demo", "chapters": []}

    @pytest.mark.asyncio
    async def test_failure_updates_db_status(self) -> None:
        """_process_storyline marks job as failed when generator raises."""
        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(side_effect=RuntimeError("Generation failed"))

        mock_session, mock_row = _mock_db_row()

        mock_app = MagicMock()
        mock_app.state.redis_client = None

        from src.api.narrative_schemas import StorylineRequest

        body = StorylineRequest(
            analysis=AnalysisInput(pages=[]),
            duration_minutes=3.0,
        )

        with patch.dict(
            _process_storyline.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_storyline(mock_gen, "fail-job", body, mock_app, "tenant-a")

        assert mock_row.status == "failed"
        assert "Generation failed" in mock_row.error

    @pytest.mark.asyncio
    async def test_db_update_query_is_tenant_scoped(self) -> None:
        """The completion UPDATE query filters on tenant_id (defence-in-depth on RLS)."""
        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value={"video_title": "Demo", "chapters": []})

        mock_session, _mock_row = _mock_db_row()
        captured: list[str] = []
        original_execute = mock_session.execute

        async def _capture(stmt):
            # Capture the SQL text AND the bound params. literal_binds is not an
            # option here: tenant_id is a native uuid column and the tenant
            # arrives as the uuid STRING the auth dependency yields, which no
            # dialect can render as a uuid literal. Text + params proves the same
            # thing -- tenant_id is in the WHERE clause and the caller's tenant
            # is the value bound to it.
            compiled = stmt.compile()
            captured.append(f"{compiled} | params={compiled.params!r}")
            return await original_execute(stmt)

        mock_session.execute = _capture

        mock_app = MagicMock()
        mock_app.state.redis_client = None

        from src.api.narrative_schemas import StorylineRequest

        body = StorylineRequest(analysis=AnalysisInput(pages=[]), duration_minutes=3.0)

        with patch.dict(
            _process_storyline.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_storyline(mock_gen, "scoped-job", body, mock_app, TENANT_C_STR)

        assert captured, "no SQL statement captured"
        assert any("tenant_id" in sql and TENANT_C_STR in sql for sql in captured)

    @pytest.mark.asyncio
    async def test_redis_cache_write_failure_is_failopen(self) -> None:
        """Redis cache write failure doesn't break the process."""
        mock_gen = AsyncMock()
        mock_gen.generate = AsyncMock(return_value={"video_title": "Demo", "chapters": []})

        mock_session, mock_row = _mock_db_row()

        mock_redis, mock_redis_inner = _mock_redis()
        mock_redis_inner.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        from src.api.narrative_schemas import StorylineRequest

        body = StorylineRequest(
            analysis=AnalysisInput(pages=[]),
            duration_minutes=3.0,
        )

        with patch.dict(
            _process_storyline.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_storyline(mock_gen, "redis-fail-job", body, mock_app, "tenant-a")

        assert mock_row.status == "completed"


# ---------------------------------------------------------------------------
# _process_narration
# ---------------------------------------------------------------------------


class TestProcessNarration:
    @pytest.mark.asyncio
    async def test_successful_narration(self) -> None:
        """_process_narration updates DB row on success and writes to Redis."""
        mock_builder = AsyncMock()
        mock_builder.generate = AsyncMock(
            return_value={
                "parts": [{"part_id": "intro", "text": "Hello", "word_count": 1}],
                "total_words": 1,
                "estimated_total_duration_seconds": 0.4,
                "tone": "professional",
            }
        )

        mock_session, mock_row = _mock_db_row()

        mock_redis, _ = _mock_redis()
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=AnalysisInput(pages=[]),
        )

        with patch.dict(
            _process_narration.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration(mock_builder, "narration-job", body, mock_app, "tenant-a")

        mock_builder.generate.assert_awaited_once()
        assert mock_row.status == "completed"
        assert mock_row.narration is not None

    @pytest.mark.asyncio
    async def test_narration_failure(self) -> None:
        """_process_narration marks job as failed when builder raises."""
        mock_builder = AsyncMock()
        mock_builder.generate = AsyncMock(side_effect=RuntimeError("Builder error"))

        mock_session, mock_row = _mock_db_row()
        mock_app = MagicMock()
        mock_app.state.redis_client = None

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=AnalysisInput(pages=[]),
        )

        with patch.dict(
            _process_narration.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration(mock_builder, "fail-narration", body, mock_app, "tenant-a")

        assert mock_row.status == "failed"
        assert "Builder error" in mock_row.error

    @pytest.mark.asyncio
    async def test_httpx_error_marks_job_failed_not_stuck(self) -> None:
        """A WM httpx.HTTPError must transition the job to 'failed', never leave it
        wedged in 'processing'. Previously the narrow except tuple let httpx escape."""
        mock_builder = AsyncMock()
        mock_builder.generate = AsyncMock(
            side_effect=httpx.ConnectError("worker_management unreachable")
        )

        mock_session, mock_row = _mock_db_row()
        mock_app = MagicMock()
        mock_app.state.redis_client = None

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=AnalysisInput(pages=[]),
        )

        with patch.dict(
            _process_narration.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration(mock_builder, "httpx-fail", body, mock_app, "tenant-a")

        assert mock_row.status == "failed"
        assert "unreachable" in mock_row.error

    @pytest.mark.asyncio
    async def test_narration_redis_failure_is_failopen(self) -> None:
        """Redis write failure after narration doesn't break the process."""
        mock_builder = AsyncMock()
        mock_builder.generate = AsyncMock(
            return_value={"parts": [], "total_words": 0, "tone": "professional"}
        )

        mock_session, mock_row = _mock_db_row()

        mock_redis, mock_redis_inner = _mock_redis()
        mock_redis_inner.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        body = NarrationRequest(
            storyline={"chapters": [], "tone": "professional"},
            analysis=AnalysisInput(pages=[]),
        )

        with patch.dict(
            _process_narration.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration(mock_builder, "redis-fail-narr", body, mock_app, "tenant-a")

        assert mock_row.status == "completed"


# ---------------------------------------------------------------------------
# _process_narration_shotlist
# ---------------------------------------------------------------------------


class TestProcessNarrationShotlist:
    @pytest.mark.asyncio
    async def test_successful_processing(self) -> None:
        """_process_narration_shotlist updates DB row on success."""
        mock_builder = AsyncMock()
        mock_builder.generate_for_shot_list = AsyncMock(
            return_value={
                "parts": [{"part_id": "ch01", "text": "Hello", "word_count": 1}],
                "total_words": 1,
                "tone": "professional",
            }
        )

        mock_session, mock_row = _mock_db_row()

        mock_redis, _ = _mock_redis()
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        body = NarrationForShotListRequest(
            shot_list={"parts": [], "recording_order": []},
            storyline={"chapters": [], "tone": "professional"},
        )

        with patch.dict(
            _process_narration_shotlist.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration_shotlist(mock_builder, "sl-job", body, mock_app, "tenant-a")

        mock_builder.generate_for_shot_list.assert_awaited_once()
        assert mock_row.status == "completed"

    @pytest.mark.asyncio
    async def test_failure_marks_job_failed(self) -> None:
        """_process_narration_shotlist marks job as failed on error."""
        mock_builder = AsyncMock()
        mock_builder.generate_for_shot_list = AsyncMock(
            side_effect=RuntimeError("Shot list narration error")
        )

        mock_session, mock_row = _mock_db_row()
        mock_app = MagicMock()
        mock_app.state.redis_client = None

        body = NarrationForShotListRequest(
            shot_list={"parts": [], "recording_order": []},
            storyline={"chapters": [], "tone": "professional"},
        )

        with patch.dict(
            _process_narration_shotlist.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration_shotlist(
                mock_builder, "fail-sl-job", body, mock_app, "tenant-a"
            )

        assert mock_row.status == "failed"
        assert "Shot list narration error" in mock_row.error

    @pytest.mark.asyncio
    async def test_redis_failure_is_failopen(self) -> None:
        """Redis failure after shotlist narration doesn't crash."""
        mock_builder = AsyncMock()
        mock_builder.generate_for_shot_list = AsyncMock(
            return_value={"parts": [], "total_words": 0, "tone": "professional"}
        )

        mock_session, mock_row = _mock_db_row()

        mock_redis, mock_redis_inner = _mock_redis()
        mock_redis_inner.set = AsyncMock(side_effect=ConnectionError("Redis down"))
        mock_app = MagicMock()
        mock_app.state.redis_client = mock_redis

        body = NarrationForShotListRequest(
            shot_list={"parts": [], "recording_order": []},
            storyline={"chapters": [], "tone": "professional"},
        )

        with patch.dict(
            _process_narration_shotlist.__globals__,
            {"get_db_session": _make_db_session_mock(mock_session)},
        ):
            await _process_narration_shotlist(
                mock_builder, "redis-fail-sl", body, mock_app, "tenant-a"
            )

        assert mock_row.status == "completed"
