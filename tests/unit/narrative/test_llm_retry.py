"""Tests for shared LLM retry utility (post-WMClient migration)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.services.narrative.llm_retry import (
    LLMCallResult,
    LLMExhaustedError,
    LLMRetryConfig,
    call_llm_with_retry,
)


def _make_mock_wm(responses: list[str | Exception]) -> MagicMock:
    """Create a mock WMClient that returns/raises from a list of responses.

    Each non-exception entry becomes a WMResponse-shaped MagicMock returned
    from ``wm.message``. Exceptions are raised on the corresponding call.
    """
    wm = MagicMock()
    side_effects = []
    for r in responses:
        if isinstance(r, Exception):
            side_effects.append(r)
        else:
            mock_response = MagicMock()
            mock_response.output = r
            mock_response.usage_metrics = None
            mock_response.metadata = None
            side_effects.append(mock_response)

    wm.message = AsyncMock(side_effect=side_effects)
    return wm


class TestCallLlmWithRetry:
    """Tests for call_llm_with_retry()."""

    async def test_success_on_first_attempt(self):
        """Returns result on first successful call."""
        wm = _make_mock_wm(["hello world"])
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        result = await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert isinstance(result, LLMCallResult)
        assert result.content == "hello world"
        assert result.attempts_used == 1
        assert result.errors == []

    async def test_success_after_retry(self):
        """Returns result after first attempt fails."""
        wm = _make_mock_wm([ValueError("bad"), "success"])
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        result = await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert result.content == "success"
        assert result.attempts_used == 2
        assert len(result.errors) == 1

    async def test_exhaustion_raises_error(self):
        """Raises LLMExhaustedError when all attempts fail."""
        wm = _make_mock_wm(
            [
                ValueError("err1"),
                ValueError("err2"),
                ValueError("err3"),
            ]
        )
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        with pytest.raises(LLMExhaustedError) as exc_info:
            await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert exc_info.value.attempts_used == 3
        assert isinstance(exc_info.value.last_error, ValueError)
        assert exc_info.value.total_elapsed_seconds > 0

    async def test_error_correction_prompt_used(self):
        """Uses error correction prompt on retry."""
        wm = _make_mock_wm([ValueError("bad json"), "fixed"])
        config = LLMRetryConfig(
            max_attempts=3,
            timeout_seconds=10,
            error_correction_prompt="Fix the JSON output:",
            service_name="test",
        )

        result = await call_llm_with_retry(wm, "worker-x", "sys", "original prompt", config)
        assert result.content == "fixed"

        # Verify second call used error correction prompt
        calls = wm.message.call_args_list
        second_prompt = calls[1].kwargs["message"]
        assert "Fix the JSON output:" in second_prompt

    async def test_empty_response_raises(self):
        """Empty WM response triggers retry."""
        mock_response = MagicMock()
        mock_response.output = ""
        mock_response.usage_metrics = None
        mock_response.metadata = None

        wm = MagicMock()
        wm.message = AsyncMock(return_value=mock_response)
        config = LLMRetryConfig(max_attempts=1, timeout_seconds=10, service_name="test")

        with pytest.raises(LLMExhaustedError):
            await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

    async def test_httpx_error_is_retried(self):
        """A transient httpx.HTTPError (the dominant WM failure) is retried, not escaped."""
        wm = _make_mock_wm(
            [
                httpx.ConnectError("connection refused"),
                "recovered",
            ]
        )
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        result = await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert result.content == "recovered"
        assert result.attempts_used == 2

    async def test_httpx_status_error_exhausts_into_llmexhausted(self):
        """httpx.HTTPStatusError on every attempt raises LLMExhaustedError (job can fail),
        rather than propagating raw and wedging the caller's job in ``processing``."""
        req = httpx.Request("POST", "https://wm.test/message")
        resp = httpx.Response(503, request=req)
        wm = _make_mock_wm(
            [
                httpx.HTTPStatusError("503", request=req, response=resp),
                httpx.HTTPStatusError("503", request=req, response=resp),
                httpx.HTTPStatusError("503", request=req, response=resp),
            ]
        )
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        with pytest.raises(LLMExhaustedError) as exc_info:
            await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert exc_info.value.attempts_used == 3
        assert isinstance(exc_info.value.last_error, httpx.HTTPStatusError)

    async def test_metadata_in_result(self):
        """Result includes timing and attempt metadata."""
        wm = _make_mock_wm(["result"])
        config = LLMRetryConfig(max_attempts=3, timeout_seconds=10, service_name="test")

        result = await call_llm_with_retry(wm, "worker-x", "sys", "prompt", config)

        assert result.total_elapsed_seconds >= 0
        assert result.attempts_used == 1
