"""WMLLMProvider.complete resilience — a malformed provider usage/metadata shape
must not fail an otherwise-successful generation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.llm.types import LLMRequest
from src.llm.wm_provider import WMLLMProvider


async def test_complete_survives_non_dict_usage_and_metadata() -> None:
    # A duck-typed WM response whose usage_metrics/metadata are NON-dict (a list):
    # complete() must not AttributeError — token accounting is non-critical.
    wm = AsyncMock()
    wm.message.return_value = SimpleNamespace(
        output="print('generated script')",
        usage_metrics=["not", "a", "dict"],
        metadata=["not", "a", "dict"],
    )
    provider = WMLLMProvider(wm_client=wm, worker_name="script-gen", system_prompt="sp")
    resp = await provider.complete(LLMRequest(system_prompt="sp", user_prompt="up"))
    assert resp.content == "print('generated script')"  # generation succeeded
    assert resp.tokens_used == 0  # malformed usage → zeroed, not a raise


async def test_complete_parses_valid_usage() -> None:
    wm = AsyncMock()
    wm.message.return_value = SimpleNamespace(
        output="ok",
        usage_metrics={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        metadata={"model": "local-llm"},
    )
    provider = WMLLMProvider(wm_client=wm, worker_name="script-gen", system_prompt="sp")
    resp = await provider.complete(LLMRequest(system_prompt="sp", user_prompt="up"))
    assert resp.tokens_used == 15
    assert resp.prompt_tokens == 10
    assert resp.model == "local-llm"


async def test_complete_forwards_max_tokens_to_wm() -> None:
    """``LLMRequest.max_tokens`` must reach ``WMClient.message``.

    It used to be silently dropped, so every call inherited WM's default of
    6000 — fatal for a reasoning model (terminal 422 "Model token limit
    exceeded before any response was generated") and it made the computed
    extraction budget in generation_service dead code.
    """
    wm = AsyncMock()
    wm.message.return_value = SimpleNamespace(output="ok", usage_metrics={}, metadata={})
    provider = WMLLMProvider(wm_client=wm, worker_name="script-gen", system_prompt="sp")

    await provider.complete(LLMRequest(system_prompt="sp", user_prompt="up", max_tokens=12345))

    assert wm.message.await_args.kwargs["max_tokens"] == 12345


async def test_complete_forwards_default_max_tokens() -> None:
    """The default is forwarded too — an unset max_tokens must NOT fall back to
    WM's failing 6000 default."""
    wm = AsyncMock()
    wm.message.return_value = SimpleNamespace(output="ok", usage_metrics={}, metadata={})
    provider = WMLLMProvider(wm_client=wm, worker_name="script-gen", system_prompt="sp")

    await provider.complete(LLMRequest(system_prompt="sp", user_prompt="up"))

    assert wm.message.await_args.kwargs["max_tokens"] == 8000
