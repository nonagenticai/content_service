"""Unit tests for the auth middleware."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import w2v_common.auth.middleware as _mw_mod

from src.auth.middleware import (
    AuthMiddleware,
    _get_license_cache,
    _introspect_license,
    _is_trusted_network,
)
from tests.conftest import TENANT_A_STR, TENANT_B_STR, TENANT_C_STR

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    path: str = "/api/storyline",
    host: str | None = "127.0.0.1",
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Build a mock Starlette/FastAPI Request."""
    request = MagicMock()
    request.url.path = path
    if host is not None:
        request.client.host = host
    else:
        request.client = None
    request.headers = headers or {}
    request.state = MagicMock()
    return request


# ---------------------------------------------------------------------------
# _is_trusted_network
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("trusted_cidrs")
class TestIsTrustedNetwork:
    def test_loopback_ipv4(self) -> None:
        assert _is_trusted_network("127.0.0.1") is True

    def test_lan_edge_192_168_not_trusted(self) -> None:
        # w2v_common 0.6.8 removed 192.168.0.0/16 from TRUSTED_NETWORKS —
        # that range is the LAN edge / SSR proxy layer, not in-cluster.
        # See project_orchestrator_trusted_networks_bypass_2026-04-18.md.
        assert _is_trusted_network("192.168.1.42") is False

    def test_private_10(self) -> None:
        assert _is_trusted_network("10.0.0.1") is True

    def test_public_ip(self) -> None:
        assert _is_trusted_network("8.8.8.8") is False

    def test_none_host(self) -> None:
        assert _is_trusted_network(None) is False

    def test_empty_string_host(self) -> None:
        assert _is_trusted_network("") is False


# ---------------------------------------------------------------------------
# _introspect_license
# ---------------------------------------------------------------------------


class TestIntrospectLicense:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        _get_license_cache().clear()

    @pytest.mark.asyncio
    async def test_valid_active_license(self) -> None:
        payload = {"active": True, "tenant_id": "t-1", "subject_id": "u-1"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            result = await _introspect_license("valid-token-123")

        assert result is not None
        assert result["active"] is True
        assert result["tenant_id"] == "t-1"
        assert result["subject_id"] == "u-1"

    @pytest.mark.asyncio
    async def test_inactive_license(self) -> None:
        payload = {"active": False, "reason": "inactive"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            result = await _introspect_license("inactive-token")

        assert result is not None
        assert result["active"] is False
        assert result["reason"] == "inactive"

    @pytest.mark.asyncio
    async def test_expired_license(self) -> None:
        payload = {"active": False, "reason": "expired"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            result = await _introspect_license("expired-token")

        # Expired license returns the data dict but active=False,
        # so the middleware will treat it as invalid.
        assert result is not None
        assert result["active"] is False
        assert result["reason"] == "expired"

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self) -> None:
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx_connect_error()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            result = await _introspect_license("error-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_hit(self) -> None:
        payload = {"active": True, "tenant_id": "cached-tenant"}
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = payload

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            first = await _introspect_license("cache-token")
            second = await _introspect_license("cache-token")

        assert first == second
        # The HTTP client should only have been called once; the second
        # call is served from the TTLCache.
        mock_client.post.assert_called_once()


def httpx_connect_error() -> Exception:
    """Return a generic connection error for httpx mocking."""
    import httpx

    return httpx.ConnectError("Connection refused")


# ---------------------------------------------------------------------------
# AuthMiddleware.dispatch
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("trusted_cidrs")
class TestAuthMiddlewareDispatch:
    @pytest.fixture(autouse=True)
    def _clear_cache(self) -> None:
        _get_license_cache().clear()

    @staticmethod
    def _middleware() -> AuthMiddleware:
        app = MagicMock()
        return AuthMiddleware(app)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/health", "/", "/docs"])
    async def test_public_endpoints_pass_through(self, path: str) -> None:
        mw = self._middleware()
        request = _make_request(path=path)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        response = await mw.dispatch(request, call_next)

        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_valid_bearer_token(self) -> None:
        # Per w2v_common 0.6.5 / FR-401, license tokens travel ONLY in
        # `X-Tenant-Id: Bearer <token>`. Authorization is reserved for JWTs.
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="8.8.8.8",
            headers={"X-Tenant-Id": "Bearer valid-token"},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        # A REAL tenant uuid: 0.67.0 401s an active token whose tenant is a slug.
        introspect_data = {"active": True, "tenant_id": TENANT_A_STR, "subject_id": "u-99"}
        with patch.object(
            _mw_mod,
            "_introspect_license",
            new_callable=AsyncMock,
            return_value=introspect_data,
        ):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_awaited_once()
        assert request.state.tenant_id == TENANT_A_STR

    @pytest.mark.asyncio
    async def test_invalid_token_returns_401(self) -> None:
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="8.8.8.8",
            headers={"X-Tenant-Id": "Bearer bad-token"},
        )
        call_next = AsyncMock()

        with patch.object(
            _mw_mod,
            "_introspect_license",
            new_callable=AsyncMock,
            return_value={"active": False, "reason": "invalid_token"},
        ):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trusted_ip_without_token_fail_closed_default(self) -> None:
        """w2v_common 0.6.9: trusted-network unauth fails closed (401) by default."""
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="127.0.0.1",
            headers={},
        )
        call_next = AsyncMock()

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_trusted_ip_without_tenant_is_refused_even_when_opted_in(self) -> None:
        """Opting INTO the trusted network still does not conjure a tenant.

        This asserted 200 + ``request.state.tenant_id is not None`` -- which passed only
        because w2v-common substituted ``AuthSettings.default_tenant_id`` (the nil UUID)
        when no tenant was on the wire. 0.67.0 deletes that attribute and the
        substitution, so the assertion is INVERTED, not deleted: "some tenant was set"
        was never a real guarantee, it was the sentinel wearing a tenant's clothes.

        The trusted network vouches for the CALLER, never the TENANT.
        """
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="127.0.0.1",
            headers={},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(_mw_mod, "_trust_incluster_identity", return_value=True):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_public_ip_without_token_returns_401(self) -> None:
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="8.8.8.8",
            headers={},
        )
        call_next = AsyncMock()

        response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_x_tenant_id_bearer_prefix_extracted(self) -> None:
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="8.8.8.8",
            headers={"X-Tenant-Id": "Bearer x-tenant-token"},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        introspect_data = {"active": True, "tenant_id": TENANT_B_STR, "subject_id": "xu-1"}
        with patch.object(
            _mw_mod,
            "_introspect_license",
            new_callable=AsyncMock,
            return_value=introspect_data,
        ) as mock_introspect:
            response = await mw.dispatch(request, call_next)

            mock_introspect.assert_awaited_once_with("x-tenant-token")
        assert response.status_code == 200
        assert request.state.tenant_id == TENANT_B_STR

    @pytest.mark.asyncio
    async def test_trusted_network_uses_x_tenant_id_header_opt_in(self) -> None:
        """Legacy X-Tenant-Id-as-tenant fallback: opt-in via AUTH_TRUST_INCLUSTER_IDENTITY=true."""
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="10.0.0.5",
            headers={"X-Tenant-Id": TENANT_C_STR},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(_mw_mod, "_trust_incluster_identity", return_value=True):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_awaited_once()
        assert request.state.tenant_id == TENANT_C_STR

    @pytest.mark.asyncio
    async def test_trusted_network_slug_x_tenant_id_is_refused(self) -> None:
        """A legacy slug in X-Tenant-Id is refused, never taken verbatim as the tenant."""
        mw = self._middleware()
        request = _make_request(
            path="/api/storyline",
            host="10.0.0.5",
            headers={"X-Tenant-Id": "custom-tenant"},
        )
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        with patch.object(_mw_mod, "_trust_incluster_identity", return_value=True):
            response = await mw.dispatch(request, call_next)

        assert response.status_code == 401
        call_next.assert_not_awaited()


