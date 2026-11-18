"""LLM provider that routes through worker_management — the sole LLM gateway.

w2v services NEVER call openai/anthropic/inference-router directly. Every
LLM call goes through ``worker_management.POST /workers/{name}/message``
so cost is attributed per tenant and providers can be swapped server-side.
See memory ``wm-as-sole-llm-gateway.md`` for the canonical pattern.

This adapter exposes the same :class:`LLMProvider` Protocol the
script-generation pipeline already consumes, so the orchestrator,
generation_service, improvement_service, etc. don't need to learn a new
shape — only the wire transport changes.
"""

from __future__ import annotations

import logging
import time

import httpx
from w2v_common.clients import WMClient

from src.llm.types import LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


def _safe_int(val: object) -> int:
    """Coerce a provider-supplied token count to int; non-numeric ⇒ 0."""
    if isinstance(val, bool) or not isinstance(val, (int, float, str)):
        return 0
    try:
        return int(val)
    except TypeError, ValueError:
        return 0


class WMLLMProvider:
    """Implements :class:`src.llm.types.LLMProvider` against worker_management.

    Each instance is bound to a stable WM worker name (one worker per
    distinct LLM purpose, e.g. ``script-generator``, ``script-improver``).
    The underlying :class:`WMClient` is connection-pooled and the
    identity event hook reads request-scoped ContextVars so a single
    shared instance serves concurrent end-user requests correctly.
    """

    def __init__(
        self,
        *,
        worker_name: str,
        system_prompt: str,
        wm_client: WMClient | None = None,
        provider_name: str | None = None,
        model_override: str | None = None,
    ) -> None:
        self._worker_name = worker_name
        self._system_prompt = system_prompt
        self._wm = wm_client if wm_client is not None else WMClient()
        self._provider_name = provider_name or f"wm:{worker_name}"
        self._model_override = model_override

    @property
    def name(self) -> str:
        return self._provider_name

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """Forward the request to WM and adapt the response.

        ``LLMRequest.system_prompt`` overrides the constructor-bound
        ``system_prompt`` when set — generation_service uses different
        system prompts for the generation and extraction passes.
        """
        t0 = time.monotonic()
        system_prompt = request.system_prompt or self._system_prompt
        try:
            wm_response = await self._wm.message(
                worker_name=self._worker_name,
                message=request.user_prompt,
                system_prompt=system_prompt,
                model_override=self._model_override,
                # MUST be forwarded: when omitted, WM applies its own default of
                # 6000, and the model behind it is a REASONING model whose
                # thinking is charged against max_tokens. Running out mid-thought
                # raises a post-generation, TERMINAL (permanent, non-retried)
                # 422 "Model token limit (6000) exceeded before any response was
                # generated". Dropping this also made the deliberately computed
                # budget in generation_service._extract_typescript dead code.
                max_tokens=request.max_tokens,
            )
        except httpx.TimeoutException as exc:
            raise TimeoutError(f"WM call to {self._worker_name} timed out: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"WM returned {exc.response.status_code} for worker {self._worker_name}: "
                f"{exc.response.text[:200]}"
            ) from exc
        except httpx.TransportError as exc:
            raise RuntimeError(f"WM transport error for {self._worker_name}: {exc}") from exc
        except httpx.InvalidURL as exc:
            # A misconfigured discovery base URL — NOT an httpx.HTTPError, so it would
            # otherwise escape this guard and strand a paid script job in 'processing'.
            raise RuntimeError(f"WM invalid URL for {self._worker_name}: {exc}") from exc

        content = wm_response.output
        if isinstance(content, dict):
            # Provider returned structured output even though we didn't ask for it.
            # This shouldn't happen for the script pipeline (no output_schema set),
            # but be defensive — stringify so downstream code that expects text still works.
            import json

            content = json.dumps(content)
        if not isinstance(content, str):
            raise RuntimeError(
                f"WM worker {self._worker_name} returned non-text output of type "
                f"{type(content).__name__}; expected string completion"
            )

        # Defensive: usage_metrics/metadata are provider-shaped; a non-dict (a list,
        # a scalar) must NOT AttributeError this parse — token accounting is
        # non-critical and must never fail an otherwise-successful generation.
        usage = wm_response.usage_metrics if isinstance(wm_response.usage_metrics, dict) else {}
        prompt_tokens = _safe_int(usage.get("prompt_tokens", 0))
        completion_tokens = _safe_int(usage.get("completion_tokens", 0))
        tokens_total = _safe_int(usage.get("total_tokens", prompt_tokens + completion_tokens))
        elapsed = time.monotonic() - t0

        metadata = wm_response.metadata if isinstance(wm_response.metadata, dict) else {}
        model_used = str(metadata.get("model", "")) or self._model_override or ""

        logger.info(
            "WM LLM call: worker=%s model=%s tokens=%d elapsed=%.2fs",
            self._worker_name,
            model_used,
            tokens_total,
            elapsed,
        )
        return LLMResponse(
            content=content,
            tokens_used=tokens_total,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            model=model_used,
            provider=self._provider_name,
            latency_seconds=elapsed,
        )

    async def aclose(self) -> None:
        """Close the underlying WMClient."""
        await self._wm.aclose()
