"""Two-pass script generation service.

Pass 1: Generate the full script via the WM LLM gateway
Pass 2: Extract clean TypeScript from the response via a second WM call

All LLM traffic flows through worker_management — see
:mod:`src.llm.wm_provider` and memory ``wm-as-sole-llm-gateway.md``.

Includes post-generation validation for Playwright API diversity:
scripts must use >= 5 distinct API types to produce engaging demos.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable

from src.api.scripts_schemas import LLMConfigOverride
from src.domain.script import GeneratedScript
from src.infra.cancel_flags import JobCancelledError
from src.llm.types import LLMProvider, LLMRequest, LLMResponse
from src.llm.wm_provider import WMLLMProvider
from src.services.scripts.prompt_builder import PromptBuilder

logger = logging.getLogger(__name__)

GENERATION_SYSTEM_PROMPT = (
    "You are a senior Playwright test automation engineer. You write clean, "
    "well-structured TypeScript Playwright test scripts for website demo recordings."
)

EXTRACTION_SYSTEM_PROMPT = (
    "You are a TypeScript code extractor. Extract ONLY the complete TypeScript source "
    "file from the following text. Output ONLY the code, no markdown fences, no explanation."
)

# Minimum distinct Playwright API types required in a generated script.
_MIN_API_TYPES = 5

# Maximum number of regeneration attempts when API diversity is insufficient.
_MAX_DIVERSITY_RETRIES = 2

# The served model's CONTEXT WINDOW: it bounds prompt + output TOGETHER, and is
# NOT an output cap. Live value verified 2026-08-19 (``Qwen/Qwen3.8-27B-FP8``,
# ``max_model_len=131072``); the old 8192 here was the retired pgx-local
# Nemotron. The pass-2 extraction call feeds pass-1's completion back in as its
# PROMPT, so ``prompt_tokens + max_tokens`` must stay <= this ceiling.
_MODEL_CONTEXT_WINDOW = 131072

# Hard ceiling on a SINGLE request's output budget, enforced by
# worker_management: ``MessageRequest.max_tokens`` REJECTS anything outside
# 1..16384 with a validation error (an FP4-MoE kernel-crash guard —
# ``pydantic_ai_agent.SAFE_MAX_TOKENS``). It does not silently clamp, so a
# budget above this is a TERMINAL 422, not a quietly shortened response. This —
# not the context window — is what bounds pass-2's output.
_WM_MAX_OUTPUT_TOKENS = 16384

# Slack added on top of pass-1's completion size for pass-2's output budget:
# the extractor re-emits pass-1's body ~verbatim (only stripping fences), so it
# needs roughly the same token count plus a small margin.
_EXTRACT_OUTPUT_MARGIN = 512

# If the LLM extraction returns a body shorter than this fraction of the
# fence-stripped pass-1 CODE baseline, treat it as truncated and fall back to
# that baseline. Both sides of the comparison are now fence-stripped code (not
# prose-laden content), so a legitimate extraction is ~100% of the baseline and
# this floor can be tight without false-positiving on a good extraction.
_EXTRACT_TRUNCATION_FLOOR = 0.9


def _extract_fenced_code(text: str) -> str:
    """Return the code inside a markdown fence, wherever it sits in ``text``.

    The pass-2 LLM is asked to emit fence-free code, but the SKIP/truncation
    fallbacks return raw pass-1 content, which routinely wraps the code in a
    ```ts fence with prose before/after. Anchored ``^`````/```` ```$ `` strips
    only leading/trailing fences and leak mid-string prose into the script, so
    match the fenced block structurally first; fall back to a stray-fence strip
    when there is no complete fence.
    """
    m = re.search(r"```(?:typescript|ts)?\s*\n?(.*?)```", text, re.DOTALL)
    if m:
        return m.group(1).strip()
    stripped = re.sub(r"^```(?:typescript|ts)?\s*\n?", "", text.strip())
    stripped = re.sub(r"\n?```\s*$", "", stripped)
    return stripped.strip()


