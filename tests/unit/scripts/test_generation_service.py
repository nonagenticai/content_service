"""Tests for services.scripts.generation_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.domain.script import GeneratedScript
from src.llm.types import LLMResponse
from src.services.scripts.generation_service import (
    GenerationService,
    _count_api_types,
    _extract_fenced_code,
)


class TestExtractFencedCode:
    def test_mid_string_fence_with_prose(self):
        text = (
            "Here is the script:\n"
            "```typescript\n"
            'import { test } from "@playwright/test";\n'
            "```\n"
            "Hope this helps!"
        )
        out = _extract_fenced_code(text)
        assert out == 'import { test } from "@playwright/test";'
        assert "```" not in out
        assert "Here is" not in out
        assert "Hope this helps" not in out

    def test_bare_ts_fence(self):
        text = "```ts\nconst x = 1;\n```"
        assert _extract_fenced_code(text) == "const x = 1;"

    def test_no_fence_returns_stripped(self):
        text = "  const x = 1;  "
        assert _extract_fenced_code(text) == "const x = 1;"

    def test_stray_leading_fence_only(self):
        # No closing fence: fall back to stray-fence strip.
        text = "```typescript\nconst x = 1;"
        assert _extract_fenced_code(text) == "const x = 1;"


class TestCountApiTypes:
    def test_all_types_present(self):
        script = (
            'page.goto("url");\n'
            'slowClick(page, "sel");\n'
            'slowType(page, locator, "text");\n'
            'scrollToElement(page, "sel");\n'
            'highlightElement(page, "sel");\n'
            "pause(page, 2000);\n"
            'slowHover(page, "sel");\n'
            "page.keyboard.press('Enter');\n"
            "page.evaluate(() => {});\n"
        )
        count, _found, missing = _count_api_types(script)
        assert count == 9
        assert len(missing) == 0

    def test_no_types_present(self):
        count, found, missing = _count_api_types("const x = 1;")
        assert count == 0
        assert len(found) == 0
        assert len(missing) == 9

    def test_partial_types(self):
        script = 'page.goto("url");\nslowClick(page, "sel");'
        count, found, _missing = _count_api_types(script)
        assert count == 2
        assert "navigation" in found
        assert "click" in found


class TestGenerateScript:
    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.name = "test-llm"
        return llm

    @pytest.fixture
    def mock_prompt_builder(self):
        builder = MagicMock()
        builder.with_overrides.return_value = builder
        builder.build.return_value = "generated prompt"
        return builder

    async def test_generate_basic(self, mock_llm, mock_prompt_builder):
        # Provide a script with enough API diversity to pass on first attempt
        rich_script = (
            'import { test } from "@playwright/test";\n'
            'page.goto("url"); slowClick(page, "s"); slowType(page, l, "t");\n'
            'scrollToElement(page, "s"); highlightElement(page, "s");\n'
            "pause(page, 1000); slowHover(page, 's');\n"
            "page.keyboard.press('Enter'); page.evaluate(() => {});\n"
        )
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content="```typescript\n" + rich_script + "\n```",
                    tokens_used=100,
                    provider="test",
                    latency_seconds=0.5,
                ),
                LLMResponse(
                    content=rich_script,
                    tokens_used=50,
                    provider="test",
                    latency_seconds=0.3,
                ),
            ]
        )
        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = await svc.generate(part={"id": "p1"})

        assert isinstance(result, GeneratedScript)
        assert "import" in result.script_source
        assert result.llm_tokens_used == 150
        assert result.provider_used == "test"

    async def test_generate_retries_on_low_diversity(self, mock_llm, mock_prompt_builder):
        low_diversity = (
            'import { test } from "@playwright/test";\npage.goto("url"); slowClick(page, "s");\n'
        )
        high_diversity = (
            'import { test } from "@playwright/test";\n'
            'page.goto("url"); slowClick(page, "s"); slowType(page, l, "t");\n'
            'scrollToElement(page, "s"); highlightElement(page, "s");\n'
            "pause(page, 1000); slowHover(page, 's');\n"
            "page.keyboard.press('Enter');\n"
        )

        # First attempt returns low diversity, second returns high
        mock_llm.complete = AsyncMock(
            side_effect=[
                # Attempt 1 generation
                LLMResponse(
                    content=low_diversity, tokens_used=50, provider="test", latency_seconds=0.1
                ),
                # Attempt 1 extraction
                LLMResponse(
                    content=low_diversity, tokens_used=50, provider="test", latency_seconds=0.1
                ),
                # Attempt 2 generation
                LLMResponse(
                    content=high_diversity, tokens_used=50, provider="test", latency_seconds=0.1
                ),
                # Attempt 2 extraction
                LLMResponse(
                    content=high_diversity, tokens_used=50, provider="test", latency_seconds=0.1
                ),
            ]
        )

        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = await svc.generate(part={"id": "p1"})
        assert result.llm_tokens_used == 200  # All 4 calls

    # --- Regression: pass-2 extraction must not silently truncate large files ---

    _RICH_SCRIPT = (
        'import { test } from "@playwright/test";\n'
        'page.goto("url"); slowClick(page, "s"); slowType(page, l, "t");\n'
        'scrollToElement(page, "s"); highlightElement(page, "s");\n'
        "pause(page, 1000); slowHover(page, 's');\n"
        "page.keyboard.press('Enter'); page.evaluate(() => {});\n"
    )

    async def test_large_completion_skips_extraction_llm(self, mock_llm, mock_prompt_builder):
        """A pass-1 completion whose re-emission budget exceeds worker_management's
        16384 output cap must SKIP the extraction LLM (which would truncate mid-file) and return
        the FULL structurally fence-stripped pass-1 body — not a validated
        fragment, and NOT with mid-string preamble/postamble prose leaking in.

        The pass-1 output here wraps the code in a ```ts fence with prose BEFORE
        and AFTER it: the old anchored ``^```/```$`` strip left those fences
        mid-string and shipped broken TS. Structural extraction must ship code
        only.
        """
        fenced = (
            "Here is the script:\n```typescript\n" + self._RICH_SCRIPT + "\n```\nHope this helps!"
        )
        # This test previously pinned the BUG: it used completion=6000 against a
        # stale ``_VLLM_MAX_MODEL_LEN = 8192`` (retired Nemotron), so 6000+6512 >
        # 8192 skipped. The live ctx is 131072, so 6000 now round-trips fine and
        # must NOT skip. The real bound is WM's 16384 output cap, so use a
        # completion whose budget exceeds it: 16000 + 512 = 16512 > 16384 -> skip.
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=fenced,
                    tokens_used=16000,
                    completion_tokens=16000,
                    provider="test",
                    latency_seconds=0.5,
                ),
            ]
        )
        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = await svc.generate(part={"id": "p1"})

        # Extraction LLM was NOT called (a 2nd call would raise StopIteration).
        assert mock_llm.complete.call_count == 1
        # Full code body shipped, and NO fence markers or surrounding prose.
        assert "page.evaluate" in result.script_source
        assert "```" not in result.script_source
        assert "Here is" not in result.script_source
        assert "Hope this helps" not in result.script_source
        # Passes validation (would have raised otherwise).
        GenerationService._validate_extracted_code(result.script_source)
        assert result.llm_tokens_used == 16000  # only pass-1 counted

    async def test_extraction_max_tokens_scales_to_completion(self, mock_llm, mock_prompt_builder):
        """When the round-trip fits, max_tokens must scale to the pass-1
        completion (+ margin), never a hard 4000 that would truncate a
        2000-token completion's re-emission budget.
        """
        fenced = "```typescript\n" + self._RICH_SCRIPT + "\n```"
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=fenced,
                    tokens_used=2500,
                    completion_tokens=2000,
                    provider="test",
                    latency_seconds=0.5,
                ),
                LLMResponse(
                    content=self._RICH_SCRIPT,
                    tokens_used=1900,
                    completion_tokens=1900,
                    provider="test",
                    latency_seconds=0.3,
                ),
            ]
        )
        svc = GenerationService(mock_llm, mock_prompt_builder)
        await svc.generate(part={"id": "p1"})

        assert mock_llm.complete.call_count == 2
        extract_request = mock_llm.complete.call_args_list[1].args[0]
        # 2000 (completion) + 512 (margin), not the old hard 4000 cap.
        assert extract_request.max_tokens == 2512

    async def test_midsize_completion_now_round_trips_and_is_capped_by_wm(
        self, mock_llm, mock_prompt_builder
    ):
        """A 6000-token completion must actually USE the extraction LLM.

        Under the stale ``_VLLM_MAX_MODEL_LEN = 8192`` (retired Nemotron) this
        was skipped: 6000 + 6512 > 8192. The live context window is 131072, so
        the round-trip fits and pass-2 must run. Its budget is bounded by
        worker_management's 16384 output cap, NOT by the context window —
        sending more is a TERMINAL 422 from WM's request validator.
        """
        fenced = "```typescript\n" + self._RICH_SCRIPT + "\n```"
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=fenced,
                    tokens_used=6000,
                    completion_tokens=6000,
                    provider="test",
                    latency_seconds=0.5,
                ),
                LLMResponse(
                    content=self._RICH_SCRIPT,
                    tokens_used=5900,
                    completion_tokens=5900,
                    provider="test",
                    latency_seconds=0.3,
                ),
            ]
        )
        svc = GenerationService(mock_llm, mock_prompt_builder)
        await svc.generate(part={"id": "p1"})

        assert mock_llm.complete.call_count == 2  # NOT skipped any more
        extract_request = mock_llm.complete.call_args_list[1].args[0]
        assert extract_request.max_tokens == 6512  # 6000 + margin, under the cap
        # Whatever the budget, it can never exceed what WM will accept.
        assert extract_request.max_tokens <= 16384

    async def test_truncated_extraction_falls_back_to_pass1(self, mock_llm, mock_prompt_builder):
        """If the extraction LLM returns a body far shorter than the fence-
        stripped pass-1 CODE baseline (structural truncation — LLMResponse has
        no finish_reason), fall back to the clean baseline rather than validate a
        fragment. Pass-1 wraps the code in prose+fence, so the fallback must be
        the fence-stripped code, never the raw prose-laden content.
        """
        fenced = (
            "Sure! Here is your Playwright script:\n"
            "```typescript\n" + self._RICH_SCRIPT + "\n```\n"
            "Let me know if you need changes."
        )
        mock_llm.complete = AsyncMock(
            side_effect=[
                LLMResponse(
                    content=fenced,
                    tokens_used=200,
                    completion_tokens=100,
                    provider="test",
                    latency_seconds=0.5,
                ),
                # Truncated extraction: keeps the import (would pass validation)
                # but drops the rest of the file — well under 90% of baseline.
                LLMResponse(
                    content='import { test } from "@playwright/test";',
                    tokens_used=20,
                    completion_tokens=10,
                    provider="test",
                    latency_seconds=0.3,
                ),
            ]
        )
        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = await svc.generate(part={"id": "p1"})

        # Fell back to the full pass-1 CODE, not the truncated fragment...
        assert "page.evaluate" in result.script_source
        assert "slowHover" in result.script_source
        # ...and with NO fence/prose leak from the raw content.
        assert "```" not in result.script_source
        assert "Here is your Playwright script" not in result.script_source
        assert "Let me know" not in result.script_source


