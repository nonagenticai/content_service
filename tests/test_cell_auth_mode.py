"""P7 intra-cell auth (``CELL_AUTH_MODE``) on content-service's own app stack.

content-service does not implement the cell lane itself: it builds its app with
``w2v_common.asgi.create_app`` (``src/asgi.py``), which registers the shared
``AuthMiddleware`` whose receiver lane reads ``CELL_AUTH_MODE`` /
``CELL_SERVICE_NAME`` / ``CELL_JWKS_PATH`` / ``CELL_ALLOWED_CALLERS`` at call
time. The cell lane only exists in w2v-common >= 0.91.2 (dual) and signs
subject/licence attribution from 0.93.0 — hence the pyproject floor. These
tests pin the behaviour gitops relies on when it sets those env vars on the
content-service Deployment:

* ``dual``    — a valid cell token lands on the cell lane; the shared
                ``DOWNSTREAM_LICENSE_TOKEN``-style delegation still works.
* ``enforce`` — that shared-token delegation is refused (401
                ``CELL_TOKEN_REQUIRED``) and never introspected; the cell token
                is accepted; a keyring issuer not in ``CELL_ALLOWED_CALLERS`` is
                refused.

Every assertion here fails on a w2v-common without the cell lane (< 0.91.2):
the cell header is ignored, enforce does not exist, and the shared token is
always accepted.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI
from jwt.algorithms import OKPAlgorithm
from w2v_common.auth.cell_jwt import CELL_TOKEN_HEADER, CELL_TOKEN_TYPE, reset_cell_state
from w2v_common.auth.identity_context import (
    EFFECTIVE_TENANT_HEADER,
    clear_identity_context,
    get_tenant_id_from_context,
    get_tenant_source_from_context,
)

from tests.conftest import TENANT_A, TENANT_B

CALLEE = "content-service"
ORCH = "w2v-orchestrator"
#: A real keyring issuer that is NOT an allowed caller of content-service.
OUTSIDER = "tts-gateway"
DEPLOYMENT = "strato-w2v"
#: The shared service-account subject behind DOWNSTREAM_LICENSE_TOKEN
#: (AUTH_DELEGATING_SUBJECTS lists it in w2v-common-config).
SA_SUBJECT = "01a0dd02-0000-7000-8000-000000000001"


def _jwk(key: Ed25519PrivateKey, kid: str, iss: str) -> dict[str, Any]:
    entry = json.loads(OKPAlgorithm.to_jwk(key.public_key()))
    entry.update({"kid": kid, "iss": iss})
    return entry


@pytest.fixture(scope="module")
def orch_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(scope="module")
def outsider_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


@pytest.fixture(autouse=True)
def _cell_env(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    orch_key: Ed25519PrivateKey,
    outsider_key: Ed25519PrivateKey,
) -> Iterator[None]:
    """The gitops env of the content-service Deployment (minus CELL_AUTH_MODE)."""
    jwks = tmp_path / "jwks.json"
    jwks.write_text(
        json.dumps(
            {
                "keys": [
                    _jwk(orch_key, "orch-1", ORCH),
                    _jwk(outsider_key, "outsider-1", OUTSIDER),
                ]
            }
        )
    )
    for name in ("CELL_AUTH_MODE", "CELL_JWT_SIGNING_KEY", "CELL_JWT_KID", "W2V_LOCAL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CELL_SERVICE_NAME", CALLEE)
    monkeypatch.setenv("CELL_DEPLOYMENT_ID", DEPLOYMENT)
    monkeypatch.setenv("CELL_JWKS_PATH", str(jwks))
    monkeypatch.setenv("CELL_ALLOWED_CALLERS", ORCH)
    monkeypatch.setenv("AUTH_DELEGATING_SUBJECTS", SA_SUBJECT)
    reset_cell_state()
    clear_identity_context()
    yield
    reset_cell_state()
    clear_identity_context()


def _cell_token(key: Ed25519PrivateKey, *, kid: str = "orch-1", iss: str = ORCH) -> str:
    now = int(time.time())
    claims = {
        "iss": iss,
        "aud": CALLEE,
        "cell": DEPLOYMENT,
        "scope": "tenant",
        "tenant": str(TENANT_A),
        "run": None,
        "actor": None,
        "project": None,
        "subject": None,
        "license": None,
        "iat": now,
        "nbf": now,
        "exp": now + 60,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": kid, "typ": CELL_TOKEN_TYPE})


def _content_service_app() -> FastAPI:
    """content-service's middleware stack, exactly as ``src/asgi.py`` builds it."""
    from w2v_common.asgi import create_app

    from src.config import get_settings

    app = create_app(get_settings(), auth_enabled=True)

    @app.get("/api/narrative/_whoami")
    async def _whoami() -> dict[str, Any]:
        source = get_tenant_source_from_context()
        return {
            "tenant": str(get_tenant_id_from_context() or ""),
            "source": source.value if source is not None else None,
        }

    return app


