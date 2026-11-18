"""Tests for src/auth/middleware.py — License token authentication middleware."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from w2v_common.tenant import MissingTenantError

from src.auth.middleware import (
    PUBLIC_ENDPOINTS,
    PUBLIC_PREFIXES,
    AuthMiddleware,
    _get_license_cache,
    _introspect_license,
    _is_trusted_network,
)
from src.auth.models import User
from tests.conftest import IN_CLUSTER_TRUSTED_CIDRS, TENANT_A, TENANT_A_STR, TENANT_B_STR

# ── _is_trusted_network ────────────────────────────────────────────────


@pytest.mark.usefixtures("trusted_cidrs")
class TestIsTrustedNetwork:
    def test_localhost_ipv4(self) -> None:
        assert _is_trusted_network("127.0.0.1") is True

    def test_localhost_ipv6(self) -> None:
        assert _is_trusted_network("::1") is True

    def test_private_10_range(self) -> None:
        assert _is_trusted_network("10.0.0.5") is True

    def test_private_172_range(self) -> None:
        assert _is_trusted_network("172.16.1.1") is True

    def test_lan_edge_192_168_not_trusted(self) -> None:
        # w2v_common 0.6.8 removed 192.168.0.0/16 from TRUSTED_NETWORKS —
        # that range is the LAN edge / SSR proxy layer, not in-cluster.
        assert _is_trusted_network("192.168.0.100") is False

    def test_public_ip_not_trusted(self) -> None:
        assert _is_trusted_network("8.8.8.8") is False

    def test_none_not_trusted(self) -> None:
        assert _is_trusted_network(None) is False

    def test_empty_string(self) -> None:
        assert _is_trusted_network("") is False

    def test_invalid_ip(self) -> None:
        assert _is_trusted_network("not-an-ip") is False


class TestTrustedNetworksConfig:
    def test_networks_are_populated(self, trusted_cidrs) -> None:
        import w2v_common.auth.middleware as lib_mod

        assert len(lib_mod.TRUSTED_NETWORKS) > 0
        assert [str(n) for n in lib_mod.TRUSTED_NETWORKS] == IN_CLUSTER_TRUSTED_CIDRS.split(",")
        assert lib_mod.TRUSTED_NETWORKS is trusted_cidrs

    def test_nothing_trusted_without_trusted_cidrs(self) -> None:
        # w2v_common >= 0.89.0: trust lanes are explicit opt-in. Prod config sets
        # no TRUSTED_CIDRS, so the service must trust no peer — not even
        # loopback. TRUSTED_NETWORKS is parsed at import, so prove the default
        # in a fresh interpreter with TRUSTED_CIDRS removed from the env.
        env = {k: v for k, v in os.environ.items() if k != "TRUSTED_CIDRS"}
        probe = (
            "from src.auth.middleware import TRUSTED_NETWORKS, _is_trusted_network as t\n"
            "assert TRUSTED_NETWORKS == [], TRUSTED_NETWORKS\n"
            "for ip in ('127.0.0.1', '::1', '10.0.0.5', '172.16.1.1'):\n"
            "    assert t(ip) is False, ip\n"
            "print('ok')\n"
        )
        result = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
            [sys.executable, "-c", probe],
            env=env,
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ok"

    def test_public_endpoints(self) -> None:
        assert "/health" in PUBLIC_ENDPOINTS
        assert "/" in PUBLIC_ENDPOINTS
        assert "/docs" in PUBLIC_ENDPOINTS
        assert "/health/ready" in PUBLIC_ENDPOINTS
        assert "/health/live" in PUBLIC_ENDPOINTS

    def test_public_prefixes(self) -> None:
        assert "/docs" in PUBLIC_PREFIXES
        assert "/health/" in PUBLIC_PREFIXES


# ── _introspect_license ────────────────────────────────────────────────


class TestIntrospectLicense:
    @pytest.mark.asyncio
    async def test_active_license_cached(self) -> None:
        """An active license is returned and then cached."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"active": True, "tenant_id": "t1"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            # Clear cache to ensure clean test
            _get_license_cache().clear()
            result = await _introspect_license("test-token-abc")
            assert result is not None
            assert result["active"] is True
            assert result["tenant_id"] == "t1"

    @pytest.mark.asyncio
    async def test_inactive_license(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"active": False, "reason": "expired"}

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            _get_license_cache().clear()
            result = await _introspect_license("expired-token")
            assert result is not None
            assert result["active"] is False

    @pytest.mark.asyncio
    async def test_introspection_failure_returns_none(self) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        import httpx

        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            _get_license_cache().clear()
            result = await _introspect_license("any-token")
            assert result is None

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("w2v_common.auth.middleware.httpx.AsyncClient", return_value=mock_client):
            _get_license_cache().clear()
            result = await _introspect_license("token-for-500")
            assert result is None


