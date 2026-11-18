"""AST scan: verify every FastAPI route declares an explicit ``operation_id``.

Walks src/ for ``@app.<method>(...)``, ``@router.<method>(...)``, and any
``add_api_route(...)`` call, and prints the (file, lineno, method, path)
of any decorator missing an ``operation_id=`` keyword.

Run from repo root:

    uv run python scripts/scan_operation_ids.py
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}


def _decorator_is_route(call: ast.Call) -> str | None:
    """Return method name if `call` is a FastAPI route decorator, else None."""
    func = call.func
    # @app.get(...) / @router.post(...)
    if (
        isinstance(func, ast.Attribute)
        and func.attr in ROUTE_METHODS
        and isinstance(func.value, ast.Name)
        and func.value.id in {"app", "router"}
    ):
        return func.attr
    return None


def _call_is_add_api_route(call: ast.Call) -> bool:
    func = call.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "add_api_route"
        and isinstance(func.value, ast.Name)
        and func.value.id in {"app", "router"}
    )


def _has_kw(call: ast.Call, name: str) -> bool:
    return any(kw.arg == name for kw in call.keywords)


def _first_path_arg(call: ast.Call) -> str:
    if call.args and isinstance(call.args[0], ast.Constant):
        return str(call.args[0].value)
    return ""


def _visit_decorators(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    py: Path,
    found: list[tuple[Path, int, str, str, bool]],
    offenders: list[tuple[Path, int, str, str]],
) -> None:
    for deco in node.decorator_list:
        if not isinstance(deco, ast.Call):
            continue
        method = _decorator_is_route(deco)
        if method is None:
            continue
        path_str = _first_path_arg(deco)
        has_id = _has_kw(deco, "operation_id")
        found.append((py, deco.lineno, method.upper(), path_str, has_id))
        if not has_id:
            offenders.append((py, deco.lineno, method.upper(), path_str))


def scan(root: Path) -> list[tuple[Path, int, str, str]]:
    """Return (file, lineno, method, path-or-empty) tuples for routes missing operation_id."""
    offenders: list[tuple[Path, int, str, str]] = []
    found: list[tuple[Path, int, str, str, bool]] = []

    for py in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(), filename=str(py))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                _visit_decorators(node, py, found, offenders)
            elif isinstance(node, ast.Call) and _call_is_add_api_route(node):
                path_str = _first_path_arg(node)
                has_id = _has_kw(node, "operation_id")
                found.append((py, node.lineno, "ROUTE", path_str, has_id))
                if not has_id:
                    offenders.append((py, node.lineno, "ROUTE", path_str))

    total = len(found)
    with_id = sum(1 for _, _, _, _, ok in found if ok)
    print(f"Routes scanned: {total}")
    print(f"With explicit operation_id: {with_id}")
    print(f"Missing operation_id: {total - with_id}")
    return offenders


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    offenders = scan(repo / "src")
    if offenders:
        print("\nOffenders:")
        for path, lineno, method, p in offenders:
            print(f"  {path.relative_to(repo)}:{lineno}  {method:<6} {p}")
        return 1
    print("\nAll routes declare an explicit operation_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
