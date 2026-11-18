"""Unit tests for enhanced StorageService features.

Covers part-type-aware file naming, backup/restore, find_script, and
directory helper methods added to StorageService.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.scripts.storage_service import StorageService

SAMPLE_TS = 'import { test } from "@playwright/test";'


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture()
def storage(tmp_path: Path) -> StorageService:
    """Provide a StorageService backed by a temporary directory."""
    svc = StorageService.__new__(StorageService)
    svc._local_path = tmp_path / "scripts"
    svc._local_path.mkdir()
    return svc


# ============================================================================
# Part-type-aware file naming
# ============================================================================


class TestStoreScriptNaming:
    """store_script should pick the correct filename based on part_type / source_type."""

    @pytest.mark.asyncio
    async def test_default_no_type_params(self, storage: StorageService) -> None:
        """Without part_type/source_type the file lands in the job root with .demo.spec.ts."""
        path = await storage.store_script("job-1", SAMPLE_TS, "ch01-part1")
        assert path.endswith("ch01-part1.demo.spec.ts")
        # Should be directly under the job dir, NOT under scripts/parts/
        assert "scripts/parts" not in path

    @pytest.mark.asyncio
    async def test_title_card(self, storage: StorageService) -> None:
        path = await storage.store_script("job-1", SAMPLE_TS, "intro", part_type="title_card")
        assert path.endswith("intro.ts")
        assert not path.endswith(".test.ts")
        assert not path.endswith(".demo.spec.ts")
        assert "scripts/parts" in path

    @pytest.mark.asyncio
    async def test_source_type_ios(self, storage: StorageService) -> None:
        path = await storage.store_script("job-1", SAMPLE_TS, "ch02-part1", source_type="ios")
        assert path.endswith("ch02-part1.test.ts")
        assert "scripts/parts" in path

    @pytest.mark.asyncio
    async def test_source_type_android(self, storage: StorageService) -> None:
        path = await storage.store_script("job-1", SAMPLE_TS, "ch03-part1", source_type="android")
        assert path.endswith("ch03-part1.test.ts")
        assert "scripts/parts" in path

    @pytest.mark.asyncio
    async def test_browser_recording_explicit(self, storage: StorageService) -> None:
        """Explicit browser_recording part_type still uses .demo.spec.ts."""
        path = await storage.store_script(
            "job-1", SAMPLE_TS, "ch04-part1", part_type="browser_recording"
        )
        assert path.endswith("ch04-part1.demo.spec.ts")
        assert "scripts/parts" in path

    @pytest.mark.asyncio
    async def test_title_card_overrides_source_type(self, storage: StorageService) -> None:
        """part_type=title_card takes precedence even if source_type is also set."""
        path = await storage.store_script(
            "job-1", SAMPLE_TS, "intro2", part_type="title_card", source_type="ios"
        )
        assert path.endswith("intro2.ts")

    @pytest.mark.asyncio
    async def test_content_is_stored_correctly(self, storage: StorageService) -> None:
        path = await storage.store_script("job-1", SAMPLE_TS, "ch01-part1", part_type="title_card")
        content = Path(path).read_text(encoding="utf-8")
        assert content == SAMPLE_TS

    @pytest.mark.asyncio
    async def test_backward_compat_positional_args(self, storage: StorageService) -> None:
        """Existing call-sites using three positional args must still work."""
        path = await storage.store_script("job-1", SAMPLE_TS, "legacy-part")
        assert path.endswith("legacy-part.demo.spec.ts")
        assert Path(path).exists()


# ============================================================================
# _resolve_script_filename
# ============================================================================


class TestResolveScriptFilename:
    def test_title_card(self, storage: StorageService) -> None:
        assert storage._resolve_script_filename("p1", "title_card", None) == "p1.ts"

    def test_ios(self, storage: StorageService) -> None:
        assert storage._resolve_script_filename("p1", None, "ios") == "p1.test.ts"

    def test_android(self, storage: StorageService) -> None:
        assert storage._resolve_script_filename("p1", None, "android") == "p1.test.ts"

    def test_browser_recording(self, storage: StorageService) -> None:
        assert (
            storage._resolve_script_filename("p1", "browser_recording", None) == "p1.demo.spec.ts"
        )

    def test_none_none(self, storage: StorageService) -> None:
        assert storage._resolve_script_filename("p1", None, None) == "p1.demo.spec.ts"

    def test_title_card_beats_ios(self, storage: StorageService) -> None:
        assert storage._resolve_script_filename("p1", "title_card", "ios") == "p1.ts"


# ============================================================================
# Backup / Restore
# ============================================================================


class TestBackupRestore:
    @pytest.mark.asyncio
    async def test_backup_creates_file(self, storage: StorageService) -> None:
        path_str = await storage.store_script("job-1", SAMPLE_TS, "ch01-part1")
        script_path = Path(path_str)
        backup_path = storage.backup_script(script_path)
        assert backup_path.exists()
        assert backup_path.name == "ch01-part1.demo.spec.ts.backup"
        assert backup_path.read_text(encoding="utf-8") == SAMPLE_TS

    @pytest.mark.asyncio
    async def test_backup_preserves_original(self, storage: StorageService) -> None:
        path_str = await storage.store_script("job-1", SAMPLE_TS, "part1")
        script_path = Path(path_str)
        storage.backup_script(script_path)
        assert script_path.exists()
        assert script_path.read_text(encoding="utf-8") == SAMPLE_TS

    @pytest.mark.asyncio
    async def test_restore_success(self, storage: StorageService) -> None:
        path_str = await storage.store_script("job-1", SAMPLE_TS, "part1")
        script_path = Path(path_str)
        storage.backup_script(script_path)

        # Overwrite original with different content
        script_path.write_text("// modified", encoding="utf-8")
        assert script_path.read_text(encoding="utf-8") == "// modified"

        # Restore should bring back original content
        result = storage.restore_script(script_path)
        assert result is True
        assert script_path.read_text(encoding="utf-8") == SAMPLE_TS

    def test_restore_no_backup(self, storage: StorageService, tmp_path: Path) -> None:
        script_path = tmp_path / "nonexistent.demo.spec.ts"
        script_path.write_text("// some content", encoding="utf-8")
        result = storage.restore_script(script_path)
        assert result is False

    @pytest.mark.asyncio
    async def test_backup_title_card(self, storage: StorageService) -> None:
        """Backup works correctly for .ts extension files (title cards)."""
        path_str = await storage.store_script("job-1", SAMPLE_TS, "intro", part_type="title_card")
        script_path = Path(path_str)
        backup_path = storage.backup_script(script_path)
        assert backup_path.name == "intro.ts.backup"
        assert backup_path.exists()


# ============================================================================
# find_script
# ============================================================================


class TestFindScript:
    @pytest.mark.asyncio
    async def test_find_in_job_root(self, storage: StorageService) -> None:
        """Default store_script places files in job root; find_script should locate them."""
        await storage.store_script("job-1", SAMPLE_TS, "ch01-part1")
        job_dir = storage.get_job_dir("job-1")
        found = storage.find_script(job_dir, "ch01-part1")
        assert found is not None
        assert found.name == "ch01-part1.demo.spec.ts"

    @pytest.mark.asyncio
    async def test_find_in_parts_dir(self, storage: StorageService) -> None:
        """Scripts placed under scripts/parts/ should also be found."""
        await storage.store_script("job-1", SAMPLE_TS, "ch02-part1", part_type="browser_recording")
        job_dir = storage.get_job_dir("job-1")
        found = storage.find_script(job_dir, "ch02-part1")
        assert found is not None
        assert "scripts/parts" in str(found)

    @pytest.mark.asyncio
    async def test_find_title_card(self, storage: StorageService) -> None:
        await storage.store_script("job-1", SAMPLE_TS, "intro", part_type="title_card")
        job_dir = storage.get_job_dir("job-1")
        found = storage.find_script(job_dir, "intro")
        assert found is not None
        assert found.name == "intro.ts"

    @pytest.mark.asyncio
    async def test_find_test_ts(self, storage: StorageService) -> None:
        await storage.store_script("job-1", SAMPLE_TS, "mobile-part", source_type="ios")
        job_dir = storage.get_job_dir("job-1")
        found = storage.find_script(job_dir, "mobile-part")
        assert found is not None
        assert found.name == "mobile-part.test.ts"

    def test_find_nonexistent(self, storage: StorageService) -> None:
        job_dir = storage.get_job_dir("job-1")
        found = storage.find_script(job_dir, "does-not-exist")
        assert found is None

    @pytest.mark.asyncio
    async def test_find_prefers_demo_spec_ts(self, storage: StorageService) -> None:
        """When multiple extensions exist, .demo.spec.ts should be found first."""
        job_dir = storage.get_job_dir("job-1")
        # Create both variants in the job root
        (job_dir / "multi.demo.spec.ts").write_text("// demo", encoding="utf-8")
        (job_dir / "multi.ts").write_text("// plain", encoding="utf-8")
        found = storage.find_script(job_dir, "multi")
        assert found is not None
        assert found.name == "multi.demo.spec.ts"


# ============================================================================
# Directory helpers
# ============================================================================


class TestDirectoryHelpers:
    def test_get_scripts_dir(self, storage: StorageService) -> None:
        job_dir = storage.get_job_dir("job-1")
        scripts_dir = storage.get_scripts_dir(job_dir)
        assert scripts_dir.exists()
        assert scripts_dir.is_dir()
        assert scripts_dir == job_dir / "scripts"

    def test_get_parts_dir(self, storage: StorageService) -> None:
        job_dir = storage.get_job_dir("job-1")
        parts_dir = storage.get_parts_dir(job_dir)
        assert parts_dir.exists()
        assert parts_dir.is_dir()
        assert parts_dir == job_dir / "scripts" / "parts"

    def test_get_scripts_dir_idempotent(self, storage: StorageService) -> None:
        job_dir = storage.get_job_dir("job-1")
        d1 = storage.get_scripts_dir(job_dir)
        d2 = storage.get_scripts_dir(job_dir)
        assert d1 == d2

    def test_get_parts_dir_idempotent(self, storage: StorageService) -> None:
        job_dir = storage.get_job_dir("job-1")
        d1 = storage.get_parts_dir(job_dir)
        d2 = storage.get_parts_dir(job_dir)
        assert d1 == d2

    def test_get_parts_dir_creates_parent_scripts_dir(self, storage: StorageService) -> None:
        """get_parts_dir should also create the parent scripts/ directory."""
        job_dir = storage.get_job_dir("job-1")
        parts_dir = storage.get_parts_dir(job_dir)
        assert (job_dir / "scripts").exists()
        assert parts_dir.exists()
