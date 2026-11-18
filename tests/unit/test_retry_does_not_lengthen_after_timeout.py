"""A retry after a TIMEOUT must not send a LONGER prompt.

`llm_retry` prepends `error_correction_prompt` + the previous error + a
"CRITICAL: Output ONLY valid JSON" trailer before retrying. That is right for a
malformed answer — the model produced something unparseable and needs telling.

It is actively harmful for a `TimeoutError`: the call already did not fit its
budget, and the retry then sends the original prompt PLUS the correction preamble
PLUS the error text, under the SAME budget. More input, same clock — strictly
likelier to time out again. A retry that cannot succeed.

This matters more since the pgx model swap (2026-08-18) made the LLM path 5-23x
slower: `TimeoutError` is now a common branch here, not a rare one.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.services.narrative.llm_retry import LLMRetryConfig, call_llm_with_retry


def _cfg() -> LLMRetryConfig:
    return LLMRetryConfig(
        max_attempts=2,
        error_correction_prompt="You returned invalid JSON. Fix it.",
        backoff_base_seconds=0.0,
        service_name="test",
        timeout_seconds=5,
    )


class _Recorder:
    """Captures the prompt of every attempt, then fails it."""

    def __init__(self, exc: BaseException) -> None:
        self.prompts: list[str] = []
        self._exc = exc

    async def message(self, **kwargs: object) -> object:
        self.prompts.append(str(kwargs.get("message", "")))
        raise self._exc


@pytest.mark.asyncio
async def test_a_timeout_retry_reuses_the_original_prompt() -> None:
    rec = _Recorder(TimeoutError("deadline exceeded"))
    wm = AsyncMock()
    wm.message = rec.message

    with pytest.raises(Exception):  # noqa: B017 — exhaustion is expected; the prompts are the point
        await call_llm_with_retry(
            wm=wm, worker_name="w", system_prompt="sys", prompt="ORIGINAL", config=_cfg()
        )

    assert len(rec.prompts) == 2, "the retry must still happen"
    assert rec.prompts[0] == rec.prompts[1], (
        "attempt 2 sent a LONGER prompt after a timeout — more input under the "
        f"same clock, so the retry cannot succeed:\n{rec.prompts[1][:200]}"
    )


@pytest.mark.asyncio
async def test_a_malformed_answer_still_gets_the_correction() -> None:
    """The correction block must survive for the failure it was written for."""
    rec = _Recorder(ValueError("not valid JSON"))
    wm = AsyncMock()
    wm.message = rec.message

    with pytest.raises(Exception):  # noqa: B017
        await call_llm_with_retry(
            wm=wm, worker_name="w", system_prompt="sys", prompt="ORIGINAL", config=_cfg()
        )

    assert len(rec.prompts) == 2
    assert "You returned invalid JSON" in rec.prompts[1], (
        "a parse failure is exactly what the correction prompt exists for"
    )
    assert len(rec.prompts[1]) > len(rec.prompts[0])
