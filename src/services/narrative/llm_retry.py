"""Shared LLM retry utility with error correction and backoff.

All calls route through worker_management — see :mod:`src.llm.wm_provider`
and memory ``wm-as-sole-llm-gateway.md``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx
from w2v_common.clients import WMClient

logger = logging.getLogger(__name__)


class LLMExhaustedError(Exception):
    """Raised when all LLM retry attempts have been exhausted."""

    def __init__(
        self,
        message: str,
        last_error: Exception | None = None,
        attempts_used: int = 0,
        total_elapsed_seconds: float = 0.0,
    ) -> None:
        super().__init__(message)
        self.last_error = last_error
        self.attempts_used = attempts_used
        self.total_elapsed_seconds = total_elapsed_seconds


@dataclass
class LLMRetryConfig:
    """Configuration for LLM retry behavior."""

    max_attempts: int = 3
    error_correction_prompt: str | None = None
    backoff_base_seconds: float = 0.0
    service_name: str = "llm"
    timeout_seconds: int = 90


@dataclass
class LLMCallResult:
    """Result of a successful LLM call."""

    content: str
    attempts_used: int = 1
    total_elapsed_seconds: float = 0.0
    errors: list[str] = field(default_factory=list)
    token_usage: dict[str, int] | None = None


async def call_llm_with_retry(
    wm: WMClient,
    worker_name: str,
    system_prompt: str,
    prompt: str,
    config: LLMRetryConfig,
    model_override: str | None = None,
) -> LLMCallResult:
    """Call WM with retry logic, error correction, and timeout.

    Args:
        wm: Connected :class:`WMClient` instance.
        worker_name: Stable WM worker name (auto-created on first call).
        system_prompt: System prompt steering the worker for this call.
        prompt: The user-side prompt to send.
        config: Retry configuration.
        model_override: Optional per-call model override.

    Returns:
        :class:`LLMCallResult` with the response content.

    Raises:
        LLMExhaustedError: If all attempts fail.
    """
    t0 = time.monotonic()
    current_prompt = prompt
    errors: list[str] = []
    last_error: Exception | None = None

    for attempt in range(1, config.max_attempts + 1):
        try:
            logger.info(
                "%s: WM call attempt %d/%d",
                config.service_name,
                attempt,
                config.max_attempts,
            )
            response = await asyncio.wait_for(
                wm.message(
                    worker_name=worker_name,
                    message=current_prompt,
                    system_prompt=system_prompt,
                    model_override=model_override,
                ),
                timeout=config.timeout_seconds,
            )
            output = response.output
            if isinstance(output, dict):
                # Provider returned structured output — keep the JSON for downstream parsing.
                import json as _json

                content = _json.dumps(output)
            else:
                content = str(output) if output is not None else ""
            if not content:
                raise ValueError("Empty response from WM")

            token_usage = None
            if response.usage_metrics:
                token_usage = {
                    "prompt_tokens": int(response.usage_metrics.get("prompt_tokens", 0) or 0),
                    "completion_tokens": int(
                        response.usage_metrics.get("completion_tokens", 0) or 0
                    ),
                    "total_tokens": int(response.usage_metrics.get("total_tokens", 0) or 0),
                }

            elapsed = time.monotonic() - t0
            return LLMCallResult(
                content=content,
                attempts_used=attempt,
                total_elapsed_seconds=elapsed,
                errors=errors,
                token_usage=token_usage,
            )

        except (httpx.HTTPError, OSError, TimeoutError, ValueError, RuntimeError) as exc:
            # httpx.HTTPError (RequestError + HTTPStatusError) is the dominant WM
            # failure mode — transient 5xx / timeouts from worker_management. Without
            # it, a WM HTTP error escaped the retry loop entirely and the caller's
            # job wedged in ``processing`` forever instead of retrying/failing.
            last_error = exc
            errors.append(f"Attempt {attempt}: {exc}")
            logger.warning(
                "%s: attempt %d/%d failed: %s",
                config.service_name,
                attempt,
                config.max_attempts,
                exc,
            )

            if attempt < config.max_attempts:
                if config.backoff_base_seconds > 0:
                    delay = config.backoff_base_seconds * attempt
                    await asyncio.sleep(delay)

                # ⚠️ Only lengthen the prompt for a failure a LONGER prompt can
                # fix. The correction block exists for a malformed answer — the
                # model produced something unparseable and needs telling. For a
                # TIMEOUT it is actively harmful: the call already did not fit in
                # its budget, and attempt 2 then sends the original prompt PLUS
                # the correction preamble PLUS the error text, under the SAME
                # budget. More input, same clock, strictly likelier to time out
                # again — a retry that cannot succeed.
                #
                # That matters more since the pgx model swap (2026-08-18): the
                # LLM path is 5-23x slower, so TimeoutError is now a common
                # branch here rather than a rare one.
                if config.error_correction_prompt and not isinstance(
                    exc, TimeoutError | asyncio.TimeoutError
                ):
                    current_prompt = (
                        f"{config.error_correction_prompt}\n\n"
                        f"Previous error: {exc}\n\n{prompt}\n\n"
                        "CRITICAL: Output ONLY valid JSON."
                    )

    elapsed = time.monotonic() - t0
    raise LLMExhaustedError(
        message=f"{config.service_name}: all {config.max_attempts} attempts exhausted",
        last_error=last_error,
        attempts_used=config.max_attempts,
        total_elapsed_seconds=elapsed,
    )
