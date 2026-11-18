"""File storage service for generated scripts."""

from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


class StorageService:
    """Manages file storage for generated scripts."""

    def __init__(self, local_path: str = "/app/data") -> None:
        self._local_path = Path(local_path)

    async def initialize(self) -> None:
        """Create the storage root directory if it does not exist."""
        self._local_path.mkdir(parents=True, exist_ok=True)
        logger.info("Storage initialized at %s", self._local_path)

    def get_job_dir(self, job_id: str) -> Path:
        """Return the job-specific directory, creating it if needed."""
        job_dir = self._local_path / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        return job_dir

    @staticmethod
    def _resolve_script_filename(
        part_id: str, part_type: str | None, source_type: str | None
    ) -> str:
        """Determine the correct filename based on part_type and source_type."""
        if part_type == "title_card":
            return f"{part_id}.ts"
        if source_type in ("ios", "android"):
            return f"{part_id}.test.ts"
        return f"{part_id}.demo.spec.ts"

    @staticmethod
    def _confine_write(file_path: Path, root: Path) -> Path:
        """Resolve *file_path* and refuse anything that escapes *root*.

        Defense in depth for the write side: ``part_id`` is validated at the
        schema edge, but a regression there (or a non-HTTP caller) must still
        never be able to write outside the job directory. A traversal raises
        ``ValueError`` so the job is marked failed rather than corrupting an
        unrelated path.
        """
        resolved_root = root.resolve()
        resolved = file_path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise ValueError(f"Refusing to write outside job directory: {file_path}")
        return resolved

    async def store_script(
        self,
        job_id: str,
        source: str,
        part_id: str,
        *,
        part_type: str | None = None,
        source_type: str | None = None,
    ) -> str:
        """Store a generated TypeScript script and return the path."""

        def _write() -> str:
            job_dir = self.get_job_dir(job_id)
            if part_type is not None or source_type is not None:
                parts_dir = self.get_parts_dir(job_dir)
                filename = self._resolve_script_filename(part_id, part_type, source_type)
                file_path = parts_dir / filename
            else:
                file_path = job_dir / f"{part_id}.demo.spec.ts"
            file_path = self._confine_write(file_path, job_dir)
            file_path.write_text(source, encoding="utf-8")
            return str(file_path)

        path = await asyncio.to_thread(_write)
        logger.info("Stored script: %s (%d chars)", path, len(source))
        return path

    async def store_prompt(self, job_id: str, prompt: str, part_id: str) -> str:
        """Store the generation prompt for debugging."""

        def _write() -> str:
            job_dir = self.get_job_dir(job_id)
            file_path = self._confine_write(job_dir / f".prompt-{part_id}.md", job_dir)
            file_path.write_text(prompt, encoding="utf-8")
            return str(file_path)

        return await asyncio.to_thread(_write)

    def _safe_local_path(self, raw: str) -> Path | None:
        """Resolve ``raw`` and reject anything outside ``self._local_path``.

        Defense in depth: the store methods only emit paths under the
        configured root, so the DB row should always round-trip cleanly. A
        rejection here means either a stale row from a different
        configuration or a tampered DB entry — return ``None`` (treated as
        "not found" by callers) rather than honouring the traversal.
        """
        root = self._local_path.resolve()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self._local_path / raw
        try:
            resolved = candidate.resolve()
        except OSError, RuntimeError:
            logger.warning("Could not resolve storage path: %s", raw)
            return None
        if not resolved.is_relative_to(root):
            logger.warning("Rejected path outside storage root: %s", raw)
            return None
        return resolved

    async def retrieve(self, path: str) -> str | None:
        """Read and return file contents as text, or None if missing."""
        file_path = self._safe_local_path(path)
        if file_path is None:
            return None

        def _read() -> str | None:
            if file_path.exists():
                return file_path.read_text(encoding="utf-8")
            return None

        return await asyncio.to_thread(_read)

    async def retrieve_bytes(self, path: str) -> bytes | None:
        """Read and return file contents as bytes, or None if missing."""
        file_path = self._safe_local_path(path)
        if file_path is None:
            return None

        def _read() -> bytes | None:
            if file_path.exists():
                return file_path.read_bytes()
            return None

        return await asyncio.to_thread(_read)

    # ------------------------------------------------------------------
    # Backup / Restore
    # ------------------------------------------------------------------

    def backup_script(self, script_path: Path) -> Path:
        """Create a backup copy of *script_path*."""
        backup_path = script_path.with_suffix(script_path.suffix + ".backup")
        shutil.copy2(script_path, backup_path)
        logger.info("Backed up %s -> %s", script_path, backup_path)
        return backup_path

    def restore_script(self, script_path: Path) -> bool:
        """Restore *script_path* from its ``.backup`` sibling."""
        backup_path = script_path.with_suffix(script_path.suffix + ".backup")
        if not backup_path.exists():
            logger.warning("No backup found at %s", backup_path)
            return False
        shutil.copy2(backup_path, script_path)
        logger.info("Restored %s from %s", script_path, backup_path)
        return True

    # ------------------------------------------------------------------
    # Lookup helpers
    # ------------------------------------------------------------------

    def find_script(self, job_dir: Path, part_id: str) -> Path | None:
        """Search for a script file matching *part_id* in *job_dir*."""
        extensions = [".demo.spec.ts", ".test.ts", ".ts"]
        search_dirs = [job_dir, job_dir / "scripts" / "parts"]
        for search_dir in search_dirs:
            for ext in extensions:
                candidate = search_dir / f"{part_id}{ext}"
                if candidate.exists():
                    return candidate
        return None

    # ------------------------------------------------------------------
    # Directory helpers
    # ------------------------------------------------------------------

    def get_scripts_dir(self, job_dir: Path) -> Path:
        """Return ``{job_dir}/scripts/``, creating it if necessary."""
        scripts_dir = job_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        return scripts_dir

    def part_script_path(
        self,
        scripts_dir: Path,
        part_id: str,
        *,
        part_type: str | None = None,
        source_type: str | None = None,
    ) -> Path:
        """Return the canonical ``{scripts_dir}/parts/<file>`` path for a part.

        The filename matches what :meth:`store_script` writes for the same
        ``part_type`` / ``source_type``, so a durable-state rebuild lands the
        bytes exactly where validation and improvement look for them. Does not
        create the file — only computes the path.
        """
        return (
            scripts_dir / "parts" / self._resolve_script_filename(part_id, part_type, source_type)
        )

    def get_parts_dir(self, job_dir: Path) -> Path:
        """Return ``{job_dir}/scripts/parts/``, creating it if necessary."""
        parts_dir = job_dir / "scripts" / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        return parts_dir
