"""Tests for src/services/validation_service.py — Script validation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scripts.validation_service import ValidationService


@pytest.fixture()
def validator() -> ValidationService:
    """Create a ValidationService with test timeouts."""
    return ValidationService(
        ts_compile_timeout=30,
        discover_timeout=15,
        dry_run_timeout=60,
        error_truncation_limit=500,
    )


# ── _run_command ───────────────────────────────────────────────────────


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_successful_command(self, validator: ValidationService) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=(b"OK", b"")):
                success, _output = await validator._run_command(
                    ["echo", "test"], Path("/tmp"), timeout=10
                )
                assert success is True

    @pytest.mark.asyncio
    async def test_failed_command(self, validator: ValidationService) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", return_value=(b"", b"error output")):
                success, _output = await validator._run_command(["false"], Path("/tmp"), timeout=10)
                assert success is False

    @pytest.mark.asyncio
    async def test_timeout_returns_false(self, validator: ValidationService) -> None:
        mock_proc = MagicMock()
        mock_proc.communicate = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with patch("asyncio.wait_for", side_effect=TimeoutError):
                success, output = await validator._run_command(
                    ["slow-cmd"], Path("/tmp"), timeout=1
                )
                assert success is False
                assert "timed out" in output

    @pytest.mark.asyncio
    async def test_file_not_found(self, validator: ValidationService) -> None:
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("npx not found"),
        ):
            success, output = await validator._run_command(["npx", "tsc"], Path("/tmp"), timeout=10)
            assert success is False
            assert "not found" in output.lower()

    @pytest.mark.asyncio
    async def test_timeout_kills_process_group(self, validator: ValidationService) -> None:
        """On timeout the whole process group is SIGKILLed (no orphan leak)."""
        mock_proc = MagicMock()
        mock_proc.returncode = None  # still running -> must be killed
        mock_proc.pid = 4242
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()
        mock_proc.communicate = MagicMock()

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=TimeoutError),
            patch("os.getpgid", return_value=4242) as mock_getpgid,
            patch("os.killpg") as mock_killpg,
        ):
            success, output = await validator._run_command(["slow"], Path("/tmp"), timeout=1)

        assert success is False
        assert "timed out" in output
        mock_getpgid.assert_called_once_with(4242)
        mock_killpg.assert_called_once()
        mock_proc.wait.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_skips_kill_when_already_exited(
        self, validator: ValidationService
    ) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0  # already finished
        mock_proc.pid = 4243
        mock_proc.wait = AsyncMock()
        mock_proc.communicate = MagicMock()

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            patch("asyncio.wait_for", side_effect=TimeoutError),
            patch("os.killpg") as mock_killpg,
        ):
            success, _output = await validator._run_command(["slow"], Path("/tmp"), timeout=1)

        assert success is False
        mock_killpg.assert_not_called()

    @pytest.mark.asyncio
    async def test_kill_process_tree_guards_mock_pid(self) -> None:
        """Regression: a mocked ``proc.pid`` (not a real int) must NOT reach
        ``os.getpgid``/``os.killpg``.

        Without the guard, a ``MagicMock`` ``proc.pid`` coerces via ``__index__``
        to 1, so ``os.killpg(os.getpgid(1), ...)`` becomes ``kill(-1, ...)`` — a
        broadcast that SIGKILLs the whole process tree (the CI-runner self-kill
        class fixed in coding-security #80). ``returncode`` is None so we get past
        the early-out and actually exercise the pid guard.
        """
        mock_proc = MagicMock()
        mock_proc.returncode = None  # still running: passes the early-out
        mock_proc.wait = AsyncMock()
        # mock_proc.pid is left as the default mock attribute (would coerce to 1).

        with patch("os.getpgid") as mock_getpgid, patch("os.killpg") as mock_killpg:
            await ValidationService._kill_process_tree(mock_proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()


# ── check_typescript_file ──────────────────────────────────────────────


class TestCheckTypescriptFile:
    @pytest.mark.asyncio
    async def test_compiles_successfully(self, validator: ValidationService) -> None:
        with patch.object(validator, "_run_command", new=AsyncMock(return_value=(True, ""))):
            success, _output = await validator.check_typescript_file(Path("/scripts"), "ch01-part1")
            assert success is True

    @pytest.mark.asyncio
    async def test_errors_filtered_to_target_file(self, validator: ValidationService) -> None:
        error_output = (
            "parts/ch01-part1.ts(5,3): error TS2304: Cannot find name 'foo'\n"
            "helpers/util.ts(10,1): error TS2304: Cannot find name 'bar'"
        )
        with patch.object(
            validator, "_run_command", new=AsyncMock(return_value=(False, error_output))
        ):
            success, output = await validator.check_typescript_file(Path("/scripts"), "ch01-part1")
            assert success is False
            assert "ch01-part1" in output
            assert "util.ts" not in output

    @pytest.mark.asyncio
    async def test_no_errors_for_target_returns_true(self, validator: ValidationService) -> None:
        """If the target file has no errors, treat as success even if other files fail."""
        error_output = "helpers/util.ts(10,1): error TS2304: Cannot find name 'bar'"
        with patch.object(
            validator, "_run_command", new=AsyncMock(return_value=(False, error_output))
        ):
            success, output = await validator.check_typescript_file(Path("/scripts"), "ch01-part1")
            assert success is True
            assert output == ""


# ── validate ───────────────────────────────────────────────────────────


class TestValidate:
    @pytest.mark.asyncio
    async def test_all_checks_pass(self, validator: ValidationService) -> None:
        with (
            patch.object(
                validator, "check_typescript_file", new=AsyncMock(return_value=(True, ""))
            ),
            patch.object(validator, "check_discoverable", new=AsyncMock(return_value=(True, ""))),
            patch.object(validator, "dry_run", new=AsyncMock(return_value=(True, ""))),
        ):
            result = await validator.validate(Path("/scripts"), "part1")
            assert result.compiles is True
            assert result.discoverable is True
            assert result.runs_headless is True
            assert result.accepted is True
            assert result.errors is None

    @pytest.mark.asyncio
    async def test_compile_failure(self, validator: ValidationService) -> None:
        with (
            patch.object(
                validator, "check_typescript_file", new=AsyncMock(return_value=(False, "TS2304"))
            ),
            patch.object(validator, "check_discoverable", new=AsyncMock(return_value=(True, ""))),
            patch.object(validator, "dry_run", new=AsyncMock(return_value=(True, ""))),
        ):
            result = await validator.validate(Path("/scripts"), "part1")
            assert result.compiles is False
            assert result.accepted is False
            assert result.errors is not None
            assert any("TypeScript" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_not_discoverable_skips_dry_run(self, validator: ValidationService) -> None:
        mock_dry_run = AsyncMock()
        with (
            patch.object(
                validator, "check_typescript_file", new=AsyncMock(return_value=(True, ""))
            ),
            patch.object(
                validator,
                "check_discoverable",
                new=AsyncMock(return_value=(False, "no tests found")),
            ),
            patch.object(validator, "dry_run", new=mock_dry_run),
        ):
            result = await validator.validate(Path("/scripts"), "part1")
            assert result.discoverable is False
            assert result.runs_headless is False
            assert result.accepted is False
            mock_dry_run.assert_not_called()

    @pytest.mark.asyncio
    async def test_safety_scan_rejects_without_executing(
        self, validator: ValidationService
    ) -> None:
        """A dangerous script is rejected and NO subprocess check runs."""
        ts_check = AsyncMock(return_value=(True, ""))
        discover = AsyncMock(return_value=(True, ""))
        dry = AsyncMock(return_value=(True, ""))
        with (
            patch.object(
                validator,
                "_read_part_source",
                return_value="const cp = require('child_process'); cp.execSync('id');",
            ),
            patch.object(validator, "check_typescript_file", new=ts_check),
            patch.object(validator, "check_discoverable", new=discover),
            patch.object(validator, "dry_run", new=dry),
        ):
            result = await validator.validate(Path("/scripts"), "part1")

        assert result.accepted is False
        assert result.errors is not None
        assert any("safety scan" in e for e in result.errors)
        # Fail-closed: nothing imported or executed the script.
        ts_check.assert_not_called()
        discover.assert_not_called()
        dry.assert_not_called()

    @pytest.mark.asyncio
    async def test_safe_script_passes_scan_and_runs_checks(
        self, validator: ValidationService
    ) -> None:
        with (
            patch.object(
                validator,
                "_read_part_source",
                return_value="await page.goto('https://example.com');",
            ),
            patch.object(
                validator, "check_typescript_file", new=AsyncMock(return_value=(True, ""))
            ),
            patch.object(validator, "check_discoverable", new=AsyncMock(return_value=(True, ""))),
            patch.object(validator, "dry_run", new=AsyncMock(return_value=(True, ""))),
        ):
            result = await validator.validate(Path("/scripts"), "part1")
        assert result.accepted is True

    @pytest.mark.asyncio
    async def test_dry_run_failure(self, validator: ValidationService) -> None:
        with (
            patch.object(
                validator, "check_typescript_file", new=AsyncMock(return_value=(True, ""))
            ),
            patch.object(validator, "check_discoverable", new=AsyncMock(return_value=(True, ""))),
            patch.object(
                validator, "dry_run", new=AsyncMock(return_value=(False, "Test timed out"))
            ),
        ):
            result = await validator.validate(Path("/scripts"), "part1")
            assert result.runs_headless is False
            assert result.accepted is False
            assert result.errors is not None
            assert any("Dry run failed" in e for e in result.errors)
