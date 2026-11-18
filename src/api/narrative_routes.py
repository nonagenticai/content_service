"""API route handlers for content_service narrative domain endpoints.

This module was split into the ``src/api/narrative/`` package (storyline /
shotlist / narration / extract + shared helpers) for readability. It is kept as
a thin facade: it aggregates the four sub-routers into one ``router`` (prefix
``/api``, tag ``narrative``) and re-exports the public symbols so existing imports —
``from src.api.narrative_routes import router | _background_tasks | TaskContext |
_cache_enabled | start_storyline | _process_storyline | ...`` — keep working.
"""

from __future__ import annotations

from fastapi import APIRouter
from w2v_common.auth import LICENSE_GATE

from src.api.narrative import extract, narration, shotlist, storyline
from src.api.narrative._shared import (
    TaskContext,
    _background_tasks,
    _cache_enabled,
    _get_license_token,
    _get_redis,
    _get_shotlist_builder,
    _get_storyline_gen,
    _get_tenant_id,
    _narration_cache_key_for,
    _storyline_cache_key_for,
    spawn_background_task,
)
from src.api.narrative.extract import extract_narrative
from src.api.narrative.narration import (
    _process_narration,
    _process_narration_shotlist,
    get_narration,
    start_narration,
    start_narration_shotlist,
)
from src.api.narrative.shotlist import (
    build_shotlist,
    enhance_shotlist,
    shotlist_from_findings,
)
from src.api.narrative.storyline import (
    _process_storyline,
    get_storyline,
    plan_storyline,
    start_storyline,
    storyline_from_findings,
)

#
# ENTITLEMENT GATE (w2v_common.auth.entitlement, >=0.75.0), mounted router-wide.
# Until now this service authenticated the licence token for tenant IDENTITY
# (AuthMiddleware answers "who are you") and never asked "may you" — a tenant
# whose licence is expired or revoked still carries a syntactically valid token
# and was served. LICENSE_GATE closes that: none/expired/revoked -> 402
# license_<state>, an unestablishable verdict -> 503 license_unavailable +
# Retry-After, active/expiring_soon -> pass. Sub-routers included below
# (storyline/shotlist/narration/extract) inherit it automatically.
#
# DORMANT ON MERGE: require_license is a no-op unless W2V_LICENSE_GATE_ENABLED
# is true, read per-call from os.environ. It defaults to False, so this change
# alters no behaviour until gitops arms the flag. Preserve that default.
router = APIRouter(prefix="/api", dependencies=list(LICENSE_GATE))
router.include_router(storyline.router)
router.include_router(shotlist.router)
router.include_router(narration.router)
router.include_router(extract.router)

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
    "_process_narration",
    "_process_narration_shotlist",
    "_process_storyline",
    "_storyline_cache_key_for",
    "build_shotlist",
    "enhance_shotlist",
    "extract_narrative",
    "get_narration",
    "get_storyline",
    "plan_storyline",
    "router",
    "shotlist_from_findings",
    "spawn_background_task",
    "start_narration",
    "start_narration_shotlist",
    "start_storyline",
    "storyline_from_findings",
]
