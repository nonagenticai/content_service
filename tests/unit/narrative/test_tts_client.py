"""Tests for TTSClient (inherits BaseServiceClient).

Tests use ``patch.object`` instead of direct attribute assignment to avoid
ty ``invalid-assignment`` errors from monkey-patching typed attributes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.services.narrative.tts_client import TTSClient

_BASE_URL = "http://tts.test:6194"


class TestTTSClientInit:
    """Constructor sanity checks."""

    def test_defaults(self) -> None:
        client = TTSClient(base_url=_BASE_URL)
        assert client.base_url == _BASE_URL
        assert client.service_name == "tts_service"
        assert client.timeout == 60.0
        assert client.license_token is None

    def test_custom_timeout_and_token(self) -> None:
        client = TTSClient(base_url=_BASE_URL, timeout=120.0, license_token="tok-123")
        assert client.timeout == 120.0
        assert client.license_token == "tok-123"


class TestTTSClientGenerate:
    """Tests for generate() which uses _request_raw internally."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_generate_success(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"fake audio bytes"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            result = await self.client.generate("Hello world")
            assert result == b"fake audio bytes"

            mock_raw.assert_awaited_once()
            call_args = mock_raw.call_args
            assert call_args[0] == ("POST", "/api/tts/synthesize")
            payload = call_args[1]["json"]
            assert payload["text"] == "Hello world"
            assert payload["provider"] == "kokoro"
            assert payload["voice"] == "af_heart"
            assert payload["speed"] == 1.0
            assert payload["output_format"] == "wav"
            # The live tts_backend contract has NO async-job surface.
            assert "async_mode" not in payload

    @pytest.mark.asyncio
    async def test_generate_custom_voice_and_provider(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"custom audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            result = await self.client.generate(
                "Hello", voice="en_male", provider="elevenlabs", speed=1.5
            )
            assert result == b"custom audio"

            payload = mock_raw.call_args[1]["json"]
            assert payload["voice"] == "en_male"
            assert payload["provider"] == "elevenlabs"
            assert payload["speed"] == 1.5

    @pytest.mark.asyncio
    async def test_generate_empty_response_raises(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b""
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ):
            with pytest.raises(RuntimeError, match="empty response"):
                await self.client.generate("Hello")

    @pytest.mark.asyncio
    async def test_generate_propagates_transport_error(self) -> None:
        with patch.object(
            self.client,
            "_request_raw",
            new_callable=AsyncMock,
            side_effect=httpx.ConnectError("Connection refused"),
        ):
            with pytest.raises(httpx.ConnectError):
                await self.client.generate("Hello")

    @pytest.mark.asyncio
    async def test_generate_propagates_http_error(self) -> None:
        with patch.object(
            self.client,
            "_request_raw",
            new_callable=AsyncMock,
            side_effect=httpx.HTTPStatusError(
                "Bad Request",
                request=MagicMock(),
                response=MagicMock(status_code=400),
            ),
        ):
            with pytest.raises(httpx.HTTPStatusError):
                await self.client.generate("Hello")


class TestTTSClientGenerateBatch:
    """Tests for generate_batch()."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_batch_all_succeed(self) -> None:
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            results = await self.client.generate_batch([("item1", "Hello"), ("item2", "World")])
            assert results == {"item1": b"audio", "item2": b"audio"}
            assert mock_raw.await_count == 2

    @pytest.mark.asyncio
    async def test_batch_partial_failure(self) -> None:
        """Batch continues on partial failure, returning only successes."""
        call_count = 0

        async def mock_generate(
            text: str,
            voice: str = "af_heart",
            provider: str = "kokoro",
            speed: float = 1.0,
            output_format: str = "wav",
        ) -> bytes:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("TTS failed for item 2")
            return b"audio"

        with patch.object(self.client, "generate", side_effect=mock_generate):
            results = await self.client.generate_batch(
                [("item1", "Hello"), ("item2", "Fail"), ("item3", "World")]
            )
            assert "item1" in results
            assert "item2" not in results
            assert "item3" in results

    @pytest.mark.asyncio
    async def test_batch_passes_voice_and_provider(self) -> None:
        """generate_batch forwards voice/provider/speed to generate."""
        mock_response = MagicMock(spec=httpx.Response)
        mock_response.content = b"audio"
        mock_response.status_code = 200

        with patch.object(
            self.client, "_request_raw", new_callable=AsyncMock, return_value=mock_response
        ) as mock_raw:
            await self.client.generate_batch(
                [("item1", "Hello")],
                voice="custom_voice",
                provider="openai",
                speed=2.0,
            )
            payload = mock_raw.call_args[1]["json"]
            assert payload["voice"] == "custom_voice"
            assert payload["provider"] == "openai"
            assert payload["speed"] == 2.0


class TestTTSClientHealthCheck:
    """Tests for health_check() inherited from BaseServiceClient."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_health_check_healthy(self) -> None:
        mock_client = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"uptime": 1234}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        with patch.object(
            self.client, "_get_client", new_callable=AsyncMock, return_value=mock_client
        ):
            result = await self.client.health_check()
            assert result["status"] == "healthy"
            assert result["service"] == "tts_service"
            assert result["uptime"] == 1234

    @pytest.mark.asyncio
    async def test_health_check_unhealthy(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch.object(
            self.client, "_get_client", new_callable=AsyncMock, return_value=mock_client
        ):
            result = await self.client.health_check()
            assert result["status"] == "unhealthy"
            assert result["service"] == "tts_service"
            assert "Connection refused" in result["error"]


class TestTTSClientClose:
    """Tests for the close() lifecycle method."""

    def setup_method(self) -> None:
        self.client = TTSClient(base_url=_BASE_URL)

    @pytest.mark.asyncio
    async def test_close_when_no_client(self) -> None:
        await self.client.close()

    @pytest.mark.asyncio
    async def test_close_closes_underlying_client(self) -> None:
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.aclose = AsyncMock()
        self.client._client = mock_client

        await self.client.close()
        mock_client.aclose.assert_awaited_once()
        assert self.client._client is None