# ---------------------------------------------------------------------------
# _extract_license_token
# ---------------------------------------------------------------------------


class TestExtractLicenseToken:
    @staticmethod
    def _middleware() -> AuthMiddleware:
        app = MagicMock()
        return AuthMiddleware(app)

    def test_opaque_authorization_bearer_is_a_license_token(self) -> None:
        """w2v_common 0.74.0 made `Authorization: Bearer <opaque>` a SECOND
        accepted licence-credential shape, because that is the only header the
        platform's `license-authz` ext-authz gate reads. (Before 0.74.0 this
        test asserted the inverse.) The bearer is still also visible on the
        JWT-extraction path — `dispatch` consumes JWT-shaped bearers first."""
        mw = self._middleware()
        request = _make_request(headers={"Authorization": "Bearer my-token-abc"})

        assert mw._extract_license_token(request) == "my-token-abc"
        assert mw._extract_jwt_bearer(request) == "my-token-abc"

    def test_jwt_shaped_authorization_bearer_is_not_a_license_token(self) -> None:
        """The invariant 0.74.0 PRESERVES: a JWT-shaped bearer is never
        submitted to licence introspection — it belongs to the Keycloak JWT
        lane. This is what the FR-401 / 2026-04-26 prod-401 fix really bought,
        and widening to `Authorization` must not give it back."""
        mw = self._middleware()
        jwt_like = "header-seg.payload-seg.signature-seg"
        request = _make_request(headers={"Authorization": f"Bearer {jwt_like}"})

        assert mw._extract_license_token(request) is None
        assert mw._extract_jwt_bearer(request) == jwt_like

    def test_bearer_from_x_tenant_id_header(self) -> None:
        mw = self._middleware()
        request = _make_request(headers={"X-Tenant-Id": "Bearer tenant-token-xyz"})

        token = mw._extract_license_token(request)

        assert token == "tenant-token-xyz"

    def test_basic_scheme_ignored(self) -> None:
        mw = self._middleware()
        request = _make_request(
            headers={
                "Authorization": "Basic dXNlcjpwYXNz",
                "X-Tenant-Id": "Basic dXNlcjpwYXNz",
            }
        )

        assert mw._extract_license_token(request) is None
        assert mw._extract_jwt_bearer(request) is None

    def test_missing_header_returns_none(self) -> None:
        mw = self._middleware()
        request = _make_request(headers={})

        assert mw._extract_license_token(request) is None
        assert mw._extract_jwt_bearer(request) is None

    def test_bearer_with_extra_whitespace(self) -> None:
        """Whitespace-stripping applies to both license (X-Tenant-Id) and
        JWT (Authorization) extraction paths."""
        mw = self._middleware()
        request = _make_request(
            headers={
                "X-Tenant-Id": "Bearer   spaced-license  ",
                "Authorization": "Bearer   spaced-jwt  ",
            }
        )

        assert mw._extract_license_token(request) == "spaced-license"
        assert mw._extract_jwt_bearer(request) == "spaced-jwt"
