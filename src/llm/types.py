"""LLM provider protocol and request/response models."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel


class LLMRequest(BaseModel):
    """Request payload for an LLM completion."""

    system_prompt: str
    user_prompt: str
    # 8000, NOT 4000: 4000 is a recorded production failure value. The model
    # behind worker_management is a reasoning model that needs roughly 6000
    # tokens of thinking before it emits any response at all, and max_tokens is
    # a CAP on that whole budget — too low and the call dies with a terminal
    # 422 before producing output. Prompt headroom is not the binding
    # constraint: the live model (Qwen/Qwen3.8-27B-FP8) is served with
    # --max-model-len=131072. Callers may still override per-request.
    max_tokens: int = 8000
    temperature: float = 0.2


class LLMResponse(BaseModel):
    """Response from an LLM completion."""

    content: str
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model: str = ""
    provider: str = ""
    latency_seconds: float = 0.0


@runtime_checkable
class LLMProvider(Protocol):
    """Protocol for LLM providers."""

    async def complete(self, request: LLMRequest) -> LLMResponse: ...

    @property
    def name(self) -> str: ...
