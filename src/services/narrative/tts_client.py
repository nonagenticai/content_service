"""Async HTTP client for the TTS microservice.

Calls the live TTS backend (``tts.test:9202`` / in-cluster
``modality-gateway``) for audio synthesis. Inherits retry, connection pooling,
and health-check from ``BaseServiceClient`` in w2v_common.

The backend is a **sync** service: ``POST /api/tts/synthesize`` returns raw
``audio/wav`` bytes. ``output_format`` accepts ONLY ``"wav"`` (tts_backend #55
answers 422 to ``"mp3"``, which it never produced). There is no async-job surface — no
``/api/tts/generate`` and no ``async_mode`` field (that was the unwired
``tts_service`` gateway contract, which prod does not route).
"""

from __future__ import annotations

import logging
import time
from typing import Literal

from w2v_common.auth.tenant_licence_lane import TenantLicenceLane
from w2v_common.clients.base import BaseServiceClient

logger = logging.getLogger(__name__)


class TTSClient(BaseServiceClient):
    """Async HTTP client for the TTS microservice.

    Inherits retry logic (3x on 429/500/502/503/504), a shared
    ``httpx.AsyncClient`` connection pool, and ``health_check()`` from
    ``BaseServiceClient``.
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 60.0,
        license_token: str | None = None,
        licence_lane: TenantLicenceLane | None = None,
    ) -> None:
        # ``licence_lane`` (MODALITY_GATEWAY_AUTH_MODE): in tenant_licence mode the
        # pooled client carries the bound tenant's minted licence on every request
        # and ``license_token`` is not sent; shared/None = unchanged.
        super().__init__(
            base_url=base_url,
            service_name="tts_service",
            timeout=timeout,
            license_token=license_token,
            licence_lane=licence_lane,
        )

    async def generate(
        self,
        text: str,
        voice: str = "af_heart",
        provider: str = "kokoro",
        speed: float = 1.0,
        output_format: Literal["wav"] = "wav",
    ) -> bytes:
        """Generate audio via POST to the TTS backend ``/api/tts/synthesize``.

        Returns raw ``audio/wav`` bytes. ``"mp3"`` is rejected with 422 by the
        backend (modality-gateway forwards the body unchanged), so it is not
        representable here.
        """
        payload = {
            "text": text,
            "provider": provider,
            "voice": voice,
            "speed": speed,
            "output_format": output_format,
        }

        t0 = time.monotonic()
        logger.info("TTS generate: starting, voice=%s, text_len=%d", voice, len(text))

        response = await self._request_raw("POST", "/api/tts/synthesize", json=payload)

        if not response.content:
            raise RuntimeError("TTS service returned empty response")

        elapsed = time.monotonic() - t0
        logger.info(
            "TTS generate: completed in %.2fs, %d bytes",
            elapsed,
            len(response.content),
        )
        return response.content

    async def generate_batch(
        self,
        items: list[tuple[str, str]],
        voice: str = "af_heart",
        provider: str = "kokoro",
        speed: float = 1.0,
    ) -> dict[str, bytes]:
        """Generate multiple audio items.

        Args:
            items: List of (item_id, text) tuples.
            voice: TTS voice identifier.
            provider: TTS provider name.
            speed: Playback speed multiplier.

        Returns:
            Mapping of item_id to audio bytes.
        """
        results: dict[str, bytes] = {}
        for item_id, text in items:
            try:
                audio = await self.generate(text, voice=voice, provider=provider, speed=speed)
                results[item_id] = audio
            except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
                logger.warning("TTS batch item %s failed: %s", item_id, exc)
        return results
