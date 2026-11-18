"""FastAPI Depends() helpers for dependency injection."""

from __future__ import annotations

from fastapi import HTTPException, Request
from w2v_common.tenant import TenantError

from src.infra.task_manager import TaskManager
from src.services.scripts.orchestrator import ScriptOrchestrator
from src.services.scripts.storage_service import StorageService
from src.tenant import require_tenant


def get_tenant_id(request: Request) -> str:
    """Return the request's tenant as a canonical UUID string, or 401.

    This used to default to the literal string ``"anonymous"``. That was a
    fail-OPEN sentinel: it is not a UUID, so under w2v-common 0.67.0 it can no
    longer reach a ``uuid`` ``tenant_id`` column at all, and before the flip it
    would have pooled every unauthenticated request into one shared pseudo-tenant
    whose rows are visible to each other. There is no default tenant — a request
    that arrives without one is unauthenticated and must be refused here, at the
    boundary, rather than silently attributed.
    """
    try:
        return str(require_tenant(getattr(request.state, "tenant_id", None), source="request"))
    except TenantError as exc:
        raise HTTPException(status_code=401, detail="request has no valid tenant") from exc


def get_orchestrator(request: Request) -> ScriptOrchestrator:
    """Retrieve the ScriptOrchestrator instance from application state."""
    return request.app.state.orchestrator


def get_task_manager(request: Request) -> TaskManager:
    """Retrieve the TaskManager instance from application state."""
    return request.app.state.task_manager


def get_storage(request: Request) -> StorageService:
    """Retrieve the StorageService instance from application state."""
    return request.app.state.storage
