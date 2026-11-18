"""Static safety scan for LLM-generated Playwright/TypeScript scripts.

The validation pipeline *executes* model-authored TypeScript (``npx playwright
test`` imports and runs the spec body). That is an arbitrary-code-execution
surface: a poisoned prompt or a hijacked LLM response can emit a "demo script"
that shells out, reads the filesystem, or exfiltrates cloud-metadata
credentials the moment Playwright imports the module.

This module is a *conservative, fail-closed pre-filter* — not a sandbox. It
greps the source for a small set of high-signal dangerous constructs (Node
built-in module access, ``eval`` / ``Function`` constructors, ``process.env``
reads, cloud-metadata endpoints) and returns a list of violations. Callers MUST
refuse to execute any script with violations.

The ruleset is intentionally narrow to avoid false positives on legitimate
Playwright APIs (``page.goto``, ``page.evaluate``, ``page.waitForFunction``,
``expect``, …). A real defence requires OS-level isolation (seccomp / gVisor /
a network-egress-locked, read-only, unprivileged container) — that is a
separate infrastructure task and this scan does NOT replace it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Node.js built-in modules that have no business inside a browser-driving demo
# script. Importing any of these is the primary RCE/SSRF/file-access vector.
_NODE_BUILTINS = (
    "child_process|fs|fs/promises|os|net|dgram|dns|http|https|http2|tls|vm|"
    "worker_threads|cluster|module|process|v8|inspector|repl|readline|zlib|"
    "perf_hooks|diagnostics_channel"
)

# A module specifier that names a Node built-in, with or without the ``node:``
# scheme prefix: 'fs', "node:child_process", etc.
_MODULE_SPEC = rf"""['"](?:node:)?(?:{_NODE_BUILTINS})['"]"""


@dataclass(frozen=True)
class Violation:
    """One dangerous construct found in a script."""

    rule: str
    message: str
    snippet: str


# (rule_name, compiled pattern, human message). Order is informational only;
# every match across every rule is reported so the author sees all problems.
_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "node_builtin_require",
        re.compile(rf"\brequire\s*\(\s*{_MODULE_SPEC}"),
        "require() of a Node built-in module is not allowed in demo scripts",
    ),
    (
        "node_builtin_dynamic_import",
        re.compile(rf"\bimport\s*\(\s*{_MODULE_SPEC}"),
        "dynamic import() of a Node built-in module is not allowed",
    ),
    (
        "node_builtin_static_import",
        re.compile(rf"\bimport\b[^\n;]*?\bfrom\s*{_MODULE_SPEC}"),
        "importing a Node built-in module is not allowed",
    ),
    (
        "node_builtin_side_effect_import",
        re.compile(rf"\bimport\s*{_MODULE_SPEC}"),
        "side-effect import of a Node built-in module is not allowed",
    ),
    (
        "node_scheme_specifier",
        re.compile(r"""['"]node:[a-z_/]+['"]"""),
        "the node: import scheme is not allowed in demo scripts",
    ),
    (
        "child_process_reference",
        re.compile(r"\bchild_process\b"),
        "references to child_process are not allowed",
    ),
    (
        "process_env_access",
        re.compile(r"\bprocess\.env\b"),
        "reading process.env is not allowed (credential-exfiltration risk)",
    ),
    (
        "process_binding",
        re.compile(r"\bprocess\.(?:binding|dlopen|mainModule)\b"),
        "low-level process internals access is not allowed",
    ),
    (
        "eval_call",
        re.compile(r"\beval\s*\("),
        "eval() is not allowed",
    ),
    (
        "function_constructor",
        re.compile(r"\bnew\s+Function\s*\(|(?<![.\w])Function\s*\("),
        "the Function constructor is not allowed",
    ),
    (
        "cloud_metadata_host",
        re.compile(
            r"169\.254\.169\.254|metadata\.google\.test|metadata\.azure\.com",
        ),
        "access to a cloud-metadata endpoint is not allowed (SSRF/credential theft)",
    ),
)

# Bound the snippet we echo back so a pathological one-line script cannot blow
# up logs or response bodies.
_SNIPPET_LIMIT = 120


def scan_script_source(source: str) -> list[Violation]:
    """Scan *source* for dangerous constructs.

    Returns a list of :class:`Violation`. An empty list means the conservative
    scan found nothing suspicious — it does NOT prove the script is safe.
    Callers treat a non-empty list as a hard reject and must not execute the
    script.
    """
    if not source:
        return []

    violations: list[Violation] = []
    for rule, pattern, message in _RULES:
        match = pattern.search(source)
        if match is None:
            continue
        snippet = match.group(0).strip()[:_SNIPPET_LIMIT]
        violations.append(Violation(rule=rule, message=message, snippet=snippet))
    return violations