# ── AuthMiddleware._extract_license_token ──────────────────────────────


class TestExtractLicenseToken:
    def _make_request(self, headers: dict[str, str]) -> MagicMock:
        request = MagicMock()
        request.headers = headers
        return request

    def test_opaque_authorization_bearer_is_a_license_token(self) -> None:
        """w2v_common 0.74.0 accepts `Authorization: Bearer <opaque>` as a
        licence credential — the only shape the platform's `license-authz`
        ext-authz gate can read. (This test asserted the inverse pre-0.74.0.)"""
        mw = AuthMiddleware(app=MagicMock())
        req = self._make_request({"Authorization": "Bearer my-token-123"})
        assert mw._extract_license_token(req) == "my-token-123"
        assert mw._extract_jwt_bearer(req) == "my-token-123"

    def test_jwt_shaped_authorization_bearer_is_not_a_license_token(self) -> None:
        """Preserved across 0.74.0: a JWT-shaped bearer goes down the Keycloak
        JWT path and is NEVER submitted to licence introspection (FR-401 /
        the 2026-04-26 prod 401s)."""
        mw = AuthMiddleware(app=MagicMock())
        jwt_like = "header-seg.payload-seg.signature-seg"
        req = self._make_request({"Authorization": f"Bearer {jwt_like}"})
        assert mw._extract_license_token(req) is None
        assert mw._extract_jwt_bearer(req) == jwt_like

    def test_bearer_from_x_tenant_id(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        req = self._make_request({"X-Tenant-Id": "Bearer tenant-token-456"})
        assert mw._extract_license_token(req) == "tenant-token-456"

    def test_x_tenant_id_wins_over_authorization_bearer(self) -> None:
        """Since w2v_common 0.74.0 BOTH headers can carry a licence token, so
        PRECEDENCE is now the load-bearing property: `X-Tenant-Id` is checked
        first. BaseServiceClient puts the CALLER's token there and its baked
        SERVICE-ACCOUNT token on `Authorization`; preferring `Authorization`
        would silently re-attribute every delegated call to the service
        account's tenant."""
        mw = AuthMiddleware(app=MagicMock())
        req = self._make_request(
            {
                "X-Tenant-Id": "Bearer from-tenant",
                "Authorization": "Bearer from-auth",
            }
        )
        assert mw._extract_license_token(req) == "from-tenant"
        assert mw._extract_jwt_bearer(req) == "from-auth"

    def test_no_token_returns_none(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        req = self._make_request({})
        assert mw._extract_license_token(req) is None

    def test_non_bearer_x_tenant_id_skipped(self) -> None:
        """X-Tenant-Id without Bearer prefix is not a token."""
        mw = AuthMiddleware(app=MagicMock())
        req = self._make_request({"X-Tenant-Id": "plain-tenant-id"})
        assert mw._extract_license_token(req) is None

    def test_case_insensitive_bearer_on_both_license_headers(self) -> None:
        """The property being pinned is unchanged: `bearer ` scheme matching is
        case-insensitive. Only the outcome moved — since w2v_common 0.74.0 an
        opaque `Authorization: BEARER ...` yields a licence token instead of
        None. A JWT-shaped one still does not, in any casing."""
        mw = AuthMiddleware(app=MagicMock())
        req_license = self._make_request({"X-Tenant-Id": "BEARER TOKEN-UPPER"})
        assert mw._extract_license_token(req_license) == "TOKEN-UPPER"

        req_auth = self._make_request({"Authorization": "BEARER TOKEN-UPPER"})
        assert mw._extract_license_token(req_auth) == "TOKEN-UPPER"
        assert mw._extract_jwt_bearer(req_auth) == "TOKEN-UPPER"

        req_jwt = self._make_request({"Authorization": "BeArEr A-SEG.B-SEG.C-SEG"})
        assert mw._extract_license_token(req_jwt) is None
        assert mw._extract_jwt_bearer(req_jwt) == "A-SEG.B-SEG.C-SEG"


# ── AuthMiddleware.dispatch ─────────────────────────────────────────────


@pytest.mark.usefixtures("trusted_cidrs")
class TestAuthMiddlewareDispatch:
    def _make_request(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        client_host: str | None = None,
    ) -> MagicMock:
        request = MagicMock()
        request.url.path = path
        request.headers = headers or {}
        request.state = MagicMock()
        if client_host:
            request.client = MagicMock()
            request.client.host = client_host
        else:
            request.client = None
        return request

    @pytest.mark.asyncio
    async def test_public_endpoint_allowed(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request("/health")
        await mw.dispatch(request, call_next)
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_public_prefix_allowed(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request("/docs/something")
        await mw.dispatch(request, call_next)
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request("/health/")
        await mw.dispatch(request, call_next)
        call_next.assert_called_once()

    @pytest.mark.asyncio
    async def test_valid_license_token(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request(
            "/api/scripts/generate",
            headers={"X-Tenant-Id": "Bearer valid-token"},
        )
        # A REAL tenant uuid. w2v-common 0.67.0 rejects a slug tenant from
        # introspection with a 401 ("license token has no valid tenant"): an
        # active token whose tenant is not a UUID would otherwise be coerced
        # into every downstream ``uuid`` tenant_id column.
        introspect_result = {"active": True, "tenant_id": TENANT_A_STR, "subject_id": "user1"}
        with patch(
            "w2v_common.auth.middleware._introspect_license", new_callable=AsyncMock
        ) as mock_intr:
            mock_intr.return_value = introspect_result
            await mw.dispatch(request, call_next)
            call_next.assert_called_once()
            # Check user was set on request state
            assert request.state.tenant_id == TENANT_A_STR

    @pytest.mark.asyncio
    async def test_invalid_license_token_returns_401(self) -> None:
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock()
        request = self._make_request(
            "/api/scripts/generate",
            headers={"X-Tenant-Id": "Bearer bad-token"},
        )
        with patch(
            "w2v_common.auth.middleware._introspect_license", new_callable=AsyncMock
        ) as mock_intr:
            mock_intr.return_value = {"active": False, "reason": "expired"}
            response = await mw.dispatch(request, call_next)
            assert response.status_code == 401
            call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_introspection_returns_none(self) -> None:
        """When introspection returns None, the token is invalid."""
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock()
        request = self._make_request(
            "/api/scripts/generate",
            headers={"X-Tenant-Id": "Bearer broken"},
        )
        with patch(
            "w2v_common.auth.middleware._introspect_license", new_callable=AsyncMock
        ) as mock_intr:
            mock_intr.return_value = None
            response = await mw.dispatch(request, call_next)
            assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_trusted_network_no_token_fail_closed_default(self) -> None:
        """w2v_common 0.6.9: trusted-network unauth fails closed (401) by default."""
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock()
        request = self._make_request(
            "/api/scripts/generate",
            client_host="127.0.0.1",
        )
        response = await mw.dispatch(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_network_no_tenant_is_refused_even_when_opted_in(self) -> None:
        """Opting INTO the trusted network still does not conjure a tenant.

        This test previously asserted ``call_next.assert_called_once()`` -- i.e. that
        the request was ADMITTED with no tenant on the wire, because w2v-common
        substituted ``AuthSettings.default_tenant_id`` (the nil UUID) for it. That
        attribute is deleted in 0.67.0 and the substitution with it, so the assertion
        is inverted rather than dropped: the behaviour it locked in was the defect.

        The trusted network vouches for the CALLER, never for the TENANT. Any pod
        inside the trusted CIDRs could assert any tenant, so a request that names
        none is unattributable and must be refused.
        """
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request(
            "/api/scripts/generate",
            client_host="127.0.0.1",
        )
        with patch("w2v_common.auth.middleware._trust_incluster_identity", return_value=True):
            response = await mw.dispatch(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_trusted_network_with_x_tenant_id_header_opt_in(self) -> None:
        """Legacy X-Tenant-Id-as-tenant fallback: opt-in via AUTH_TRUST_INCLUSTER_IDENTITY=true."""
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request(
            "/api/scripts/generate",
            headers={"X-Tenant-Id": TENANT_B_STR},
            client_host="10.0.0.1",
        )
        with patch("w2v_common.auth.middleware._trust_incluster_identity", return_value=True):
            await mw.dispatch(request, call_next)
        call_next.assert_called_once()
        assert request.state.tenant_id == TENANT_B_STR

    @pytest.mark.asyncio
    async def test_trusted_network_slug_x_tenant_id_is_refused(self) -> None:
        """A legacy slug in X-Tenant-Id is refused, never coerced.

        The header used to be taken verbatim as the tenant, so ``custom-tenant``
        became a tenant id. Under 0.67.0 the value must parse as a UUID.
        """
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = self._make_request(
            "/api/scripts/generate",
            headers={"X-Tenant-Id": "custom-tenant"},
            client_host="10.0.0.1",
        )
        with patch("w2v_common.auth.middleware._trust_incluster_identity", return_value=True):
            response = await mw.dispatch(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_untrusted_no_token_returns_401(self) -> None:
        """No token, untrusted network => 401."""
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock()
        request = self._make_request(
            "/api/scripts/generate",
            client_host="8.8.8.8",
        )
        response = await mw.dispatch(request, call_next)
        assert response.status_code == 401
        call_next.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_client_no_token_returns_401(self) -> None:
        """No client IP, no token => 401."""
        mw = AuthMiddleware(app=MagicMock())
        call_next = AsyncMock()
        request = self._make_request("/api/scripts/generate")
        response = await mw.dispatch(request, call_next)
        assert response.status_code == 401


# ── User model ──────────────────────────────────────────────────────────


class TestUserModel:
    def test_user_creation(self) -> None:
        u = User(sub="t1", preferred_username="admin", tenant_id=TENANT_A)
        assert u.sub == "t1"
        assert u.preferred_username == "admin"
        assert u.tenant_id == TENANT_A

    def test_user_rejects_slug_tenant(self) -> None:
        """``User.tenant_id`` is a ``uuid.UUID`` in 0.67.0; a slug cannot validate.

        Built via ``model_validate`` from a dict -- the shape the middleware actually
        constructs a ``User`` from (an introspection / JWT-claims payload), and the
        one that carries an untyped slug in the first place.
        """
        with pytest.raises(ValidationError):
            User.model_validate({"sub": "t1", "tenant_id": "tenant-1"})

    def test_effective_tenant_id_from_tenant(self) -> None:
        u = User(sub="sub-1", tenant_id=TENANT_A)
        assert u.effective_tenant_id == TENANT_A

    def test_effective_tenant_id_refuses_to_fall_back_to_sub(self) -> None:
        """A tenant-less User raises instead of self-naming a tenant from ``sub``.

        This asserted ``effective_tenant_id == "sub-1"``. That fallback authenticated
        a token with no tenant claim as a SELF-NAMED tenant equal to its own subject
        -- inventing a tenant that was never registered, and writing rows under it.
        0.67.0 raises ``MissingTenantError`` instead, so the refusal is what is pinned.
        """
        u = User(sub="sub-1", tenant_id=None)
        with pytest.raises(MissingTenantError):
            _ = u.effective_tenant_id

    def test_default_username(self) -> None:
        u = User(sub="s1")
        assert u.preferred_username == "anonymous"