class TestGenerateTitleCard:
    def test_generates_title_card_script(self):
        mock_llm = AsyncMock()
        mock_prompt_builder = MagicMock()
        svc = GenerationService(mock_llm, mock_prompt_builder)

        part = {
            "id": "title-1",
            "title": "Welcome",
            "subtitle": "To the demo",
            "chapter": 1,
            "estimated_duration_seconds": 5,
        }
        result = svc.generate_title_card(part)

        assert isinstance(result, GeneratedScript)
        assert "showChapterTitle" in result.script_source
        assert "Welcome" in result.script_source
        assert result.provider_used == "hardcoded"
        assert result.llm_tokens_used == 0

    def test_title_card_defaults(self):
        mock_llm = AsyncMock()
        mock_prompt_builder = MagicMock()
        svc = GenerationService(mock_llm, mock_prompt_builder)

        part = {"id": "title-2"}
        result = svc.generate_title_card(part)
        assert "showChapterTitle" in result.script_source


class TestValidateExtractedCode:
    def test_valid_code(self):
        GenerationService._validate_extracted_code(
            'import { test } from "@playwright/test";\ntest("x", async () => {});'
        )

    def test_trivially_short(self):
        with pytest.raises(ValueError, match="trivially short"):
            GenerationService._validate_extracted_code("x")

    def test_missing_imports(self):
        with pytest.raises(ValueError, match="missing imports"):
            GenerationService._validate_extracted_code(
                "const x = 1; const y = 2; const z = 3; something;"
            )


