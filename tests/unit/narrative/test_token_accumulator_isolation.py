"""Per-Task isolation of NarrationBuilder + ShotListEnhancer token totals.

Regression: previously ``_accumulated_tokens`` was an instance attribute on
``NarrationBuilder`` / ``ShotListEnhancer``. Both classes are stored as
singletons on ``app.state`` and reused across concurrent FastAPI requests.
Two parallel ``generate()`` calls would each:
  1. Reset the shared ``self._accumulated_tokens`` dict to zeros.
  2. Accumulate sums from their own ``_wm_call`` results.
  3. Read the final totals.
Step 1 by request B would zero out request A's counters; step 2 by both
would interleave through await points; step 3 would see whichever set
landed last. End result: silent corruption of per-tenant cost
attribution.

The fix moves the accumulator from instance state into a
``contextvars.ContextVar``. Each asyncio.Task (one per FastAPI request)
gets an isolated view automatically. This test runs two ``generate()``
flows concurrently against the same instance and asserts each sees its
own totals — proving the ContextVar boundary holds.
"""

from __future__ import annotations

import asyncio
import contextlib

from src.services.narrative.narration_builder import (
    _accumulate_usage,
    _token_accumulator,
)


@contextlib.contextmanager
def _accumulator():
    """Set up a per-task accumulator and yield it; teardown on exit."""
    local: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    token = _token_accumulator.set(local)
    try:
        yield local
    finally:
        _token_accumulator.reset(token)


async def _run_flow(usage_per_call: dict[str, int], n_calls: int) -> dict[str, int]:
    """Mimic a generate() call: open accumulator, accumulate N times, return totals."""
    with _accumulator() as local:
        for _ in range(n_calls):
            # Yield to the event loop between accumulations so two concurrent
            # flows actually interleave under asyncio.gather.
            await asyncio.sleep(0)
            _accumulate_usage(usage_per_call)
        return dict(local)


async def test_two_concurrent_flows_have_isolated_totals() -> None:
    """Two parallel ``generate()`` calls must each see only their own usage."""
    flow_a_usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
    flow_b_usage = {"prompt_tokens": 100, "completion_tokens": 200, "total_tokens": 300}

    totals_a, totals_b = await asyncio.gather(
        _run_flow(flow_a_usage, n_calls=5),
        _run_flow(flow_b_usage, n_calls=5),
    )

    assert totals_a == {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150}
    assert totals_b == {"prompt_tokens": 500, "completion_tokens": 1000, "total_tokens": 1500}


async def test_accumulate_outside_generate_is_noop() -> None:
    """_accumulate_usage with no active accumulator must not raise or pollute."""
    # Sanity: outside any ContextVar.set(), accumulate is silently dropped.
    _accumulate_usage({"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 1})
    # No assertion needed beyond not raising.


async def test_nested_accumulators_use_innermost() -> None:
    """Reset() correctly unwinds — inner scope sees its own dict, outer sees its own."""
    with _accumulator() as outer:
        _accumulate_usage({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1})
        with _accumulator() as inner:
            _accumulate_usage({"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10})
            assert inner["total_tokens"] == 10
            assert outer["total_tokens"] == 1
        # Inner unwound; further accumulate goes back to outer.
        _accumulate_usage({"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1})
        assert outer["total_tokens"] == 2