# Categories of Playwright/demo-helper APIs. Each entry is (category_name, [patterns]).
_API_TYPE_CATEGORIES: list[tuple[str, list[str]]] = [
    ("navigation", [r"page\.goto\(", r"page\.goBack\(", r"page\.goForward\(", r"page\.reload\("]),
    (
        "click",
        [r"slowClick\(", r"safeClick\(", r"page\.click\(", r"\.click\("],
    ),
    (
        "type_input",
        [r"slowType\(", r"safeType\(", r"page\.fill\(", r"page\.type\(", r"\.fill\("],
    ),
    (
        "scroll",
        [r"scrollToElement\(", r"smoothScroll\(", r"scrollIntoView"],
    ),
    (
        "highlight_annotate",
        [
            r"highlightElement\(",
            r"safeHighlight\(",
            r"spotlightElement\(",
            r"annotateAndWait\(",
            r"showAnnotation\(",
        ],
    ),
    (
        "pause_wait",
        [
            r"(?<!\w)pause\(",
            r"page\.waitForTimeout\(",
            r"waitForVisualStability\(",
            r"page\.waitForLoadState\(",
            r"page\.waitForSelector\(",
        ],
    ),
    ("hover", [r"slowHover\(", r"page\.hover\("]),
    ("keyboard", [r"page\.keyboard\.", r"showKeyCombo\("]),
    ("evaluate", [r"page\.evaluate\("]),
]


def _count_api_types(script_source: str) -> tuple[int, list[str], list[str]]:
    """Count distinct Playwright API type categories used in a script.

    Returns (count, found_categories, missing_categories).
    """
    found: list[str] = []
    missing: list[str] = []
    for category, patterns in _API_TYPE_CATEGORIES:
        if any(re.search(pat, script_source) for pat in patterns):
            found.append(category)
        else:
            missing.append(category)
    return len(found), found, missing