#: The shared-token call: content-service's peers send DOWNSTREAM_LICENSE_TOKEN
#: (an SA licence for tenant B) plus the effective tenant they act for.
_SHARED_TOKEN_HEADERS = {
    "X-Tenant-Id": "Bearer shared-downstream-licence",
    EFFECTIVE_TENANT_HEADER: str(TENANT_A),
}


async def _call(headers: dict[str, str]) -> tuple[int, dict[str, Any], AsyncMock]:
    introspection = {"active": True, "tenant_id": str(TENANT_B), "subject_id": SA_SUBJECT}
    with patch(
        "w2v_common.auth.middleware._introspect_license",
        new_callable=AsyncMock,
        return_value=introspection,
    ) as introspect:
        transport = httpx.ASGITransport(app=_content_service_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            resp = await client.get("/api/narrative/_whoami", headers=headers)
    return resp.status_code, resp.json(), introspect


@pytest.mark.parametrize("mode", ["dual", "enforce"])
async def test_cell_token_from_allowed_caller_lands_on_cell_lane(
    monkeypatch: pytest.MonkeyPatch, orch_key: Ed25519PrivateKey, mode: str
) -> None:
    monkeypatch.setenv("CELL_AUTH_MODE", mode)
    status, body, introspect = await _call({CELL_TOKEN_HEADER: _cell_token(orch_key)})
    assert status == 200, body
    assert body == {"tenant": str(TENANT_A), "source": "cell-jwt"}
    introspect.assert_not_awaited()


async def test_dual_still_accepts_the_shared_downstream_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELL_AUTH_MODE", "dual")
    status, body, introspect = await _call(_SHARED_TOKEN_HEADERS)
    assert status == 200, body
    # Delegated to the effective tenant, not the SA licence's own tenant B.
    assert body["tenant"] == str(TENANT_A)
    assert body["source"] != "cell-jwt"
    introspect.assert_awaited_once()


async def test_enforce_refuses_the_shared_downstream_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CELL_AUTH_MODE", "enforce")
    status, body, introspect = await _call(_SHARED_TOKEN_HEADERS)
    assert status == 401, body
    assert body["error"]["code"] == "CELL_TOKEN_REQUIRED"
    introspect.assert_not_awaited()


async def test_enforce_refuses_a_keyring_issuer_that_is_not_an_allowed_caller(
    monkeypatch: pytest.MonkeyPatch, outsider_key: Ed25519PrivateKey
) -> None:
    monkeypatch.setenv("CELL_AUTH_MODE", "enforce")
    token = _cell_token(outsider_key, kid="outsider-1", iss=OUTSIDER)
    status, body, introspect = await _call({CELL_TOKEN_HEADER: token})
    assert status == 401, body
    assert body["error"]["code"] == "CELL_TOKEN_INVALID"
    introspect.assert_not_awaited()
