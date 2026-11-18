"""Cache key construction and input hashing for content_service."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _default_serializer(obj: Any) -> Any:
    """Deterministic JSON serializer for datetime, UUID, and other objects."""
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "hex"):
        return obj.hex
    return repr(obj)


def _stable_hash(data: Any) -> str:
    """Create a SHA256-based deterministic hash of data."""
    serialized = json.dumps(data, sort_keys=True, default=_default_serializer)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


def job_status_key(tenant_id: str, job_id: str) -> str:
    """Cache key for job status polling, namespaced per tenant. TTL: 5s.

    ``tenant_id`` is REQUIRED: the cached job body is served before any DB/RLS
    check, so a tenant-agnostic key would let one tenant read another tenant's
    cached storyline/narration.
    """
    return f"narrative:job:{tenant_id}:{job_id}:status"


JOB_STATUS_TTL = 5  # Legacy constant, prefer job_status_ttl()


def job_status_ttl(status: str) -> int:
    """Return TTL in seconds based on job status.

    Processing jobs get a longer TTL to reduce DB polls during LLM calls.
    Terminal states (completed/failed) get an even longer TTL since they won't change.
    """
    if status == "processing":
        return 30
    if status in ("completed", "failed"):
        return 300
    return 10


def storyline_cache_key(
    tenant_id: str,
    analysis: Any,
    duration: float,
    message: str,
    mode: str = "webapp_commercial",
    reviews: Any = None,
    user_context: Any = None,
    model: str | None = None,
) -> str:
    """Cache key for completed storyline results, namespaced per tenant. TTL: 1hr.

    Hashes every output-determining input. ``mode``, ``reviews`` and
    ``user_context`` were previously omitted, so two requests that differed only
    in (e.g.) mode or user context collided on the same cached storyline.

    ``model`` (``llm_config.model``) is output-determining: it selects WHICH LLM
    produces the result (``model_override`` on the WM call), so a premium-model
    body must never be served to a request billed for a cheaper model. It is
    folded in ONLY when truthy: a ``None`` OR empty (``""``) model hashes exactly
    as the pre-model scheme did. WM normalizes ``llm_config.model or None``, so
    ``model=""`` and no-model produce identical output — they MUST share one key,
    and existing cached entries for model-agnostic requests are not invalidated.
    """
    data = {
        "analysis": analysis,
        "duration": duration,
        "message": message,
        "mode": mode,
        "reviews": reviews,
        "user_context": user_context,
    }
    if model:
        data["model"] = model
    return f"narrative:storyline:{tenant_id}:{_stable_hash(data)}"


STORYLINE_CACHE_TTL = 3600


def shotlist_cache_key(tenant_id: str, storyline: Any, analysis: Any) -> str:
    """Cache key for shot list results, namespaced per tenant. TTL: 24hr."""
    data = {"storyline": storyline, "analysis": analysis}
    return f"narrative:shotlist:{tenant_id}:{_stable_hash(data)}"


SHOTLIST_CACHE_TTL = 86400


def narration_cache_key(
    tenant_id: str,
    storyline: Any,
    analysis: Any,
    mode: str,
    message: str,
    synthesize_audio: bool = False,
    user_context: Any = None,
    code_quality_facts: Any = None,
    model: str | None = None,
) -> str:
    """Cache key for narration results, namespaced per tenant. TTL: 1hr.

    ``synthesize_audio`` MUST be part of the key: an audio (base64-bearing) and a
    text-only narration are different payloads and previously collided, so a
    text-only request could be served a cached audio result (or vice versa).
    ``user_context`` and ``code_quality_facts`` also steer the output and were
    previously omitted.

    ``model`` (``llm_config.model``) selects WHICH LLM produces the narration
    (``model_override`` on the WM call), so it is output-determining and folded
    in the same way as for ``storyline_cache_key`` — ONLY when truthy, so a
    ``None`` OR empty (``""``) model preserves the pre-model hash (WM normalizes
    ``model or None``, so ``""`` and no-model must share one key) and existing
    entries are not invalidated.
    """
    data = {
        "storyline": storyline,
        "analysis": analysis,
        "mode": mode,
        "message": message,
        "synthesize_audio": synthesize_audio,
        "user_context": user_context,
        "code_quality_facts": code_quality_facts,
    }
    if model:
        data["model"] = model
    return f"narrative:narration:{tenant_id}:{_stable_hash(data)}"


NARRATION_CACHE_TTL = 3600
