"""A database fault during the shot_list backfill must not become a 500.

``POST /api/narrative/shotlist`` builds the shot list first and only then tries
to backfill it onto the ``StorylineJob`` row — a best-effort side-effect that is
skipped entirely when no ``job_id`` is supplied. The guard around it caught only
builtins, and ``sqlalchemy.exc.OperationalError`` (what a psycopg/pgbouncer blip
surfaces as) inherits from none of them, so the guard was dead: a DB blip threw
away a shot list that had already been built and answered 500.

The test drives the real route through ASGI and asserts the STATUS CODE the
caller sees, not that some handler swallowed something.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.exc import OperationalError

from src.api.narrative import shotlist as shotlist_routes
from src.database.session import get_db
from tests.conftest import TENANT_A_STR

_SHOT_LIST = {
    "parts": [],
    "recording_order": [],
    "total_parts": 0,
    "estimated_total_duration_seconds": 0.0,
}


def _analysis_payload() -> dict:
    return {
        "pages": [
            {
                "page_id": "p1",
                "url": "https://example.com/",
                "title": "Home",
                "page_type": "homepage",
            }
        ]
    }


async def _post_shotlist(db) -> httpx.Response:
    app = FastAPI()
    app.include_router(shotlist_routes.router, prefix="/api/narrative")
    app.state.redis_client = None
    builder = MagicMock()
    builder.build.return_value = _SHOT_LIST
    app.state.shotlist_builder = builder
    app.dependency_overrides[get_db] = lambda: db

    async def _bind_tenant(scope, receive, send):
        scope.setdefault("state", {})["tenant_id"] = TENANT_A_STR
        await app(scope, receive, send)

    transport = httpx.ASGITransport(app=_bind_tenant, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/narrative/shotlist",
            json={
                "storyline": {"chapters": []},
                "analysis": _analysis_payload(),
                "job_id": "some-job",
            },
        )


@pytest.mark.asyncio
async def test_sqlalchemy_error_during_backfill_still_returns_the_shot_list() -> None:
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=OperationalError("SELECT storyline_jobs", {}, Exception("connection lost"))
    )

    resp = await _post_shotlist(db)

    assert resp.status_code == 200, (
        f"a DB blip during a BEST-EFFORT backfill destroyed an already-built shot list: {resp.text}"
    )
    assert resp.json()["total_parts"] == 0
    # The aborted transaction is rolled back, so get_db's teardown commit cannot
    # blow up after the 200 has already gone out.
    db.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_succeeds_when_the_row_exists() -> None:
    """Control: the happy path still writes the shot list onto the job row."""
    row = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)

    resp = await _post_shotlist(db)

    assert resp.status_code == 200
    assert row.shot_list == _SHOT_LIST
    db.rollback.assert_not_awaited()
