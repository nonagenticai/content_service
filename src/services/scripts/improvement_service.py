"""Auto-fix loop for failing Playwright demo scripts.

Uses the shared LLM provider (no duplicated httpx code) and delegates
validation back to ValidationService.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path

from src.domain.script import ImprovementAttempt, ValidationResult
from src.infra.cancel_flags import JobCancelledError
from src.llm.types import LLMProvider, LLMRequest
from src.services.scripts.validation_service import ValidationService

logger = logging.getLogger(__name__)

_MIN_CODE_CHARS = 20

FIX_SYSTEM_PROMPT = (
    "You are a senior Playwright test automation engineer who fixes broken "
    "TypeScript Playwright demo scripts. You always return the COMPLETE fixed "
    "file in a ```typescript code block. Never omit imports or boilerplate."
)

_DEMO_HELPERS_CHEAT_SHEET = """\
Import from `'../helpers/demo-helpers'`:

```typescript
// Visual highlighting
export async function highlightElement(page: Page, selector: string, options?: HighlightOptions): Promise<void>;
export async function safeHighlight(page: Page, selector: string, options?: HighlightOptions): Promise<void>;

// Clicking
export async function slowClick(page: Page, selector: string, options?: ClickOptions): Promise<void>;
export async function safeClick(page: Page, selector: string, options?: ClickOptions): Promise<void>;

// Typing
export async function slowType(page: Page, locator: Locator, text: string, options?: TypeOptions): Promise<void>;
export async function safeType(page: Page, selector: string, text: string, options?: TypeOptions): Promise<void>;

// Scrolling
export async function scrollToElement(page: Page, selector: string, options?: ScrollOptions): Promise<void>;

// Timing / pacing
export async function pause(page: Page, ms: number): Promise<void>;

// Chapter titles
export async function showChapterTitle(page: Page, options: ChapterTitleOptions): Promise<void>;

// Theme
export function setDemoTheme(theme: Partial<DemoTheme>): void;
export const currentTheme: DemoTheme;
```
"""


def _is_meaningful_code(code: str) -> bool:
    """Return True if *code* contains meaningful TypeScript."""
    stripped = re.sub(r"//[^\n]*", "", code)
    stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
    stripped = re.sub(r"\s+", "", stripped)
    return len(stripped) >= _MIN_CODE_CHARS


def _extract_typescript_block(text: str) -> str | None:
    """Extract TypeScript code from a fenced code block."""
    for lang in ("typescript", "ts"):
        pattern = rf"```{lang}\s*\n(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
    match = re.search(r"```\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None


def _build_error_hints(errors: list[str], attempt_number: int) -> str:
    """Build targeted hint text based on error patterns."""
    hints = ""
    if attempt_number > 1:
        hints += (
            f"\n## IMPORTANT: Attempt {attempt_number} -- Previous fix did not work\n\n"
            "You MUST make a substantive change. Do not return the same code. "
            "If the error is a strict mode violation, replace ALL "
            "`slowType(page, page.locator(...), ...)` calls with "
            "`safeType(page, selector, text)` -- no exceptions.\n\n"
        )

    combined = " ".join(errors).lower()

    if "not visible" in combined or "hidden" in combined:
        hints += (
            "\n## Hint: Hidden Element\n\n"
            "Fix: Use `safeClick(page, selector)` instead of `slowClick` or raw `.click()` -- "
            "it waits up to 5 s for visibility and skips gracefully.\n"
        )

    if "strict mode" in combined:
        hints += (
            "\n## Hint: Strict Mode Violation\n\n"
            "Replace every `slowType` with `safeType`. "
            "For non-typing locators, append `.first()`.\n"
        )

    if "timeout" in combined:
        hints += (
            "\n## Hint: Timeout\n\n"
            "Add `await page.waitForTimeout(2000)` after navigation. "
            "Use `waitForLoadState('networkidle')` after `goto()`. "
            "Prefer `safeClick` / `safeHighlight` which skip missing elements.\n"
        )

    if "missing" in combined or "not found" in combined:
        hints += (
            "\n## Hint: Missing Element\n\n"
            "Use `safeHighlight` and `safeClick` which skip missing elements gracefully.\n"
        )

    return hints


_BACKOFF_BASE_SECONDS: float = 2.0


class ImprovementService:
    """Iteratively improve scripts that fail validation using the shared LLM provider."""

    def __init__(self, llm: LLMProvider, max_attempts: int = 3) -> None:
        self._llm = llm
        self._max_attempts = max_attempts

    async def _call_llm_for_fix(
        self,
        current_source: str,
        current_errors: list[str],
        attempt: int,
        attempts_detail: list[ImprovementAttempt],
    ) -> str | None:
        """Call LLM to fix the script. Returns raw response or None if failed."""
        error_text = "\n".join(f"- {e}" for e in current_errors)
        fix_hints = _build_error_hints(current_errors, attempt)
        fix_prompt = (
            "# Fix Playwright Demo Script\n\n"
            "The following Playwright demo script has errors. Fix them.\n\n"
            "## Current Script\n\n"
            f"```typescript\n{current_source}\n```\n\n"
            f"## Errors\n\n{error_text}\n\n"
            f"{fix_hints}"
            "## Available Demo Helpers\n\n"
            f"{_DEMO_HELPERS_CHEAT_SHEET}\n"
            "Output the COMPLETE fixed TypeScript file in a ```typescript code block.\n"
        )
        try:
            response = await self._llm.complete(
                LLMRequest(system_prompt=FIX_SYSTEM_PROMPT, user_prompt=fix_prompt)
            )
            return response.content
        except (OSError, TimeoutError, ValueError, RuntimeError) as exc:
            logger.warning("LLM call failed on attempt %d: %s", attempt, exc)
            attempts_detail.append(
                ImprovementAttempt(
                    attempt=attempt,
                    errors=current_errors,
                    fix_applied=False,
                    reason=f"LLM call failed: {exc}",
                )
            )
            if attempt < self._max_attempts:
                delay = _BACKOFF_BASE_SECONDS * attempt
                logger.info("Backing off %.1fs before retry", delay)
                await asyncio.sleep(delay)
            return None

    @staticmethod
    def _extract_and_validate_code(
        raw_response: str,
        current_errors: list[str],
        attempt: int,
        attempts_detail: list[ImprovementAttempt],
    ) -> str | None:
        """Extract TS code block and validate it's meaningful. Returns code or None."""
        fixed_code = _extract_typescript_block(raw_response)
        if fixed_code is None:
            logger.warning("No code block in LLM response (attempt %d)", attempt)
            attempts_detail.append(
                ImprovementAttempt(
                    attempt=attempt,
                    errors=current_errors,
                    fix_applied=False,
                    reason="No TypeScript code block found in LLM response",
                )
            )
            return None
        if not _is_meaningful_code(fixed_code):
            logger.warning("Extracted code is trivial (attempt %d)", attempt)
            attempts_detail.append(
                ImprovementAttempt(
                    attempt=attempt,
                    errors=current_errors,
                    fix_applied=False,
                    reason="Extracted code is trivial/empty",
                )
            )
            return None
        return fixed_code

    @staticmethod
    async def _raise_if_cancelled(cancel_check: Callable[[], Awaitable[bool]] | None) -> None:
        """Poll the cross-replica cancel flag; raise if a cancel was requested."""
        if cancel_check is not None and await cancel_check():
            raise JobCancelledError("improvement cancelled before attempt")

    async def improve(
        self,
        scripts_dir: Path,
        script_path: Path,
        errors: list[str],
        validator: ValidationService,
        cancel_check: Callable[[], Awaitable[bool]] | None = None,
        baseline: ValidationResult | None = None,
    ) -> ValidationResult:
        """Run the fix-validate loop for *script_path*.

        ``cancel_check`` is polled at the top of each fix attempt so a
        cross-replica cancel stops the loop between LLM fix calls; it raises
        :class:`JobCancelledError` when the flag is set.

        ``baseline`` is the pre-improvement :class:`ValidationResult` for the
        script as it exists on disk *now*. It matters only on the exhausted
        path, which restores ``original_source``: once the original is back,
        the honest compile/discover/dry-run measurement for that file is the
        baseline one, and it is the caller (not this loop) that holds it.
        Omitting it leaves the three booleans at their ``False`` defaults,
        i.e. "never measured" is reported as "measured and failed".
        """
        attempts_detail: list[ImprovementAttempt] = []

        if not script_path.exists():
            return ValidationResult(
                errors=["Script file not found -- generation may have failed."],
            )

        # Backup original
        backup_path = script_path.with_suffix(".bak")
        await asyncio.to_thread(shutil.copy2, script_path, backup_path)
        original_source = await asyncio.to_thread(script_path.read_text, encoding="utf-8")
        logger.info("Backed up %s -> %s", script_path.name, backup_path.name)

        current_errors = list(errors)
        current_source = original_source
        part_id = self._derive_part_id(script_path)

        for attempt in range(1, self._max_attempts + 1):
            await self._raise_if_cancelled(cancel_check)

            logger.info(
                "Improvement attempt %d/%d for %s",
                attempt,
                self._max_attempts,
                script_path.name,
            )

            buildinfo = scripts_dir / "tsconfig.tsbuildinfo"
            if buildinfo.exists():
                buildinfo.unlink()

            raw_response = await self._call_llm_for_fix(
                current_source, current_errors, attempt, attempts_detail
            )
            if raw_response is None:
                continue

            fixed_code = self._extract_and_validate_code(
                raw_response, current_errors, attempt, attempts_detail
            )
            if fixed_code is None:
                continue

            await asyncio.to_thread(script_path.write_text, fixed_code, encoding="utf-8")
            current_source = fixed_code

            # Invalidate again before re-validation
            if buildinfo.exists():
                buildinfo.unlink()

            validation = await validator.validate(scripts_dir, part_id)

            attempts_detail.append(
                ImprovementAttempt(
                    attempt=attempt,
                    errors=list(validation.errors) if validation.errors else [],
                    fix_applied=True,
                    accepted=validation.accepted,
                )
            )

            if validation.accepted:
                logger.info("Script accepted on attempt %d", attempt)
                if backup_path.exists():
                    backup_path.unlink()
                return ValidationResult(
                    compiles=validation.compiles,
                    discoverable=validation.discoverable,
                    runs_headless=validation.runs_headless,
                    accepted=True,
                    improvement_attempts=attempt,
                    attempts_detail=attempts_detail,
                )

            current_errors = list(validation.errors) if validation.errors else current_errors

        # All attempts exhausted -- restore original
        logger.warning("All %d fix attempts exhausted -- restoring original", self._max_attempts)
        await asyncio.to_thread(script_path.write_text, original_source, encoding="utf-8")
        if backup_path.exists():
            backup_path.unlink()

        # Carry the baseline measurement forward. The fix loop re-measured only
        # candidate sources that have just been thrown away, so those readings
        # do not describe the file on disk; the baseline does. Without this the
        # three booleans fall back to ``False`` and the orchestrator persists
        # "it never compiled" for a script that compiled and was discoverable
        # but flaked on the headless dry-run -- destroying the triage signal
        # and leaving the row disagreeing with the restored file.
        return ValidationResult(
            compiles=baseline.compiles if baseline is not None else False,
            discoverable=baseline.discoverable if baseline is not None else False,
            runs_headless=baseline.runs_headless if baseline is not None else False,
            errors=current_errors,
            improvement_attempts=self._max_attempts,
            attempts_detail=attempts_detail,
        )

    @staticmethod
    def _derive_part_id(script_path: Path) -> str:
        """Extract the part ID from a script filename."""
        name = script_path.name
        for suffix in (".demo.spec.ts", ".spec.ts", ".ts"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return script_path.stem
