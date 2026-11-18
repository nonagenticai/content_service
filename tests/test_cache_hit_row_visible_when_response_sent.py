"""The cache-hit create paths must COMMIT before the response leaves the app.

Both ``POST /api/narrative/storyline`` and ``POST /api/narrative/narration``
answer ``{"job_id": ..., "status": "completed"}`` straight out of the Redis
result cache. That answer is a promise the caller acts on immediately: the very
next ``GET /api/narrative/storyline/{job_id}`` must find the row.

``get_db`` (w2v_common) commits in its *teardown*, and FastAPI runs a
``yield``-dependency's teardown AFTER the response has been sent (see
``fastapi.routing.request_response``: ``await response(scope, receive, send)``
happens INSIDE the dependency ``AsyncExitStack``, which only unwinds afterwards).
So a handler that only ``flush()``es leaves a window in which the client holds a
job_id whose row is not committed — a concurrent GET, or any other pod, reads
nothing and 404s.

These tests assert the OBSERVABLE fact, not a call count: a **separate database
connection**, sampled at the exact instant the last response byte is sent, can
already see the row. Remove the ``await db.commit()`` from either cache-hit
branch and the probe comes back empty.

Needs a real postgres (the ``db_engine`` fixture).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from src.api.narrative import narration as narration_routes
from src.api.narrative import storyline as storyline_routes
from src.database.models import NarrationJob, StorylineJob
from tests.conftest import TENANT_A, TENANT_A_STR


class _AlwaysHitRedis:
    """Minimal redis stand-in whose result-cache lookup always hits."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def get(self, key: str) -> dict:
        return self._payload

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        return None


def _bind_tenant_and_observe(app, probe):
    """Wrap ``app``: bind the request tenant, and run ``probe()`` at send-time.

    Pure ASGI on BOTH counts, deliberately.

    * ``scope["state"]["tenant_id"]`` is what ``request.state.tenant_id`` reads,
      standing in for w2v_common's AuthMiddleware.
    * hooking ``send`` (rather than using ``@app.middleware("http")``) is what
      makes the sample meaningful: ``BaseHTTPMiddleware`` drains the inner
      response into a memory stream and only re-sends it after the route's
      ``AsyncExitStack`` has already unwound, so a probe behind one would
      always see a committed row and could never fail. Hooked here, the probe
      fires from inside ``await response(scope, receive, send)`` — the exact
      point the client has the answer and the ``get_db`` teardown has not run.
    """

    async def _wrapped(scope, receive, send):
        scope.setdefault("state", {})["tenant_id"] = TENANT_A_STR

        async def _send(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                await probe()

        await app(scope, receive, _send)

    return _wrapped


def _build_app(router_module, *, state: dict, redis_payload: dict) -> FastAPI:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/narrative")
    app.state.redis_client = _AlwaysHitRedis(redis_payload)
    for key, value in state.items():
        setattr(app.state, key, value)
    return app


async def _run_cache_hit(
    *,
    router_module,
    url: str,
    body: dict,
    state: dict,
    redis_payload: dict,
    db_session_factory,
    model,
    monkeypatch,
) -> tuple[dict, list]:
    """POST the cache-hit request; return (response json, rows seen at send-time)."""
    import w2v_common.database.session as sess_mod

    from src.api.narrative import _shared

    monkeypatch.setattr(sess_mod, "_async_session_maker", db_session_factory)
    monkeypatch.setattr(_shared, "_cache_enabled", lambda: True)

    seen: list = []

    async def probe() -> None:
        # A FRESH connection — exactly what the caller's next GET, or another
        # pod, would use. Uncommitted work is invisible to it.
        async with db_session_factory() as other:
            rows = (await other.execute(select(model))).scalars().all()
            seen.extend([(r.job_id, r.status) for r in rows])

    app = _build_app(router_module, state=state, redis_payload=redis_payload)
    transport = httpx.ASGITransport(app=_bind_tenant_and_observe(app, probe))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(url, json=body)
    assert resp.status_code == 200, resp.text
    return resp.json(), seen


@pytest.mark.asyncio
async def test_storyline_cache_hit_row_is_readable_when_the_response_is_sent(
    db_session_factory, sample_analysis, sample_storyline, monkeypatch
) -> None:
    payload, seen = await _run_cache_hit(
        router_module=storyline_routes,
        url="/api/narrative/storyline",
        body={"analysis": sample_analysis.model_dump(mode="json")},
        state={"storyline_generator": MagicMock()},
        redis_payload=sample_storyline,
        db_session_factory=db_session_factory,
        model=StorylineJob,
        monkeypatch=monkeypatch,
    )
    assert payload["status"] == "completed"
    assert seen == [(payload["job_id"], "completed")], (
        "the client holds a completed job_id that a second connection cannot see — "
        "the cache-hit branch flushed but never committed"
    )

    # And it is still there afterwards, owned by the right tenant.
    async with db_session_factory() as session:
        row = (
            await session.execute(
                select(StorylineJob).where(StorylineJob.job_id == payload["job_id"])
            )
        ).scalar_one()
        assert row.tenant_id == TENANT_A
        assert row.storyline == sample_storyline


@pytest.mark.asyncio
async def test_narration_cache_hit_row_is_readable_when_the_response_is_sent(
    db_session_factory, sample_analysis, sample_storyline, monkeypatch
) -> None:
    cached_narration = {"parts": [{"part_id": "p1", "narration_text": "hello"}]}
    payload, seen = await _run_cache_hit(
        router_module=narration_routes,
        url="/api/narrative/narration",
        body={
            "storyline": sample_storyline,
            "analysis": sample_analysis.model_dump(mode="json"),
        },
        state={"narration_builder": MagicMock()},
        redis_payload=cached_narration,
        db_session_factory=db_session_factory,
        model=NarrationJob,
        monkeypatch=monkeypatch,
    )
    assert payload["status"] == "completed"
    assert seen == [(payload["job_id"], "completed")], (
        "the client holds a completed job_id that a second connection cannot see — "
        "the cache-hit branch flushed but never committed"
    )

    async with db_session_factory() as session:
        row = (
            await session.execute(
                select(NarrationJob).where(NarrationJob.job_id == payload["job_id"])
            )
        ).scalar_one()
        assert row.tenant_id == TENANT_A
        assert row.narration == cached_narration
