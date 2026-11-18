"""Tenant-id coercion for content_service — the one place a ``str`` becomes a ``uuid.UUID``.

w2v-common 0.67.0 made ``tenant_id`` a real ``uuid.UUID`` end to end (``User.tenant_id``,
the identity ContextVar, ``bind_session_tenant``, the metering cost bus) and DELETED
``AuthSettings.default_tenant_id``. There is no fallback tenant any more: an absent or
unparseable tenant must FAIL, never be substituted.

This module is the single coercion seam. Everything upstream of the database — request
state, cache keys, redis keys, log fields — keeps carrying the canonical ``str`` spelling;
everything that touches Postgres goes through :func:`require_tenant` first, because the
``tenant_id`` columns are ``Uuid`` and the RLS GUC bind is the choke point every repository
method runs before its first query.
"""

from __future__ import annotations

import uuid

from w2v_common.tenant import InvalidTenantIdError, parse_tenant_id

__all__ = ["require_tenant"]


def require_tenant(value: object, *, source: str = "tenant_id") -> uuid.UUID:
    """Return ``value`` as a real, non-nil ``uuid.UUID`` or raise.

    The nil check is by VALUE, not by spelling. ``uuid.UUID`` happily accepts
    ``"00000000-0000-0000-0000-000000000000"``, the undashed
    ``"00000000000000000000000000000000"``, the braced
    ``"{00000000-0000-0000-0000-000000000000}"`` and the
    ``"urn:uuid:00000000-0000-0000-0000-000000000000"`` forms as the SAME nil value, and
    w2v-common's older trusted-network path wrote exactly that value as the
    ``default_tenant_id`` sentinel. A ``None``-only guard would therefore leave the hole
    wide open: rows attributed to the nil tenant are unattributed rows wearing a tenant's
    clothes, and they defeat RLS isolation and cost attribution alike. Comparing
    ``parsed.int == 0`` rejects every spelling at once.

    Raises:
        MissingTenantError: ``value`` is ``None`` or empty (raised by ``parse_tenant_id``).
        InvalidTenantIdError: ``value`` is not a UUID, or is the nil UUID.
    """
    parsed = parse_tenant_id(value, source=source)
    if parsed.int == 0:
        raise InvalidTenantIdError(
            f"{source}: nil UUID is not a tenant — every row must carry a real registered tenant"
        )
    return parsed