class TestResolveLlm:
    def test_no_override(self):
        mock_llm = AsyncMock()
        mock_prompt_builder = MagicMock()
        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = svc._resolve_llm(None)
        assert result is mock_llm

    def test_override_without_model(self):
        from src.api.scripts_schemas import LLMConfigOverride

        mock_llm = AsyncMock()
        mock_prompt_builder = MagicMock()
        svc = GenerationService(mock_llm, mock_prompt_builder)
        result = svc._resolve_llm(LLMConfigOverride())
        assert result is mock_llm


class TestFindWMProvider:
    def test_direct_wm_provider(self):
        from src.llm.wm_provider import WMLLMProvider

        wm_client = AsyncMock()
        provider = WMLLMProvider(worker_name="test", system_prompt="sp", wm_client=wm_client)
        svc = GenerationService(provider, MagicMock())
        found = svc._find_wm_provider(provider)
        assert found is provider

    def test_wrapped_provider(self):
        from src.llm.wm_provider import WMLLMProvider

        wm_client = AsyncMock()
        inner = WMLLMProvider(worker_name="test", system_prompt="sp", wm_client=wm_client)
        wrapper = MagicMock()
        wrapper._inner = inner
        svc = GenerationService(wrapper, MagicMock())
        found = svc._find_wm_provider(wrapper)
        assert found is inner

    def test_no_wm_provider(self):
        mock_llm = MagicMock(spec=[])  # No _inner / _provider attribute
        svc = GenerationService(mock_llm, MagicMock())
        found = svc._find_wm_provider(mock_llm)
        assert found is None


