"""Phase D RLS — re-bind the tenant GUC at the start of every transaction.

``bind_session_tenant`` / the ``get_db_session`` auto-bind only set
``app.current_tenant`` transaction-scoped (``set_config(..., is_local=true)``,
PgBouncer-safe). That is correct as long as a single session opens exactly one
transaction. content_service's narration/storyline background tasks
(``api/narrative_routes.py``) already satisfy that: they propagate the
request-scoped identity ContextVars via ``contextvars.copy_context()`` into the
task and open a fresh single-commit ``get_db_session()`` per phase, so each
transaction binds its own tenant.

This listener is **defense in depth** for the day a background path reuses ONE
session across multiple commits (the shape that broke ``analysis_service``'s
``audit_logs`` finalize INSERT): the first commit ends the transaction the GUC
was bound in, and a later write in the same session would run unbound → RLS
fail-closes (``InsufficientPrivilege``) under enforce + the restricted
``w2v_content_app`` role. The ``after_begin`` hook re-applies
``app.current_tenant`` from the identity ContextVar at the start of EVERY
transaction, so a future refactor cannot silently reintroduce that bug.

Inert unless ``TENANT_RLS_MODE=enforce`` and a tenant is in context, so request
handlers (tenant already bound by AuthMiddleware) and any no-tenant background
loop stay no-ops. Scope is this process only. Mirrors
``analysis_service/src/shared/database/rls_events.py``.
"""

from __future__ import annotations

from sqlalchemy import Connection, event
from sqlalchemy.orm import Session, SessionTransaction
from w2v_common.auth.identity_context import get_tenant_id_from_context
from w2v_common.database.rls import tenant_rls_mode

from src.tenant import require_tenant

_installed = False


def install_rls_tenant_rebind() -> None:
    """Register the after_begin GUC re-bind listener once per process."""
    global _installed
    if _installed:
        return

    @event.listens_for(Session, "after_begin")
    def _rebind_tenant(
        session: Session, transaction: SessionTransaction, connection: Connection
    ) -> None:
        if tenant_rls_mode() != "enforce":
            return
        tenant = get_tenant_id_from_context()
        if tenant is None:
            return
        # w2v-common 0.67.0 made the identity ContextVar a ``uuid.UUID``. Postgres'
        # ``set_config(text, text, bool)`` has no ``uuid`` overload, so handing the
        # UUID object straight to psycopg raises
        # ``UndefinedFunction: function set_config(unknown, uuid, boolean) does not exist``
        # and takes down EVERY transaction opened under ``TENANT_RLS_MODE=enforce``.
        # The GUC is a text setting; send it as text.
        #
        # ``is None`` rather than ``not tenant``: ``uuid.UUID`` defines no ``__bool__``,
        # so the nil UUID is truthy and the old falsy-check could never have caught it
        # anyway. ``require_tenant`` rejects it by value instead — a nil tenant reaching
        # this hook means an unattributed write is about to happen under enforce, which
        # must fail loudly rather than bind a sentinel that satisfies every RLS policy.
        connection.exec_driver_sql(
            "SELECT set_config('app.current_tenant', %s, true)",
            (str(require_tenant(tenant, source="identity context")),),
        )

    _installed = True
