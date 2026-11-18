"""Shared helpers for the narrative route sub-modules.

Request-state accessors, the cache-enable check, tenant-namespaced cache-key
builders, the background-task registry + spawn helper, and the ``TaskContext``
carrier. ``_cache_enabled`` is called through this module (``_shared._cache_enabled()``)
by the handlers so a single patch target controls all of them in tests.
"""

from __future__ import annotations

import asyncio
import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException, Request
from w2v_common.tenant import TenantError

from src.api.narrative_schemas import NarrationRequest, StorylineRequest
from src.cache.cache_keys import narration_cache_key, storyline_cache_key
from src.config import get_settings
from src.tenant import require_tenant

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from src.services.narrative.shotlist_builder import ShotListBuilder
    from src.services.narrative.storyline_generator import StorylineGenerator

__all__ = [
    "TaskContext",
    "_background_tasks",
    "_cache_enabled",
    "_get_license_token",
    "_get_redis",
    "_get_shotlist_builder",
    "_get_storyline_gen",
    "_get_tenant_id",
    "_narration_cache_key_for",
    "_storyline_cache_key_for",
    "spawn_background_task",
]

_background_tasks: set[asyncio.Task] = set()  # prevent GC of fire-and-forget tasks


def spawn_background_task(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """Schedule ``coro`` as a fire-and-forget task under the current context.

    Snapshots the request-scoped Context so the identity + cost-correlation
    ContextVars (tenant_id / license_id / subject_id / correlation_id / stage,
    incl. the X-Effective-Tenant-Id the AuthMiddleware resolved) survive into the
    background task — these drive the run-tenant attribution + BYO resolution of
    the WM/TTS calls the task makes. AuthMiddleware's ``clear_identity_context()``
    must not race them. NOTE: a plain ``asyncio.create_task(coro)`` already snapshots
    the current context, so this ``context=ctx`` is belt-and-braces, not the load-
    bearing bit — but DO keep an explicit copy and do NOT move this work to a
    thread/process pool or ``run_in_executor``, which would NOT inherit the context
    and would silently NULL correlation + drop the tenant on the cost event.
    """
    ctx = contextvars.copy_context()
    task = asyncio.create_task(coro, context=ctx)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


def _get_storyline_gen(request: Request) -> StorylineGenerator:
    """Retrieve the StorylineGenerator instance from app state."""
    return request.app.state.storyline_generator


def _get_shotlist_builder(request: Request) -> ShotListBuilder:
    """Retrieve the ShotListBuilder instance from app state."""
    return request.app.state.shotlist_builder


def _get_redis(request: Request) -> Any:
    """Get Redis client from app state, or None."""
    return getattr(request.app.state, "redis_client", None)


def _get_tenant_id(request: Request) -> str:
    """Return the request's tenant as a canonical UUID string, or 401.

    Narrative twin of ``src.api.dependencies.get_tenant_id``. This used to return
    the literal ``"anonymous"`` sentinel, which namespaced cache/redis keys and
    (post-0.67.0) could never reach the ``uuid`` ``tenant_id`` columns. There is
    no default tenant: an unattributed request is refused at the boundary instead
    of being pooled into a shared pseudo-tenant.
    """
    try:
        return str(require_tenant(getattr(request.state, "tenant_id", None), source="request"))
    except TenantError as exc:
        raise HTTPException(status_code=401, detail="request has no valid tenant") from exc


def _get_license_token(request: Request) -> str | None:
    """Return the request-scoped licence token, whichever header carried it.

    Prefer ``request.state.license_token``: ``AuthMiddleware`` stashes the
    token it actually authenticated, so this helper never has to re-derive
    which credential shape the caller used. Since **w2v_common 0.74.0** there
    are TWO accepted shapes — the legacy ``X-Tenant-Id: Bearer <token>`` and
    the platform's ``Authorization: Bearer <opaque>`` (the only header the
    ``license-authz`` ext-authz gate reads) — so the old header-only parse
    below silently returned ``None`` for every gate-fronted caller and dropped
    peer-service calls onto the trusted-network fallback.

    Falls back to the legacy header for requests the licence lane did not
    authenticate (e.g. trusted-network requests in development). The JWT lane
    is deliberately NOT a fallback: a Keycloak JWT is never a licence token.
    """
    stashed = getattr(request.state, "license_token", None)
    if isinstance(stashed, str) and stashed.strip():
        return stashed.strip()
    x_tenant_id = request.headers.get("X-Tenant-Id")
    if x_tenant_id and x_tenant_id.lower().startswith("bearer "):
        return x_tenant_id[7:].strip() or None
    return None


def _cache_enabled() -> bool:
    """Return True if result caching is enabled."""
    return get_settings().cache.enabled


def _storyline_cache_key_for(body: StorylineRequest, tenant_id: str) -> str:
    """Build the tenant-namespaced storyline cache key from the request body.

    Centralised so the submit-time check and the background-task write derive the
    identical key from the same output-determining inputs (tenant, analysis,
    duration, message, mode, reviews, user_context, and the resolved LLM model —
    ``llm_config.model`` selects which model the WM call runs, so it must key).
    """
    return storyline_cache_key(
        tenant_id=tenant_id,
        analysis=body.analysis.model_dump(),
        duration=body.duration_minutes,
        message=body.message,
        mode=body.mode,
        reviews=({k: v.model_dump() for k, v in body.reviews.items()} if body.reviews else None),
        user_context=body.user_context.model_dump() if body.user_context else None,
        model=body.llm_config.model if body.llm_config else None,
    )


def _narration_cache_key_for(body: NarrationRequest, tenant_id: str) -> str:
    """Build the tenant-namespaced narration cache key from the request body.

    ``synthesize_audio`` is part of the key so an audio result is never served to
    a text-only request (or vice versa). ``llm_config.model`` is included too — it
    selects which LLM the narration WM call runs, so it is output-determining.
    """
    return narration_cache_key(
        tenant_id=tenant_id,
        storyline=body.storyline,
        analysis=body.analysis.model_dump(),
        mode=body.mode,
        message=body.message,
        synthesize_audio=body.synthesize_audio,
        user_context=body.user_context.model_dump() if body.user_context else None,
        code_quality_facts=(
            body.code_quality_facts.model_dump() if body.code_quality_facts else None
        ),
        model=body.llm_config.model if body.llm_config else None,
    )


@dataclass
class TaskContext:
    """Context propagated to background tasks for structured logging."""

    job_id: str
    tenant_id: str
    request_id: str = ""
