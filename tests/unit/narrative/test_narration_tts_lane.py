"""Narration TTS -> modality-gateway under ``MODALITY_GATEWAY_AUTH_MODE``.

Real ``TTSClient`` (``BaseServiceClient``) with its real event hooks; only the
network is replaced. Default must send the forwarded request token exactly as
before; ``tenant_licence`` must send the bound tenant's own minted ``w2v``
licence and nothing else.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from w2v_common.auth.bundle_tokens import (
    BundleMinterNotConfiguredError,
    BundleTokenMinter,
    reset_bundle_token_minter,
    set_bundle_token_minter,
)
from w2v_common.auth.identity_context import clear_identity_context, set_identity_context
from w2v_common.tenant import TenantSource

from src.config import Settings
from src.services.narrative.narration_builder import NarrationBuilder

TENANT = "0199a1b2-0000-7000-8000-00000000000a"
REQUEST_TOKEN = "request-scoped-licence"  # pragma: allowlist secret
TOKEN_URL = "http://kc.test/realms/secure-apps/protocol/openid-connect/token"
_REAL_ASYNC_CLIENT = httpx.AsyncClient


class _Wire:
    def __init__(self) -> None:
        self.tts: list[httpx.Request] = []
        self.minted: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "minter-sa", "expires_in": 300})
        if request.url.host == "lic.test":
            app = request.url.params["app"]
            self.minted.append(app)
            return httpx.Response(
                201,
                json={
                    "token": f"lic-{app}-{len(self.minted)}",
                    "expires_at": datetime.fromtimestamp(4_000_000_000, tz=UTC).isoformat(),
                    "tenant_id": request.url.path.split("/")[3],
                    "app": app,
                    "license_id": f"lid-{app}-{len(self.minted)}",
                },
            )
        self.tts.append(request)
        return httpx.Response(200, content=b"ID3audio")


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Wire]:
    monkeypatch.delenv("MODALITY_GATEWAY_AUTH_MODE", raising=False)
    w = _Wire()

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(w.handler)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    reset_bundle_token_minter()
    set_bundle_token_minter(
        BundleTokenMinter(
            licenses_url="http://lic.test",
            token_url=TOKEN_URL,
            client_id="w2v-bundle-minter",
            client_secret="s",  # pragma: allowlist secret
            transport=httpx.MockTransport(w.handler),
        )
    )
    set_identity_context(
        TENANT, "inbound-licence", "subject-1", tenant_source=TenantSource.LICENSE_TOKEN
    )
    yield w
    clear_identity_context()
    reset_bundle_token_minter()


async def _narrate(builder: NarrationBuilder) -> list[dict]:
    parts = [{"part_id": "p1", "text": "Hello there."}]
    await builder._synthesize_parts(parts, license_token=REQUEST_TOKEN)
    return parts


async def test_default_sends_forwarded_token_unchanged(wire: _Wire) -> None:
    parts = await _narrate(NarrationBuilder(settings=Settings(), wm_client=AsyncMock()))
    assert "audio_base64" in parts[0]
    assert wire.tts[0].headers["X-Tenant-Id"] == f"Bearer {REQUEST_TOKEN}"
    assert wire.minted == []


async def test_tenant_licence_sends_tenants_w2v_licence(
    wire: _Wire, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MODALITY_GATEWAY_AUTH_MODE", "tenant_licence")
    parts = await _narrate(NarrationBuilder(settings=Settings(), wm_client=AsyncMock()))
    assert "audio_base64" in parts[0]
    req = wire.tts[0]
    assert req.headers["X-Tenant-Id"] == "Bearer lic-w2v-1"
    assert req.headers["X-License-Id"] == "lid-w2v-1"
    assert "X-Effective-Tenant-Id" not in req.headers
    assert REQUEST_TOKEN not in str(req.headers)
    assert wire.minted == ["w2v"]


def test_tenant_licence_without_minter_fails_at_builder_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "BUNDLE_MINTER_CLIENT_ID",
        "BUNDLE_MINTER_CLIENT_SECRET",
        "BUNDLE_MINTER_TOKEN_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MODALITY_GATEWAY_AUTH_MODE", "tenant_licence")
    reset_bundle_token_minter()
    with pytest.raises(BundleMinterNotConfiguredError):
        NarrationBuilder(settings=Settings(), wm_client=AsyncMock())
