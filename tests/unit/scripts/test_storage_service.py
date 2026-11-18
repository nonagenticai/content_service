"""Tests for services.scripts.storage_service."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.scripts.storage_service import StorageService


class TestStorageServiceInit:
    def test_default_path(self):
        svc = StorageService()
        assert svc._local_path == Path("/app/data")

    def test_custom_path(self):
        svc = StorageService(local_path="/tmp/custom")
        assert svc._local_path == Path("/tmp/custom")


class TestInitialize:
    async def test_creates_directory(self, tmp_path):
        target = tmp_path / "new_dir"
        svc = StorageService(local_path=str(target))
        await svc.initialize()
        assert target.is_dir()


class TestGetJobDir:
    def test_creates_job_dir(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        job_dir = svc.get_job_dir("job-123")
        assert job_dir.is_dir()
        assert job_dir.name == "job-123"


class TestResolveScriptFilename:
    def test_title_card(self):
        assert StorageService._resolve_script_filename("p1", "title_card", None) == "p1.ts"

    def test_ios(self):
        assert StorageService._resolve_script_filename("p1", "browser", "ios") == "p1.test.ts"

    def test_android(self):
        assert StorageService._resolve_script_filename("p1", "browser", "android") == "p1.test.ts"

    def test_default(self):
        assert (
            StorageService._resolve_script_filename("p1", "browser_recording", "web")
            == "p1.demo.spec.ts"
        )

    def test_none_types(self):
        assert StorageService._resolve_script_filename("p1", None, None) == "p1.demo.spec.ts"


class TestStoreScript:
    async def test_store_with_part_type(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        path = await svc.store_script("job-1", "code", "p1", part_type="browser_recording")
        assert Path(path).exists()
        assert Path(path).read_text() == "code"

    async def test_store_without_part_type(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        path = await svc.store_script("job-1", "code", "p1")
        assert Path(path).name == "p1.demo.spec.ts"


class TestStorePrompt:
    async def test_store_prompt(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        path = await svc.store_prompt("job-1", "prompt text", "p1")
        assert Path(path).exists()
        assert Path(path).name == ".prompt-p1.md"


class TestRetrieve:
    async def test_existing_file(self, tmp_path):
        f = tmp_path / "test.ts"
        f.write_text("content")
        svc = StorageService(local_path=str(tmp_path))
        result = await svc.retrieve(str(f))
        assert result == "content"

    async def test_missing_file(self):
        svc = StorageService()
        result = await svc.retrieve("/nonexistent/path.ts")
        assert result is None


class TestRetrieveBytes:
    async def test_existing_file(self, tmp_path):
        f = tmp_path / "test.bin"
        f.write_bytes(b"binary data")
        svc = StorageService(local_path=str(tmp_path))
        result = await svc.retrieve_bytes(str(f))
        assert result == b"binary data"

    async def test_missing_file(self):
        svc = StorageService()
        result = await svc.retrieve_bytes("/nonexistent/path.bin")
        assert result is None


class TestBackupRestore:
    def test_backup_creates_copy(self, tmp_path):
        f = tmp_path / "script.ts"
        f.write_text("original")
        svc = StorageService(local_path=str(tmp_path))
        backup = svc.backup_script(f)
        assert backup.exists()
        assert backup.suffix == ".backup"
        assert backup.read_text() == "original"

    def test_restore_success(self, tmp_path):
        f = tmp_path / "script.ts"
        f.write_text("modified")
        backup = f.with_suffix(".ts.backup")
        backup.write_text("original")

        svc = StorageService(local_path=str(tmp_path))
        assert svc.restore_script(f) is True
        assert f.read_text() == "original"

    def test_restore_no_backup(self, tmp_path):
        f = tmp_path / "script.ts"
        f.write_text("content")
        svc = StorageService(local_path=str(tmp_path))
        assert svc.restore_script(f) is False


class TestFindScript:
    def test_finds_demo_spec(self, tmp_path):
        f = tmp_path / "p1.demo.spec.ts"
        f.write_text("code")
        svc = StorageService(local_path=str(tmp_path))
        result = svc.find_script(tmp_path, "p1")
        assert result == f

    def test_finds_in_scripts_parts(self, tmp_path):
        parts = tmp_path / "scripts" / "parts"
        parts.mkdir(parents=True)
        f = parts / "p1.demo.spec.ts"
        f.write_text("code")
        svc = StorageService(local_path=str(tmp_path))
        result = svc.find_script(tmp_path, "p1")
        assert result == f

    def test_not_found(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        assert svc.find_script(tmp_path, "missing") is None


class TestDirectoryHelpers:
    def test_get_scripts_dir(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        d = svc.get_scripts_dir(tmp_path)
        assert d.is_dir()
        assert d.name == "scripts"

    def test_get_parts_dir(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        d = svc.get_parts_dir(tmp_path)
        assert d.is_dir()
        assert d == tmp_path / "scripts" / "parts"


class TestPathConfinement:
    """Defense-in-depth: retrieve/retrieve_bytes must refuse paths outside root."""

    async def test_retrieve_rejects_absolute_outside_root(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive", encoding="utf-8")
        svc = StorageService(local_path=str(root))
        assert await svc.retrieve(str(outside)) is None
        assert outside.read_text(encoding="utf-8") == "sensitive"

    async def test_retrieve_rejects_traversal(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "secret.txt"
        outside.write_text("sensitive", encoding="utf-8")
        svc = StorageService(local_path=str(root))
        assert await svc.retrieve("../secret.txt") is None
        assert outside.read_text(encoding="utf-8") == "sensitive"

    async def test_retrieve_bytes_rejects_outside_root(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "secret.bin"
        outside.write_bytes(b"sensitive")
        svc = StorageService(local_path=str(root))
        assert await svc.retrieve_bytes(str(outside)) is None
        assert outside.read_bytes() == b"sensitive"

    async def test_legitimate_retrieve_still_works(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        await svc.initialize()
        stored = await svc.store_script("job-1", "console.log('hi')", "p1")
        # Returns string path; retrieve must be able to read it back.
        result = await svc.retrieve(stored)
        assert result == "console.log('hi')"


class TestStoreWriteConfinement:
    """Defense-in-depth: store_* must refuse to write outside the job dir."""

    async def test_store_script_rejects_traversal_part_id(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        outside = tmp_path / "outside.txt"
        with pytest.raises(ValueError, match="outside job directory"):
            # Bypasses the schema validator (direct service call) — the write
            # layer must still refuse to escape the job directory.
            await svc.store_script("job-1", "PWNED", "../../outside")
        assert not outside.exists()

    async def test_store_script_rejects_traversal_with_parts_dir(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        with pytest.raises(ValueError, match="outside job directory"):
            await svc.store_script(
                "job-1",
                "PWNED",
                "../../../../../../tmp/evil",
                part_type="browser_recording",
                source_type="web",
            )

    async def test_store_prompt_rejects_traversal_part_id(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        with pytest.raises(ValueError, match="outside job directory"):
            await svc.store_prompt("job-1", "data", "../" * 12 + "tmp/evil")

    async def test_store_script_normal_id_succeeds(self, tmp_path):
        svc = StorageService(local_path=str(tmp_path))
        path = await svc.store_script("job-1", "ok", "part-1")
        assert Path(path).exists()
        assert Path(path).is_relative_to((tmp_path / "job-1").resolve())
