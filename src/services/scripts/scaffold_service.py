"""Scaffold service -- copies TypeScript project templates into job directories.

Handles both web (Playwright) and mobile (WebdriverIO) scaffold variants.
Theme injection is folded in (was previously a separate theme_injector module).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import signal
import tempfile
import time
from pathlib import Path

from src.scaffold import get_scaffold_dir
from src.scaffold_mobile import get_mobile_scaffold_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Theme injection (folded from theme_injector.py)
# ---------------------------------------------------------------------------

_DEFAULT_THEME_RE = re.compile(
    r"(const DEFAULT_THEME:\s*DemoTheme\s*=\s*)\{.*?\}",
    re.DOTALL,
)

_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")

THEME_COLOR_FIELDS = (
    "primaryColor",
    "backgroundColor",
    "textColor",
    "accentColor",
    "subtitleColor",
    "gradientStart",
    "gradientEnd",
)


def _validate_color(value: object, field_name: str) -> None:
    if not isinstance(value, str):
        raise ValueError(f"Theme field '{field_name}' must be a string, got {type(value).__name__}")
    if not _HEX_COLOR_RE.match(value.strip()):
        raise ValueError(f"Theme field '{field_name}' has invalid color value '{value}'.")


def _ts_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("`", "\\`")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
    )


def _inject_theme(demo_helpers_path: Path, theme: dict) -> None:
    """Replace DEFAULT_THEME in demo-helpers.ts with user-supplied theme colors."""
    if not demo_helpers_path.exists():
        raise FileNotFoundError(
            f"demo-helpers.ts not found at {demo_helpers_path}. Was the scaffold copied first?"
        )

    # _validate_color validates value.strip(), so we must inject the SAME
    # normalized (stripped) form — injecting the raw value would smuggle
    # surrounding whitespace / a literal '\n' (via _ts_escape) into the TS
    # color string and corrupt the runtime CSS color.
    norm: dict[str, str] = {}
    for field_name in THEME_COLOR_FIELDS:
        if field_name not in theme:
            raise KeyError(f"Required theme field '{field_name}' is missing from theme dict.")
        _validate_color(theme[field_name], field_name)
        norm[field_name] = theme[field_name].strip()

    content = demo_helpers_path.read_text()

    new_theme_obj = (
        "{\n"
        f"  primaryColor: '{_ts_escape(norm['primaryColor'])}',\n"
        f"  backgroundColor: '{_ts_escape(norm['backgroundColor'])}',\n"
        f"  textColor: '{_ts_escape(norm['textColor'])}',\n"
        f"  accentColor: '{_ts_escape(norm['accentColor'])}',\n"
        f"  subtitleColor: '{_ts_escape(norm['subtitleColor'])}',\n"
        f"  gradientStart: '{_ts_escape(norm['gradientStart'])}',\n"
        f"  gradientEnd: '{_ts_escape(norm['gradientEnd'])}',\n"
        "}"
    )

    new_content, count = _DEFAULT_THEME_RE.subn(
        lambda m: m.group(1) + new_theme_obj, content, count=1
    )
    if count == 0:
        raise ValueError(
            f"Could not find DEFAULT_THEME block in {demo_helpers_path}. "
            "The TypeScript source may have changed in an incompatible way."
        )

    with tempfile.NamedTemporaryFile(
        mode="w",
        dir=demo_helpers_path.parent,
        suffix=".tmp",
        delete=False,
    ) as tmp:
        tmp.write(new_content)
        tmp_path = Path(tmp.name)
    tmp_path.rename(demo_helpers_path)
    logger.debug("Injected theme into %s", demo_helpers_path)


# ---------------------------------------------------------------------------
# ScaffoldService
# ---------------------------------------------------------------------------


class ScaffoldService:
    """Copy scaffold templates and prepare job working directories."""

    def __init__(
        self,
        npm_install_enabled: bool = True,
        npm_install_timeout: int = 120,
    ) -> None:
        self.npm_install_enabled = npm_install_enabled
        self.npm_install_timeout = npm_install_timeout

    async def scaffold(
        self,
        job_dir: Path,
        theme: dict | None = None,
    ) -> Path:
        """Copy the web (Playwright) scaffold into *job_dir*/scripts/."""
        t0 = time.monotonic()
        scaffold_src = get_scaffold_dir()
        scripts_dir = job_dir / "scripts"

        logger.info("Copying scaffold from %s to %s", scaffold_src, scripts_dir)

        await asyncio.to_thread(
            shutil.copytree,
            scaffold_src,
            scripts_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "__init__.py"),
            dirs_exist_ok=True,
        )

        parts_dir = scripts_dir / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)

        if self.npm_install_enabled:
            await self._npm_install(scripts_dir)

        if theme is not None:
            demo_helpers_path = scripts_dir / "helpers" / "demo-helpers.ts"
            await asyncio.to_thread(_inject_theme, demo_helpers_path, theme)
            logger.info("Theme injected into %s", demo_helpers_path)

        logger.info("Scaffold ready at %s in %.2fs", scripts_dir, time.monotonic() - t0)
        return scripts_dir

    async def scaffold_mobile(self, job_dir: Path) -> Path:
        """Copy the mobile (WebdriverIO) scaffold into *job_dir*/scripts/."""
        t0 = time.monotonic()
        scaffold_src = get_mobile_scaffold_dir()
        scripts_dir = job_dir / "scripts"

        logger.info("Copying mobile scaffold from %s to %s", scaffold_src, scripts_dir)

        await asyncio.to_thread(
            shutil.copytree,
            scaffold_src,
            scripts_dir,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "__init__.py"),
            dirs_exist_ok=True,
        )

        parts_dir = scripts_dir / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)

        if self.npm_install_enabled:
            await self._npm_install(scripts_dir)

        logger.info("Mobile scaffold ready at %s in %.2fs", scripts_dir, time.monotonic() - t0)
        return scripts_dir

    async def _npm_install(self, scripts_dir: Path) -> None:
        """Run ``npm install`` in the given scripts directory."""
        logger.info("Running npm install in %s", scripts_dir)
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "npm",
                "install",
                cwd=str(scripts_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.npm_install_timeout,
            )
        except TimeoutError as exc:
            logger.error("npm install timed out after %ds", self.npm_install_timeout)
            # Kill the whole process group: npm spawns node/child fetchers that
            # outlive a bare child .kill() and leak as orphans on timeout.
            await self._kill_process_tree(proc)
            raise RuntimeError(f"npm install timed out after {self.npm_install_timeout}s") from exc

        if proc.returncode != 0:
            stderr_text = stderr.decode(errors="replace").strip()
            logger.error("npm install failed (exit %d):\n%s", proc.returncode, stderr_text)
            raise RuntimeError(
                f"npm install failed with exit code {proc.returncode}: {stderr_text}"
            )

        logger.debug(
            "npm install succeeded: %s",
            stdout.decode(errors="replace").strip()[:200],
        )

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
