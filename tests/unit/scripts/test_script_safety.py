"""Tests for the static script-safety scan."""

from __future__ import annotations

import pytest

from src.services.scripts.script_safety import scan_script_source

# A normal, legitimate Playwright demo spec — must pass cleanly.
_SAFE_SCRIPT = """\
import { test, expect } from '@playwright/test';
import { safeClick, slowType, highlightElement } from '../helpers/demo-helpers';

test('demo', async ({ page }) => {
  await page.goto('https://example.com');
  await expect(page).toHaveTitle(/Example/);
  await page.waitForFunction(() => document.readyState === 'complete');
  await safeClick(page, '#cta');
  await slowType(page, page.locator('#search'), 'hello');
  await page.evaluate(() => window.scrollTo(0, 100));
  await highlightElement(page, '.hero');
});
"""


def test_safe_script_has_no_violations() -> None:
    assert scan_script_source(_SAFE_SCRIPT) == []


def test_empty_source_is_safe() -> None:
    assert scan_script_source("") == []


@pytest.mark.parametrize(
    ("snippet", "expected_rule"),
    [
        ("const cp = require('child_process');", "node_builtin_require"),
        ("const cp = require('node:child_process');", "node_builtin_require"),
        ("const fs = require('fs');", "node_builtin_require"),
        ("import { exec } from 'child_process';", "node_builtin_static_import"),
        ("import * as fs from 'node:fs';", "node_builtin_static_import"),
        ("import 'fs';", "node_builtin_side_effect_import"),
        ("await import('node:os');", "node_builtin_dynamic_import"),
        ("const k = process.env.AWS_SECRET_ACCESS_KEY;", "process_env_access"),
        ("eval('2+2');", "eval_call"),
        ("const f = new Function('return 1');", "function_constructor"),
        ("fetch('http://169.254.169.254/latest/meta-data/');", "cloud_metadata_host"),
        ("fetch('http://metadata.google.test/');", "cloud_metadata_host"),
        ("process.binding('spawn_sync');", "process_binding"),
    ],
)
def test_dangerous_constructs_flagged(snippet: str, expected_rule: str) -> None:
    violations = scan_script_source(snippet)
    assert violations, f"expected a violation for: {snippet}"
    assert any(v.rule == expected_rule for v in violations), (
        f"expected rule {expected_rule}, got {[v.rule for v in violations]}"
    )


def test_child_process_reference_flagged() -> None:
    violations = scan_script_source("const x = globalThis.child_process;")
    assert any(v.rule == "child_process_reference" for v in violations)


def test_legit_playwright_apis_not_flagged() -> None:
    # page.evaluate / waitForFunction / expect must NOT trip the eval/Function rules.
    src = (
        "await page.evaluate(() => 1);\n"
        "await page.waitForFunction(() => true);\n"
        "await page.evaluateHandle(() => document.body);\n"
    )
    assert scan_script_source(src) == []
