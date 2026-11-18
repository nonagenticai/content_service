"""Narration TTS goes through the run's ``tts`` provider slot (w2v_common.providers).

* ``W2V_PROVIDER_SLOTS`` off (default) ⇒ the platform request is byte-identical to
  before: same URL, same JSON body, no orchestrator resolve call.
* ``enforce`` + a ``deployment`` slot (no tenant bound) ⇒ the same platform request.
* ``enforce`` + a ``tenant`` slot ⇒ the tenant's provider URL is hit with ONLY its
  Bearer token — no platform licence, no identity headers — and the platform TTS is
  never called.
"""

from __future__ import annotations

import base64
import json
import uuid
from collections.abc import Iterator

import httpx
import pytest
import respx
from pydantic import SecretStr
from w2v_common.auth.identity_context import clear_identity_context, set_identity_context
from w2v_common.discovery import service_url
from w2v_common.providers import (
    SWITCH_ENV,
    ProviderSlot,
    ProviderSlotClient,
    clear_provider_slot_cache,
)
from w2v_common.providers import _http as provider_http
from w2v_common.providers.adapters import pcm16_to_wav
from w2v_common.providers.slots import RESOLVE_PATH, set_provider_slot_client
from w2v_common.tenant import TenantSource

from src.config import Settings
from src.services.narrative.narration_builder import NarrationBuilder

TENANT = uuid.UUID("0199a1b2-0000-7000-8000-00000000000a")
TENANT_SECRET = "tenant-tts-key-SECRET"  # pragma: allowlist secret
LICENCE = "platform-licence-token"  # pragma: allowlist secret
PUBLIC_IP = "93.184.216.34"
PLATFORM_AUDIO = b"RIFF-platform-wav"
EXPECTED_PLATFORM_BODY = {
    "text": "Welcome to the tour.",
    "provider": "kokoro",
    "voice": "af_heart",
    "speed": 1.0,
    "output_format": "wav",
}


def _wav() -> bytes:
    return pcm16_to_wav(b"\x01\x00" * 2400, 24_000)


class _StubSlots(ProviderSlotClient):
    """Answers the resolve with a fixed slot (the orchestrator wire is w2v_common's test)."""

    def __init__(self, slot: ProviderSlot) -> None:
        super().__init__()
        self._slot = slot
        self.calls: list[str] = []

    async def resolve(self, slot: str, *, run_id: str | None) -> ProviderSlot:
        self.calls.append(slot)
        return self._slot


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    async def _public(_host: str, _port: int) -> list[str]:
        return [PUBLIC_IP]

    monkeypatch.setattr(provider_http, "_resolve", _public)
    monkeypatch.delenv(SWITCH_ENV, raising=False)
    clear_identity_context()
    clear_provider_slot_cache()
    set_provider_slot_client(None)
    yield
    clear_identity_context()
    clear_provider_slot_cache()
    set_provider_slot_client(None)


def _parts() -> list[dict]:
    return [{"part_id": "intro", "text": "Welcome to the tour.", "word_count": 4}]


async def _synthesize(parts: list[dict]) -> list[dict]:
    builder = NarrationBuilder(settings=Settings(), wm_client=None)  # type: ignore[arg-type]
    await builder._synthesize_parts(parts, license_token=LICENCE)
    return parts


def _platform_url() -> str:
    return f"{service_url('tts').rstrip('/')}/api/tts/synthesize"


def _resolve_url() -> str:
    return f"{service_url('orchestrator').rstrip('/')}{RESOLVE_PATH}"


def _assert_platform_request(request: httpx.Request) -> None:
    assert str(request.url) == _platform_url()
    assert json.loads(request.content) == EXPECTED_PLATFORM_BODY


@respx.mock(assert_all_called=False)
async def test_off_sends_the_identical_platform_request(respx_mock: respx.MockRouter) -> None:
    resolve = respx_mock.post(_resolve_url()).mock(return_value=httpx.Response(200, json={}))
    platform = respx_mock.post(_platform_url()).mock(
        return_value=httpx.Response(200, content=PLATFORM_AUDIO)
    )
    stub = _StubSlots(ProviderSlot(slot="tts", source="tenant", kind="openai-compatible"))
    set_provider_slot_client(stub)
    set_identity_context(TENANT, "lic-1", "subject-1", tenant_source=TenantSource.LICENSE_TOKEN)

    parts = await _synthesize(_parts())

    assert platform.call_count == 1
    _assert_platform_request(platform.calls[0].request)
    assert not resolve.called and stub.calls == [], "off must never resolve a slot"
    assert base64.b64decode(parts[0]["audio_base64"]) == PLATFORM_AUDIO
    assert "degraded" not in parts[0]


@respx.mock(assert_all_called=False)
async def test_enforce_deployment_slot_sends_the_same_platform_request(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SWITCH_ENV, "enforce")
    resolve = respx_mock.post(_resolve_url()).mock(return_value=httpx.Response(200, json={}))
    platform = respx_mock.post(_platform_url()).mock(
        return_value=httpx.Response(200, content=PLATFORM_AUDIO)
    )
    # No tenant bound ⇒ resolve_provider_slot returns the platform slot without a call.
    parts = await _synthesize(_parts())

    assert platform.call_count == 1
    _assert_platform_request(platform.calls[0].request)
    assert not resolve.called
    assert base64.b64decode(parts[0]["audio_base64"]) == PLATFORM_AUDIO


@respx.mock(assert_all_called=False)
async def test_enforce_tenant_slot_hits_the_tenant_provider_with_bearer_only(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(SWITCH_ENV, "enforce")
    platform = respx_mock.post(_platform_url()).mock(
        return_value=httpx.Response(200, content=PLATFORM_AUDIO)
    )
    tenant = respx_mock.post(f"https://{PUBLIC_IP}/v1/audio/speech").mock(
        return_value=httpx.Response(200, content=_wav(), headers={"content-type": "audio/wav"})
    )
    stub = _StubSlots(
        ProviderSlot(
            slot="tts",
            source="tenant",
            kind="openai-compatible",
            base_url="https://tts.tenant.example/v1",
            model="tenant-tts-1",
            secret=SecretStr(TENANT_SECRET),
        )
    )
    set_provider_slot_client(stub)
    set_identity_context(TENANT, "lic-1", "subject-1", tenant_source=TenantSource.LICENSE_TOKEN)

    parts = await _synthesize(_parts())

    assert not platform.called, "a tenant slot must not touch the platform TTS"
    assert stub.calls == ["tts"]
    assert tenant.call_count == 1
    req = tenant.calls[0].request
    assert req.headers["host"] == "tts.tenant.example"
    assert req.headers["authorization"] == f"Bearer {TENANT_SECRET}"
    for header in ("x-tenant-id", "x-license-id", "x-subject-id", "x-w2v-cell-token"):
        assert header not in req.headers, f"{header} leaked to the tenant provider"
    assert LICENCE not in str(req.headers)
    body = json.loads(req.content)
    assert body["input"] == "Welcome to the tour."
    assert body["model"] == "tenant-tts-1"
    assert base64.b64decode(parts[0]["audio_base64"]).startswith(b"RIFF")
    assert "degraded" not in parts[0]