class GenerationService:
    """Two-pass generation: generate + extract."""

    def __init__(self, llm: LLMProvider, prompt_builder: PromptBuilder) -> None:
        self._llm = llm
        self._prompt_builder = prompt_builder

    async def generate(
        self,
        *,
        part: dict,
        page_analysis: dict[str, dict] | None = None,
        storyline_context: str | None = None,
        theme: dict | None = None,
        user_context: dict | None = None,
        mode: str | None = None,
        source_type: str | None = None,
        auth_enabled: bool | None = None,
        message: str | None = None,
        llm_config: LLMConfigOverride | None = None,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> GeneratedScript:
        """Generate a Playwright script via two-pass LLM calls.

        After extraction, validates that the script uses at least
        ``_MIN_API_TYPES`` distinct Playwright API categories. If not,
        regenerates with a stronger prompt (up to ``_MAX_DIVERSITY_RETRIES``).

        ``cancel_check`` is polled at the top of each generate/retry attempt so
        a cross-replica cancel stops the loop between (expensive) LLM calls
        rather than only after the whole job finishes; it raises
        :class:`JobCancelledError` when the flag is set.
        """
        llm = self._resolve_llm(llm_config)

        prompt_builder = self._prompt_builder.with_overrides(
            mode=mode,
            source_type=source_type,
            auth_enabled=auth_enabled,
        )
        base_prompt = prompt_builder.build(
            part=part,
            page_analysis=page_analysis or {},
            storyline_context=storyline_context or "",
            theme=theme,
            user_context=user_context,
            message=message or "",
        )

        total_gen_time = 0.0
        total_tokens = 0
        provider_used = ""
        script_source = ""
        max_attempts = 1 + _MAX_DIVERSITY_RETRIES

        for attempt in range(1, max_attempts + 1):
            if cancel_check is not None and await cancel_check():
                raise JobCancelledError("generation cancelled before attempt")

            prompt = base_prompt
            if attempt > 1:
                api_count, found, missing = _count_api_types(script_source)
                prompt += (
                    "\n\n## CRITICAL: Interaction Diversity Requirement\n\n"
                    f"Your previous script used only {api_count} API type(s): "
                    f"{', '.join(found)}.\n"
                    f"You MUST also use: **{', '.join(missing[:5])}**.\n\n"
                    "Specifically:\n"
                )
                if "scroll" in missing:
                    prompt += (
                        "- Use `scrollToElement(page, selector)` before highlighting "
                        "below-fold elements\n"
                    )
                if "highlight_annotate" in missing:
                    prompt += (
                        "- Use `safeHighlight(page, selector, 'label')` to draw attention "
                        "to key elements\n"
                        "- Use `annotateAndWait(page, 'text', 3000)` to explain what's "
                        "happening\n"
                    )
                if "click" in missing:
                    prompt += (
                        "- Use `safeClick(page, selector)` to demonstrate interactive elements\n"
                    )
                if "hover" in missing:
                    prompt += "- Use `slowHover(page, selector)` to reveal hover states\n"
                if "pause_wait" in missing:
                    prompt += "- Use `pause(page, 2000)` between actions for pacing\n"
                prompt += (
                    "\nEvery page visit must include: navigate → scroll → "
                    "highlight 2-3 elements → annotate.\n"
                )

            # Pass 1: Generate
            gen_response = await llm.complete(
                LLMRequest(
                    system_prompt=GENERATION_SYSTEM_PROMPT,
                    user_prompt=prompt,
                )
            )

            # Pass 2: Extract clean TypeScript. ``_extract_typescript`` returns
            # fully fence-stripped code on every path (structural fence
            # extraction via ``_extract_fenced_code``), so no anchored fence-strip
            # is needed here — the old ``^```/```$`` regexes leaked mid-string
            # prose on the SKIP path. The vLLM-ceiling / skip / truncation-fallback
            # logic lives in the helper so it stays out of this diversity-retry
            # loop.
            script_source, extract_latency, extract_tokens = await self._extract_typescript(
                llm, gen_response
            )

            script_source = script_source.strip()

            total_gen_time += gen_response.latency_seconds + extract_latency
            total_tokens += gen_response.tokens_used + extract_tokens
            provider_used = gen_response.provider

            self._validate_extracted_code(script_source)

            api_count, found, missing = _count_api_types(script_source)
            if api_count >= _MIN_API_TYPES:
                logger.debug(
                    "Script API diversity %d/%d — accepted (attempt %d)",
                    api_count,
                    _MIN_API_TYPES,
                    attempt,
                )
                break

            logger.info(
                "Script API diversity %d/%d (found: %s) — retrying (%d/%d)",
                api_count,
                _MIN_API_TYPES,
                ", ".join(found),
                attempt,
                max_attempts,
            )

        truncated_prompt = base_prompt[:500] + "..." if len(base_prompt) > 500 else base_prompt
        return GeneratedScript(
            script_source=script_source,
            prompt_used=truncated_prompt,
            generation_time_seconds=total_gen_time,
            llm_tokens_used=total_tokens,
            provider_used=provider_used,
        )

    async def _extract_typescript(
        self, llm: LLMProvider, gen_response: LLMResponse
    ) -> tuple[str, float, int]:
        """Pass 2: strip markdown fences from pass-1 output → clean TypeScript.

        The extractor's ONLY job is to strip fences and re-emit the code, so its
        output length ~= pass-1's COMPLETION length, and its prompt
        (``gen_response.content``) is also ~= pass-1's completion. The round-trip
        therefore needs ~2x the completion tokens of context. Under the model's
        context window a completion larger than ~half the window CANNOT be re-emitted
        without truncating mid-file — and since ``_validate_extracted_code`` only
        checks for imports + a length floor, a truncated fragment ships GREEN. So:
          * cap ``max_tokens`` on the COMPLETION size (not total tokens, which
            double-counts the prompt), and
          * when the round-trip cannot fit the window, SKIP the LLM and return
            the structurally fence-stripped pass-1 code (via
            ``_extract_fenced_code``) — which does exactly what
            EXTRACTION_SYSTEM_PROMPT asks — instead of shipping a fragment.

        Every return path is fully fence-stripped code, so ``generate`` needs no
        further fence handling. Returns
        ``(script_source, extract_latency_seconds, extract_tokens)``.
        """
        # Clean baseline: the fence-stripped pass-1 code. Every fallback path
        # returns THIS (not raw prose-laden content), so mid-string preamble/
        # postamble around the fence can never leak into the shipped script.
        pass1_code = _extract_fenced_code(gen_response.content)

        pass1_completion = gen_response.completion_tokens or gen_response.tokens_used or 0
        extract_budget = pass1_completion + _EXTRACT_OUTPUT_MARGIN

        if (
            pass1_completion <= 0
            or pass1_completion + extract_budget > _MODEL_CONTEXT_WINDOW
            or extract_budget > _WM_MAX_OUTPUT_TOKENS
        ):
            # Round-trip won't fit (or we have no size signal to trust the cap):
            # the LLM would truncate. Return the fence-stripped baseline directly.
            logger.info(
                "Skipping pass-2 LLM extraction (completion=%d tokens, budget=%d "
                "exceeds ctx %d or WM output cap %d); returning fence-stripped "
                "pass-1 code directly",
                pass1_completion,
                extract_budget,
                _MODEL_CONTEXT_WINDOW,
                _WM_MAX_OUTPUT_TOKENS,
            )
            return pass1_code, 0.0, 0

        extract_response = await llm.complete(
            LLMRequest(
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                user_prompt=gen_response.content,
                max_tokens=min(extract_budget, _WM_MAX_OUTPUT_TOKENS),
            )
        )
        extracted = _extract_fenced_code(extract_response.content)
        # Post-call guard: LLMResponse carries no finish_reason, so detect
        # truncation structurally by comparing against the CODE baseline (both
        # sides fence-stripped). A legit extraction ~= the full baseline; a body
        # far shorter than it was cut off mid-file — fall back to the clean
        # baseline rather than validate a fragment.
        if len(extracted) < len(pass1_code) * _EXTRACT_TRUNCATION_FLOOR:
            logger.warning(
                "Pass-2 extraction returned %d chars from a %d-char code baseline "
                "— treating as truncated; falling back to pass-1 code",
                len(extracted),
                len(pass1_code),
            )
            return pass1_code, extract_response.latency_seconds, extract_response.tokens_used
        return extracted, extract_response.latency_seconds, extract_response.tokens_used

    def _resolve_llm(self, llm_config: LLMConfigOverride | None) -> LLMProvider:
        """Return an LLM provider, applying per-request model overrides when present.

        ``base_url`` and ``api_key_env`` from :class:`LLMConfigOverride` are
        ignored — WM is the sole LLM gateway; provider/base_url selection
        happens server-side by tenant. Only ``model`` is honored.
        """
        if not llm_config or not llm_config.model:
            return self._llm

        logger.info("WM LLM model override active: model=%s", llm_config.model)
        # Reuse the existing WMLLMProvider's worker name + system prompt;
        # override only the model on the next call. If the inner provider
        # is the script-generator WMLLMProvider, copy its config.
        if isinstance(self._llm, WMLLMProvider):
            return WMLLMProvider(
                worker_name=self._llm._worker_name,
                system_prompt=self._llm._system_prompt,
                wm_client=self._llm._wm,
                provider_name=f"{self._llm.name}+override",
                model_override=llm_config.model,
            )
        # Provider chain wraps a WMLLMProvider — find it and rebuild with override.
        inner = self._find_wm_provider(self._llm)
        if inner is not None:
            return WMLLMProvider(
                worker_name=inner._worker_name,
                system_prompt=inner._system_prompt,
                wm_client=inner._wm,
                provider_name=f"{inner.name}+override",
                model_override=llm_config.model,
            )
        # Couldn't introspect (probably a test mock) — return the unmodified provider.
        return self._llm

    @staticmethod
    def _find_wm_provider(provider: LLMProvider) -> WMLLMProvider | None:
        """Find a WMLLMProvider inside wrapper chains."""
        seen: set[int] = set()
        node: object = provider
        while node is not None and id(node) not in seen:
            seen.add(id(node))
            if isinstance(node, WMLLMProvider):
                return node
            node = getattr(node, "_inner", None) or getattr(node, "_provider", None)
        return None

    def generate_title_card(self, part: dict) -> GeneratedScript:
        """Generate a simple title card script (no LLM needed)."""
        part_id = part.get("id", "title")
        title = json.dumps(part.get("title", ""))
        subtitle = json.dumps(part.get("subtitle", "") or "")
        chapter = int(part.get("chapter", 1) or 1)
        duration_ms = int(part.get("estimated_duration_seconds", 5) * 1000)
        test_name = json.dumps(f"{part_id}: {part.get('title', '')}")

        script = f"""import {{ test }} from '@playwright/test';
import {{ showChapterTitle, pause }} from '../helpers/demo-helpers';

test.describe({json.dumps(part.get("title", "")[:80])}, () => {{
  test({test_name}, async ({{ page }}) => {{
    await page.goto('about:blank');
    await showChapterTitle(page, {{
      chapter: {chapter},
      title: {title},
      subtitle: {subtitle},
      duration: {duration_ms},
    }});
    await pause(page, {duration_ms});
  }});
}});
"""
        return GeneratedScript(
            script_source=script,
            prompt_used=None,
            generation_time_seconds=0.0,
            llm_tokens_used=0,
            provider_used="hardcoded",
        )

    @staticmethod
    def _validate_extracted_code(code: str) -> None:
        """Quick sanity checks before accepting extracted code."""
        stripped = re.sub(r"//[^\n]*", "", code)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        stripped = re.sub(r"\s+", "", stripped)
        if len(stripped) < 20:
            raise ValueError("Extracted code is trivially short")
        if "import" not in code:
            raise ValueError("Extracted code missing imports")
