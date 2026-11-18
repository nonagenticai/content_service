"""Pin WHY the narrative background tasks carry no distributed job lock.

Background: ``_process_storyline`` / ``_process_narration`` /
``_process_narration_shotlist`` used to open with
``if not await acquire_job_lock(redis, job_id): return`` and log "skipped
(locked by another pod)". That branch was unreachable. Each task has exactly
ONE call site — the POST handler that mints ``job_id = str(uuid7())`` a few
lines earlier — so the key given to ``SET NX`` was a brand-new uuid7 that no
other replica could possibly hold. ``acquire_job_lock`` always returned True,
the guard protected nothing, and the ``asyncio.shield``ed release protected a
lock that guarded nothing. It was removed (see ``src/cache/job_lock.py``).

These tests are the tripwire on that reasoning. They fail if the premise stops
holding — i.e. if a retry, a resume, or a re-dispatching reconciler ever makes
the SAME job_id reachable twice — so nobody can introduce a duplicate-LLM-spend
path without being told to put the lock back. They equally fail if somebody
re-adds a job-id-keyed lock while the job_id is still single-use, which would
restore the misleading dead branch.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[3] / "src"

# The three fire-and-forget narrative tasks that do paid LLM work.
_TASKS = (
    "_process_storyline",
    "_process_narration",
    "_process_narration_shotlist",
)

_REINSTATE = (
    "If a job_id can now be dispatched more than once, the duplicate-processing "
    "hazard is REAL: re-instate `acquire_job_lock(redis, job_id)` at the top of "
    "the task and release it in a shielded `finally`. See src/cache/job_lock.py."
)


def _modules() -> dict[Path, ast.Module]:
    """Parse every module under ``src/`` once."""
    return {p: ast.parse(p.read_text()) for p in sorted(_SRC.rglob("*.py"))}


def _called_names(node: ast.AST) -> set[str]:
    """Bare function names called anywhere inside ``node``."""
    return {
        n.func.id
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)]


def _dispatch_sites(trees: dict[Path, ast.Module], task: str) -> list[tuple[Path, str]]:
    """Every (file, enclosing-function) that CALLS ``task``, excluding its own def."""
    sites: list[tuple[Path, str]] = []
    for path, tree in trees.items():
        for fn in _functions(tree):
            if fn.name == task:
                continue
            if task in _called_names(fn):
                sites.append((path, fn.name))
        # A module-level call would bypass the per-function scan above.
        for node in tree.body:
            if not isinstance(
                node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            ) and task in _called_names(node):
                sites.append((path, "<module>"))
    return sites


def _task_def(trees: dict[Path, ast.Module], task: str) -> ast.AsyncFunctionDef:
    for tree in trees.values():
        for fn in _functions(tree):
            if fn.name == task and isinstance(fn, ast.AsyncFunctionDef):
                return fn
    pytest.fail(f"{task} not found under src/ — did it move or get renamed?")


@pytest.mark.parametrize("task", _TASKS)
def test_narrative_task_has_exactly_one_dispatch_site(task: str) -> None:
    """No retry / resume / reconciler may re-dispatch a task without a lock decision."""
    sites = _dispatch_sites(_modules(), task)
    assert len(sites) == 1, (
        f"{task} is dispatched from {len(sites)} places: "
        f"{[(p.name, f) for p, f in sites]}. {_REINSTATE}"
    )


@pytest.mark.parametrize("task", _TASKS)
def test_the_sole_dispatch_site_mints_a_fresh_job_id(task: str) -> None:
    """The lock was inert because the handler mints the job_id it dispatches with."""
    trees = _modules()
    (path, caller_name) = _dispatch_sites(trees, task)[0]
    caller = next(fn for fn in _functions(trees[path]) if fn.name == caller_name)

    mints_fresh = any(
        isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "job_id" for t in node.targets)
        and "uuid7" in _called_names(node.value)
        for node in ast.walk(caller)
    )
    assert mints_fresh, (
        f"{caller_name} dispatches {task} with a job_id it did NOT mint via uuid7(). "
        f"That job_id may be caller-supplied or replayed. {_REINSTATE}"
    )


@pytest.mark.parametrize("task", _TASKS)
def test_job_lock_presence_matches_dispatch_reachability(task: str) -> None:
    """A job-id-keyed lock is required iff the job_id is reachable more than once.

    Both directions matter. Locking a single-use job_id re-creates the dead
    "locked by another pod" branch; NOT locking a re-dispatchable one is
    duplicate LLM spend plus duplicate written artifacts.
    """
    trees = _modules()
    locks = "acquire_job_lock" in _called_names(_task_def(trees, task))
    redispatchable = len(_dispatch_sites(trees, task)) > 1

    if redispatchable:
        assert locks, f"{task} is re-dispatchable but takes no job lock. {_REINSTATE}"
    else:
        assert not locks, (
            f"{task} takes a job lock on a job_id minted at its only dispatch site. "
            "That lock can never be contended, so its 'locked by another pod' branch "
            "is dead code advertising a guard that does not exist. Remove it, or give "
            "the lock an identity that is stable across replicas and re-dispatches."
        )


def test_reconciler_fails_stale_jobs_out_and_never_redispatches() -> None:
    """The one component that touches other requests' rows must not re-run work."""
    source = (_SRC / "services" / "reconciler.py").read_text()
    for task in _TASKS:
        assert task not in source, (
            f"reconciler.py references {task}. If it re-dispatches instead of "
            f"failing stale rows out, the job_id is no longer single-use. {_REINSTATE}"
        )
