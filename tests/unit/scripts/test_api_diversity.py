"""Tests for Playwright API diversity validation and retry logic.

Verifies that ``_count_api_types`` correctly categorises API usage and that
``GenerationService.generate()`` retries when diversity is below threshold.
"""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

from src.llm.types import LLMResponse
from src.services.scripts.generation_service import (
    _MIN_API_TYPES,
    GenerationService,
    _count_api_types,
)

# ---------------------------------------------------------------------------
# Sample scripts
# ---------------------------------------------------------------------------

DIVERSE_SCRIPT = """\
import { test } from '@playwright/test';
import { safeHighlight, scrollToElement, safeClick, slowHover, annotateAndWait, pause, waitForVisualStability } from '../helpers/demo-helpers';
test.describe('Demo', () => {
  test('demo', async ({ page }) => {
    await page.goto('https://example.com');
    await waitForVisualStability(page);
    await scrollToElement(page, '.features');
    await pause(page, 500);
    await safeHighlight(page, '.feature-1', 'Feature');
    await safeClick(page, '.cta-button');
    await slowHover(page, '.nav-link');
    await annotateAndWait(page, 'Key feature', 3000);
  });
});
"""

MINIMAL_SCRIPT = """\
import { test } from '@playwright/test';
test.describe('Demo', () => {
  test('demo', async ({ page }) => {
    await page.goto('https://example.com');
    await page.waitForTimeout(1000);
  });
});
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_response(content: str, *, tokens: int = 100, latency: float = 0.1) -> LLMResponse:
    """Build a minimal ``LLMResponse``."""
    return LLMResponse(
        content=content,
        tokens_used=tokens,
        latency_seconds=latency,
        provider="test-provider",
    )


def _make_service(side_effects: list[LLMResponse]) -> GenerationService:
    """Build a ``GenerationService`` backed by a mock LLM with preset responses."""
    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(side_effect=side_effects)
    mock_llm.name = "mock-llm"

    mock_prompt_builder = MagicMock()
    mock_prompt_builder.with_overrides.return_value = mock_prompt_builder
    mock_prompt_builder.build.return_value = "test prompt"

    return GenerationService(llm=mock_llm, prompt_builder=mock_prompt_builder)


# ============================================================================
# _count_api_types tests
# ============================================================================


class TestCountApiTypes:
    def test_diverse_script(self) -> None:
        """A script with 6+ API types is counted correctly."""
        count, found, _missing = _count_api_types(DIVERSE_SCRIPT)
        assert count >= 6
        for category in (
            "navigation",
            "click",
            "scroll",
            "highlight_annotate",
            "pause_wait",
            "hover",
        ):
            assert category in found, f"expected '{category}' to be found"

    def test_minimal_script(self) -> None:
        """A script with only page.goto and page.waitForTimeout scores 2."""
        count, found, missing = _count_api_types(MINIMAL_SCRIPT)
        assert count == 2
        assert set(found) == {"navigation", "pause_wait"}
        assert "click" in missing
        assert "scroll" in missing


# ============================================================================
# GenerationService.generate() diversity retry tests
# ============================================================================


class TestGenerateDiversityRetries:
    """Validate that ``generate()`` retries when API diversity is insufficient."""

    async def test_generate_accepts_diverse_script_without_retry(self) -> None:
        """If the first gen+extract yields a diverse script, no retry happens.

        Expect exactly 2 LLM calls: 1 gen + 1 extract.
        """
        service = _make_service(
            [
                _llm_response("raw generation output"),
                _llm_response(DIVERSE_SCRIPT),
            ]
        )

        result = await service.generate(part={"id": "p1", "title": "Demo"})

        assert result.script_source.strip() != ""
        assert cast(AsyncMock, service._llm.complete).call_count == 2

    async def test_generate_retries_on_low_diversity(self) -> None:
        """First attempt produces a low-diversity script; second attempt passes.

        Expect exactly 4 LLM calls: 2 per attempt.
        """
        service = _make_service(
            [
                # Attempt 1: gen + extract → minimal script
                _llm_response("raw generation output"),
                _llm_response(MINIMAL_SCRIPT),
                # Attempt 2: gen + extract → diverse script
                _llm_response("raw generation output v2"),
                _llm_response(DIVERSE_SCRIPT),
            ]
        )

        result = await service.generate(part={"id": "p1", "title": "Demo"})

        assert cast(AsyncMock, service._llm.complete).call_count == 4
        # The accepted script should be the diverse one
        api_count, _, _ = _count_api_types(result.script_source)
        assert api_count >= _MIN_API_TYPES

    async def test_generate_exhausts_retries_uses_last_script(self) -> None:
        """All 3 attempts (1 initial + 2 retries) produce low diversity.

        Expect 6 LLM calls (3 attempts x 2 passes) and the last script is used.
        """
        service = _make_service(
            [
                # Attempt 1
                _llm_response("raw gen 1"),
                _llm_response(MINIMAL_SCRIPT),
                # Attempt 2
                _llm_response("raw gen 2"),
                _llm_response(MINIMAL_SCRIPT),
                # Attempt 3
                _llm_response("raw gen 3"),
                _llm_response(MINIMAL_SCRIPT),
            ]
        )

        result = await service.generate(part={"id": "p1", "title": "Demo"})

        assert cast(AsyncMock, service._llm.complete).call_count == 6
        # Should still return a result (the last attempt), even though diversity is low
        assert result.script_source.strip() != ""
        api_count, _, _ = _count_api_types(result.script_source)
        assert api_count < _MIN_API_TYPES
