"""Pins `_get_license_token` against the w2v_common 0.74.0 licence-lane change.

Before 0.74.0 the only accepted licence credential was `X-Tenant-Id: Bearer`.
0.74.0 added `Authorization: Bearer <opaque>` because that is the only header
the platform's `license-authz` ext-authz gate forwards. This helper parsed the
legacy header directly, so for every gate-fronted caller it returned None and
the TTS forward silently fell back to the trusted-network bypass. It now reads
the token `AuthMiddleware` actually authenticated and stashed.
"""

from types import SimpleNamespace
from typing import Any, cast

from src.api.narrative._shared import _get_license_token


def _request(headers: dict[str, str], **state: Any) -> Any:
    return cast(Any, SimpleNamespace(headers=headers, state=SimpleNamespace(**state)))


def test_stashed_token_is_used_whichever_header_carried_it() -> None:
    """A caller arriving via `Authorization: Bearer <opaque>` (the ext-authz
    gate's shape) has no `X-Tenant-Id`; the middleware's stash is the only
    source, and returning None here would drop the call to trusted-network."""
    request = _request(
        {"Authorization": "Bearer opaque-license-abc"},
        license_token="opaque-license-abc",
    )

    assert _get_license_token(request) == "opaque-license-abc"


def test_legacy_x_tenant_id_header_still_works_without_a_stash() -> None:
    """Trusted-network / non-licence-lane requests never get a stash, so the
    legacy header parse stays as the fallback."""
    request = _request({"X-Tenant-Id": "Bearer legacy-token-xyz"})

    assert _get_license_token(request) == "legacy-token-xyz"


def test_no_credential_returns_none() -> None:
    assert _get_license_token(_request({})) is None
    assert _get_license_token(_request({"X-Tenant-Id": "plain-tenant-id"})) is None


def test_jwt_lane_authorization_is_not_harvested_as_a_license_token() -> None:
    """The invariant 0.74.0 preserves: a JWT-shaped bearer is not a licence
    token. The JWT lane sets no `license_token` stash, so the helper must not
    reach into `Authorization` on its own."""
    request = _request({"Authorization": "Bearer head-seg.body-seg.sig-seg"})

    assert _get_license_token(request) is None
