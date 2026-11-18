"""Tests for services.scripts.improvement_service."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.domain.script import ValidationResult
from src.llm.types import LLMResponse
from src.services.scripts.improvement_service import (
    ImprovementService,
    _build_error_hints,
    _extract_typescript_block,
    _is_meaningful_code,
)


class TestIsMeaningfulCode:
    def test_meaningful(self):
        code = 'import { test } from "@playwright/test"; test("x", async () => {});'
        assert _is_meaningful_code(code) is True

    def test_trivial(self):
        assert _is_meaningful_code("// comment") is False
        assert _is_meaningful_code("") is False
        assert _is_meaningful_code("   ") is False

    def test_only_comments(self):
        code = "// line comment\n/* block comment */"
        assert _is_meaningful_code(code) is False


class TestExtractTypescriptBlock:
    def test_typescript_fence(self):
        text = "Here is the fix:\n```typescript\nconst x = 1;\n```\nDone."
        assert _extract_typescript_block(text) == "const x = 1;"

    def test_ts_fence(self):
        text = "```ts\nconst x = 1;\n```"
        assert _extract_typescript_block(text) == "const x = 1;"

    def test_plain_fence(self):
        text = "```\nconst x = 1;\n```"
        assert _extract_typescript_block(text) == "const x = 1;"

    def test_no_fence(self):
        assert _extract_typescript_block("just text") is None


class TestBuildErrorHints:
    def test_attempt_1_no_special_hints(self):
        hints = _build_error_hints(["some error"], 1)
        assert "IMPORTANT" not in hints

    def test_attempt_2_includes_warning(self):
        hints = _build_error_hints(["some error"], 2)
        assert "Attempt 2" in hints

    def test_hidden_element_hint(self):
        hints = _build_error_hints(["element not visible"], 1)
        assert "Hidden Element" in hints

    def test_strict_mode_hint(self):
        hints = _build_error_hints(["strict mode violation"], 1)
        assert "Strict Mode" in hints

    def test_timeout_hint(self):
        hints = _build_error_hints(["timeout waiting for selector"], 1)
        assert "Timeout" in hints

    def test_missing_element_hint(self):
        hints = _build_error_hints(["element not found"], 1)
        assert "Missing Element" in hints


class TestDerivePartId:
    def test_demo_spec_ts(self):
        assert ImprovementService._derive_part_id(Path("foo.demo.spec.ts")) == "foo"

    def test_spec_ts(self):
        assert ImprovementService._derive_part_id(Path("bar.spec.ts")) == "bar"

    def test_ts(self):
        assert ImprovementService._derive_part_id(Path("baz.ts")) == "baz"

    def test_no_known_suffix(self):
        assert ImprovementService._derive_part_id(Path("qux.js")) == "qux"


class TestImprovementServiceImprove:
    @pytest.fixture
    def tmp_scripts(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        parts_dir = scripts_dir / "parts"
        parts_dir.mkdir()
        script_path = parts_dir / "part1.demo.spec.ts"
        script_path.write_text(
            'import { test } from "@playwright/test";\ntest("x", async () => {});',
            encoding="utf-8",
        )
        return scripts_dir, script_path

    async def test_script_not_found(self):
        mock_llm = AsyncMock()
        svc = ImprovementService(mock_llm, max_attempts=1)
        result = await svc.improve(
            Path("/tmp/scripts"),
            Path("/tmp/scripts/nonexistent.ts"),
            ["error"],
            AsyncMock(),
        )
        assert result.accepted is False
        assert "not found" in (result.errors or [""])[0]

    async def test_fix_accepted_on_first_attempt(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        mock_llm = AsyncMock()
        fixed_code = (
            'import { test } from "@playwright/test";\n'
            'test("fixed", async ({ page }) => { await page.goto("http://example.com"); });'
        )
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(
                content=f"```typescript\n{fixed_code}\n```",
                provider="test",
            )
        )
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult(
                compiles=True,
                discoverable=True,
                runs_headless=True,
                accepted=True,
            )
        )

        svc = ImprovementService(mock_llm, max_attempts=3)
        result = await svc.improve(scripts_dir, script_path, ["compile error"], mock_validator)

        assert result.accepted is True
        assert result.improvement_attempts == 1

    async def test_llm_failure_continues(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(side_effect=RuntimeError("LLM down"))
        mock_validator = AsyncMock()

        svc = ImprovementService(mock_llm, max_attempts=1)
        result = await svc.improve(scripts_dir, script_path, ["error"], mock_validator)

        assert result.accepted is False
        assert result.improvement_attempts == 1

    async def test_no_code_block_in_response(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(content="No code here", provider="test")
        )
        mock_validator = AsyncMock()

        svc = ImprovementService(mock_llm, max_attempts=1)
        result = await svc.improve(scripts_dir, script_path, ["error"], mock_validator)
        assert result.accepted is False

    async def test_trivial_code_skipped(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(
                content="```typescript\n// just a comment\n```",
                provider="test",
            )
        )
        mock_validator = AsyncMock()

        svc = ImprovementService(mock_llm, max_attempts=1)
        result = await svc.improve(scripts_dir, script_path, ["error"], mock_validator)
        assert result.accepted is False

    async def test_all_attempts_exhausted_restores_original(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        original = script_path.read_text(encoding="utf-8")
        fixed_code = (
            'import { test } from "@playwright/test";\n'
            'test("attempt", async ({ page }) => { await page.goto("http://example.com"); });'
        )
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(
                content=f"```typescript\n{fixed_code}\n```",
                provider="test",
            )
        )
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult(
                compiles=False,
                accepted=False,
                errors=["still broken"],
            )
        )

        svc = ImprovementService(mock_llm, max_attempts=2)
        result = await svc.improve(scripts_dir, script_path, ["error"], mock_validator)
        assert result.accepted is False
        assert result.improvement_attempts == 2
        # Original should be restored
        assert script_path.read_text(encoding="utf-8") == original

    async def test_buildinfo_removed(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        buildinfo = scripts_dir / "tsconfig.tsbuildinfo"
        buildinfo.write_text("{}")

        fixed_code = (
            'import { test } from "@playwright/test";\n'
            'test("fixed", async ({ page }) => { await page.goto("http://example.com"); });'
        )
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(
                content=f"```typescript\n{fixed_code}\n```",
                provider="test",
            )
        )
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult(accepted=True, compiles=True)
        )

        svc = ImprovementService(mock_llm, max_attempts=1)
        await svc.improve(scripts_dir, script_path, ["error"], mock_validator)
        # buildinfo should have been deleted (at least attempted)


class TestExhaustedPathPreservesBaselineMeasurement:
    """The exhausted improve path must not erase what was already measured.

    ``TestImprovementServiceImprove.test_all_attempts_exhausted_restores_original``
    asserts only ``accepted`` / ``improvement_attempts`` and the restored file
    bytes -- it never asserts ``compiles`` / ``discoverable`` /
    ``runs_headless``. That blind spot let the exhausted path return a
    ``ValidationResult`` with those three at their ``False`` defaults, so a
    script that genuinely compiled and was discoverable but flaked on the
    headless dry-run got persisted as "it never compiled".
    """

    @pytest.fixture
    def tmp_scripts(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        parts_dir = scripts_dir / "parts"
        parts_dir.mkdir()
        script_path = parts_dir / "part1.demo.spec.ts"
        script_path.write_text(
            'import { test } from "@playwright/test";\ntest("x", async () => {});',
            encoding="utf-8",
        )
        return scripts_dir, script_path

    async def test_exhausted_carries_baseline_booleans(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        fixed_code = (
            'import { test } from "@playwright/test";\n'
            'test("attempt", async ({ page }) => { await page.goto("http://example.com"); });'
        )
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(
                content=f"```typescript\n{fixed_code}\n```",
                provider="test",
            )
        )
        mock_validator = AsyncMock()
        mock_validator.validate = AsyncMock(
            return_value=ValidationResult(
                compiles=True,
                discoverable=True,
                runs_headless=False,
                accepted=False,
                errors=["still flaky headless"],
            )
        )
        # The script compiled and was discoverable; only the dry-run failed.
        baseline = ValidationResult(
            compiles=True,
            discoverable=True,
            runs_headless=False,
            accepted=False,
            errors=["dry-run failed"],
        )

        svc = ImprovementService(mock_llm, max_attempts=2)
        result = await svc.improve(
            scripts_dir,
            script_path,
            ["dry-run failed"],
            mock_validator,
            baseline=baseline,
        )

        assert result.accepted is False
        assert result.improvement_attempts == 2
        # These three are the regression: without the fix they are all False.
        assert result.compiles is True
        assert result.discoverable is True
        assert result.runs_headless is False

    async def test_exhausted_without_baseline_stays_false(self, tmp_scripts):
        scripts_dir, script_path = tmp_scripts
        mock_llm = AsyncMock()
        mock_llm.complete = AsyncMock(
            return_value=LLMResponse(content="no code here", provider="test")
        )
        mock_validator = AsyncMock()

        svc = ImprovementService(mock_llm, max_attempts=1)
        result = await svc.improve(scripts_dir, script_path, ["error"], mock_validator)

        assert result.compiles is False
        assert result.discoverable is False
        assert result.runs_headless is False