class TestResolveLLMOverride:
    def test_override_with_wm_provider(self):
        from src.api.scripts_schemas import LLMConfigOverride
        from src.llm.wm_provider import WMLLMProvider

        wm_client = AsyncMock()
        base = WMLLMProvider(
            worker_name="content-script-llm",
            system_prompt="sp",
            wm_client=wm_client,
        )
        svc = GenerationService(base, MagicMock())
        override = svc._resolve_llm(LLMConfigOverride(model="gpt-4o"))
        assert isinstance(override, WMLLMProvider)
        assert override._model_override == "gpt-4o"
        # Same WMClient is reused
        assert override._wm is wm_client


class TestWMProviderCompleteTransportErrors:
    """Regression: WMLLMProvider.complete() must translate ALL httpx transport

    errors — including RemoteProtocolError ("server disconnected"/truncated
    read, a common WM transient) — into a RuntimeError so callers get a uniform
    degrade signal instead of a raw httpx exception wedging them.
    """

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("WM connection refused"),
            httpx.ReadError("WM read failed"),
            httpx.RemoteProtocolError("WM server disconnected"),
        ],
    )
    async def test_transport_error_becomes_runtimeerror(self, exc: httpx.TransportError) -> None:
        from src.llm.types import LLMRequest
        from src.llm.wm_provider import WMLLMProvider

        wm_client = AsyncMock()
        wm_client.message = AsyncMock(side_effect=exc)
        provider = WMLLMProvider(
            worker_name="content-script-llm", system_prompt="sp", wm_client=wm_client
        )
        request = LLMRequest(system_prompt="sp", user_prompt="hello")

        with pytest.raises(RuntimeError, match="transport error"):
            await provider.complete(request)
