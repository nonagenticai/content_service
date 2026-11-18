"""Extra coverage tests for TTSClient edge cases."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.narrative.tts_client import TTSClient

_BASE_URL = "http://tts.test:6194"


class TestTTSClientFormatEdgeCases:
    """Edge cases for output format and large text."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_generate_wav_format(self) -> None:
        """generate() sends the correct output_format."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"wav audio data"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            result = await self.client.generate("Hello", output_format="wav")
            assert result == b"wav audio data"
            payload = mock_raw.call_args[1]["json"]
            assert payload["output_format"] == "wav"

    @pytest.mark.asyncio
    async def test_generate_large_text(self) -> None:
        """generate() handles large text inputs."""
        large_text = "word " * 10000
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"large audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            result = await self.client.generate(large_text)
            assert result == b"large audio"
            payload = mock_raw.call_args[1]["json"]
            assert payload["text"] == large_text

    @pytest.mark.asyncio
    async def test_generate_hits_synthesize_without_async_mode(self) -> None:
        """generate() POSTs the live /api/tts/synthesize contract (no async_mode).

        The live tts_backend (tts.test:9202) / modality-gateway expose a
        sync ``POST /api/tts/synthesize`` with no ``async_mode`` field. The old
        ``/api/tts/generate`` + ``async_mode`` belonged to the unwired
        tts_service gateway and 404'd the live backend.
        """
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            await self.client.generate("Test")
            assert mock_raw.call_args[0] == ("POST", "/api/tts/synthesize")
            payload = mock_raw.call_args[1]["json"]
            assert "async_mode" not in payload


class TestTTSClientBatchEdgeCases:
    """Edge cases for TTSClient.generate_batch()."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_batch_empty_list(self) -> None:
        """generate_batch() returns empty dict for empty input."""
        results = await self.client.generate_batch([])
        assert results == {}

    @pytest.mark.asyncio
    async def test_batch_all_fail(self) -> None:
        """generate_batch() returns empty dict when all items fail."""

        async def mock_generate(
            text: str,
            voice: str = "af_heart",
            provider: str = "kokoro",
            speed: float = 1.0,
            output_format: str = "wav",
        ) -> bytes:
            raise RuntimeError("all fail")

        with patch.object(self.client, "generate", side_effect=mock_generate):
            results = await self.client.generate_batch([("a", "Hello"), ("b", "World")])
            assert results == {}

    @pytest.mark.asyncio
    async def test_batch_single_item(self) -> None:
        """generate_batch() works with a single item."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"single audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ):
            results = await self.client.generate_batch([("only", "Hello")])
            assert results == {"only": b"single audio"}


class TestTTSClientInheritance:
    """Verify BaseServiceClient inheritance."""

    def test_inherits_base_service_client(self) -> None:
        from w2v_common.clients.base import BaseServiceClient

        client = TTSClient(base_url=_BASE_URL)
        assert isinstance(client, BaseServiceClient)

    def test_service_name(self) -> None:
        client = TTSClient(base_url=_BASE_URL)
        assert client.service_name == "tts_service"

    def test_url_normalization(self) -> None:
        client = TTSClient(base_url="http://tts.test:6194/")
        assert client.base_url == "http://tts.test:6194"

    def test_initial_client_is_none(self) -> None:
        client = TTSClient(base_url=_BASE_URL)
        assert client._client is None
