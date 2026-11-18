"""Script validation service -- TypeScript compilation, Playwright test checks."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import time
from pathlib import Path

from src.domain.script import ValidationResult
from src.services.scripts.script_safety import scan_script_source

logger = logging.getLogger(__name__)

# A file-attributed tsc diagnostic: ``src/x.ts(12,7): error TS2304: ...``.
# Its presence proves tsc reached the type-checking phase. Global errors
# (TS5058 "tsconfig not found", TS6053) carry no ``(line,col)`` and therefore
# do NOT count as evidence that the project was checked.
_TS_FILE_DIAGNOSTIC_RE = re.compile(r"\(\d+,\d+\):\s*error\s+TS\d+")


class ValidationService:
    """Validate generated Playwright scripts by running TypeScript compiler and Playwright."""

    def __init__(
        self,
        *,
        ts_compile_timeout: int = 60,
        discover_timeout: int = 30,
        dry_run_timeout: int = 120,
        error_truncation_limit: int = 2000,
    ) -> None:
        self._ts_timeout = ts_compile_timeout
        self._discover_timeout = discover_timeout
        self._dry_run_timeout = dry_run_timeout
        self._error_limit = error_truncation_limit

    async def _run_command(self, args: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
        """Run a subprocess command asynchronously.

        The child is started in its own session/process-group so a timeout can
        kill the whole tree (``npx`` -> ``node`` -> ``chromium``), not just the
        direct child — otherwise orphaned browser/node processes accumulate on
        every timed-out validation. Captured output is bounded only by the
        container's cgroup memory limit; the per-command timeout bounds runtime.
        """
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = (
                stderr.decode("utf-8", errors="replace") + stdout.decode("utf-8", errors="replace")
            ).strip()
            return proc.returncode == 0, output
        except TimeoutError:
            await self._kill_process_tree(proc)
            return False, f"Command timed out after {timeout}s"
        except FileNotFoundError as e:
            return False, f"Command not found: {e}"

    @staticmethod
    async def _kill_process_tree(proc: asyncio.subprocess.Process | None) -> None:
        """SIGKILL the process group of *proc*, tolerating an already-dead child."""
        if proc is None or proc.returncode is not None:
            return
        pid = proc.pid
        # Mock-pid broadcast guard: under unit tests ``proc`` may be a mock whose
        # ``.pid`` is not a real int (a ``MagicMock`` coerces via ``__index__`` to
        # 1). ``os.getpgid(1)`` resolves init's group, so ``os.killpg(...)`` would
        # become ``kill(-1, sig)`` — a broadcast that SIGKILLs the whole process
        # tree (e.g. the CI runner agent). Only signal a real child pid.
        if isinstance(pid, int) and pid > 1:
            with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                os.killpg(os.getpgid(pid), signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()

    async def check_typescript(self, scripts_dir: Path) -> tuple[bool, str]:
        """Check if TypeScript compiles without errors."""
        return await self._run_command(
            ["npx", "tsc", "--noEmit", "--incremental", "false"],
            cwd=scripts_dir,
            timeout=self._ts_timeout,
        )

    async def check_typescript_file(self, scripts_dir: Path, part_id: str) -> tuple[bool, str]:
        """Check a single part file for TypeScript errors.

        Honesty contract: "the checker did not run" is NOT "the code
        compiles". ``tsc`` can fail wholesale — a timeout that kills the
        process group, a missing ``npx``, an unreadable ``tsconfig.json``, an
        OOM — and produce output in which no line mentions ``parts/<part_id>``.
        Filtering that output and finding nothing used to return ``(True, "")``,
        so the job was accepted with ``compiles=true`` and zero errors having
        never been type-checked at all.

        The discriminator is whether the run produced at least one
        FILE-ATTRIBUTED diagnostic (``file(line,col): error TSxxxx``). Only
        then did ``tsc`` actually type-check the project, which makes "no line
        names this part" a real, trustworthy pass — that is the ordinary case
        where a SIBLING part is broken and this one is fine. A non-zero exit
        with no attributed diagnostic is a could-not-check and fails closed.
        """
        success, output = await self._run_command(
            ["npx", "tsc", "--project", "tsconfig.json", "--noEmit", "--incremental", "false"],
            cwd=scripts_dir,
            timeout=self._ts_timeout,
        )
        if success:
            return True, output

        if not _TS_FILE_DIAGNOSTIC_RE.search(output):
            # tsc never got as far as type-checking anything. Fail closed.
            reason = output.strip() or "no output"
            return False, (
                "TypeScript check could not run (tsc exited non-zero with no file-attributed "
                f"diagnostics — the script was NOT type-checked): {reason}"
            )

        # tsc ran and produced diagnostics: filter to errors referencing this file.
        target = f"parts/{part_id}"
        filtered = [line for line in output.splitlines() if target in line]
        if not filtered:
            return True, ""
        return False, "\n".join(filtered)

    async def check_discoverable(self, scripts_dir: Path, part_id: str) -> tuple[bool, str]:
        """Check if a test is discoverable by Playwright."""
        return await self._run_command(
            [
                "npx",
                "playwright",
                "test",
                "--list",
                "--config=playwright.recording.config.ts",
                f"parts/{part_id}.*",
            ],
            cwd=scripts_dir,
            timeout=self._discover_timeout,
        )

    async def dry_run(self, scripts_dir: Path, part_id: str) -> tuple[bool, str]:
        """Run a test headless without video capture."""
        return await self._run_command(
            [
                "npx",
                "playwright",
                "test",
                f"parts/{part_id}.*",
                "--config=playwright.recording.config.ts",
                "--reporter=list",
            ],
            cwd=scripts_dir,
            timeout=self._dry_run_timeout,
        )

    @staticmethod
    def _read_part_source(scripts_dir: Path, part_id: str) -> str | None:
        """Return the TypeScript source for *part_id*, or None if not found."""
        parts_dir = scripts_dir / "parts"
        for ext in (".demo.spec.ts", ".test.ts", ".spec.ts", ".ts"):
            candidate = parts_dir / f"{part_id}{ext}"
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")
        return None

    async def _safety_scan(self, scripts_dir: Path, part_id: str) -> list[str]:
        """Static safety scan of the script before it is executed.

        Returns a list of human-readable violation messages. Empty means the
        conservative scan found nothing — execution may proceed.
        """
        source = await asyncio.to_thread(self._read_part_source, scripts_dir, part_id)
        if source is None:
            return []
        return [f"{v.message} (matched: {v.snippet!r})" for v in scan_script_source(source)]

    async def validate(self, scripts_dir: Path, part_id: str) -> ValidationResult:
        """Run all validation checks for a single script."""
        t0 = time.monotonic()
        logger.info("Validation start: part_id=%s scripts_dir=%s", part_id, scripts_dir)

        # Fail-closed: scan the LLM-generated source for dangerous constructs
        # BEFORE any subprocess imports/executes it. `playwright test --list`
        # alone imports the spec module (running top-level code), so the gate
        # must precede discovery and dry-run, not just dry-run.
        safety_violations = await self._safety_scan(scripts_dir, part_id)
        if safety_violations:
            logger.warning(
                "Script rejected by safety scan: part_id=%s violations=%s",
                part_id,
                safety_violations,
            )
            return ValidationResult(
                compiles=False,
                discoverable=False,
                runs_headless=False,
                accepted=False,
                errors=[
                    "Script rejected by safety scan (not executed): " + "; ".join(safety_violations)
                ],
            )

        compiles, compile_err = await self.check_typescript_file(scripts_dir, part_id)
        logger.info("TypeScript check: part_id=%s compiles=%s", part_id, compiles)

        discoverable, discover_err = await self.check_discoverable(scripts_dir, part_id)
        logger.info("Discover check: part_id=%s discoverable=%s", part_id, discoverable)

        runs_headless = False
        run_err = "Not discoverable"
        if discoverable:
            runs_headless, run_err = await self.dry_run(scripts_dir, part_id)
            logger.info("Dry run check: part_id=%s runs_headless=%s", part_id, runs_headless)

        errors = []
        limit = self._error_limit
        if not compiles:
            errors.append(f"TypeScript error: {compile_err[:limit]}")
        if not discoverable:
            errors.append(f"Not discoverable: {discover_err[:limit]}")
        if not runs_headless:
            errors.append(f"Dry run failed: {run_err[:limit]}")

        accepted = compiles and discoverable and runs_headless
        logger.info(
            "Validation done: part_id=%s accepted=%s in %.2fs",
            part_id,
            accepted,
            time.monotonic() - t0,
        )
        return ValidationResult(
            compiles=compiles,
            discoverable=discoverable,
            runs_headless=runs_headless,
            accepted=accepted,
            errors=errors if errors else None,
        )
