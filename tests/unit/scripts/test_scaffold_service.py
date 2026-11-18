"""Tests for services.scripts.scaffold_service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.scripts.scaffold_service import (
    THEME_COLOR_FIELDS,
    ScaffoldService,
    _inject_theme,
    _ts_escape,
    _validate_color,
)


class TestValidateColor:
    def test_valid_hex_3(self):
        _validate_color("#abc", "test")

    def test_valid_hex_6(self):
        _validate_color("#abcdef", "test")

    def test_valid_hex_8(self):
        _validate_color("#abcdef12", "test")

    def test_invalid_color(self):
        with pytest.raises(ValueError, match="invalid color"):
            _validate_color("red", "test")

    def test_non_string_raises(self):
        with pytest.raises(ValueError, match="must be a string"):
            _validate_color(123, "test")


class TestTsEscape:
    def test_escapes_backslash(self):
        assert _ts_escape("a\\b") == "a\\\\b"

    def test_escapes_single_quote(self):
        assert _ts_escape("a'b") == "a\\'b"

    def test_escapes_backtick(self):
        assert _ts_escape("a`b") == "a\\`b"

    def test_escapes_newline(self):
        assert _ts_escape("a\nb") == "a\\nb"

    def test_escapes_carriage_return(self):
        assert _ts_escape("a\rb") == "a\\rb"


class TestInjectTheme:
    def test_inject_theme_success(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helpers_dir.mkdir()
        demo_helpers = helpers_dir / "demo-helpers.ts"
        demo_helpers.write_text(
            "const DEFAULT_THEME: DemoTheme = {\n"
            "  primaryColor: '#000000',\n"
            "  backgroundColor: '#ffffff',\n"
            "}"
        )

        theme = {
            "primaryColor": "#111111",
            "backgroundColor": "#222222",
            "textColor": "#333333",
            "accentColor": "#444444",
            "subtitleColor": "#555555",
            "gradientStart": "#666666",
            "gradientEnd": "#777777",
        }
        _inject_theme(demo_helpers, theme)

        result = demo_helpers.read_text()
        assert "#111111" in result

    def test_inject_theme_normalizes_surrounding_whitespace(self, tmp_path):
        """A color that only validates after .strip() (trailing newline / leading
        spaces from an LLM or user) must be injected in its NORMALIZED form.
        _validate_color validates value.strip(), so raw injection of the un-stripped
        value smuggles a literal '\\n' into the TS color string (via _ts_escape) and
        corrupts the runtime CSS color."""
        helpers_dir = tmp_path / "helpers"
        helpers_dir.mkdir()
        demo_helpers = helpers_dir / "demo-helpers.ts"
        demo_helpers.write_text("const DEFAULT_THEME: DemoTheme = {\n  primaryColor: '#000',\n}")

        theme = {f: "#111111" for f in THEME_COLOR_FIELDS}
        theme["primaryColor"] = "  #ffffff\n"
        _inject_theme(demo_helpers, theme)

        result = demo_helpers.read_text()
        assert "'#ffffff'" in result  # normalized, injected clean
        assert "#ffffff\\n" not in result  # no smuggled newline escape
        assert "  #ffffff" not in result  # no leading whitespace

    def test_inject_theme_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            _inject_theme(tmp_path / "nonexistent.ts", {})

    def test_inject_theme_missing_field(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helpers_dir.mkdir()
        demo_helpers = helpers_dir / "demo-helpers.ts"
        demo_helpers.write_text("const DEFAULT_THEME: DemoTheme = {}")

        with pytest.raises(KeyError, match="Required theme field"):
            _inject_theme(demo_helpers, {"primaryColor": "#111111"})

    def test_inject_theme_no_match(self, tmp_path):
        helpers_dir = tmp_path / "helpers"
        helpers_dir.mkdir()
        demo_helpers = helpers_dir / "demo-helpers.ts"
        demo_helpers.write_text("// no theme here")

        theme = {f: "#000000" for f in THEME_COLOR_FIELDS}
        with pytest.raises(ValueError, match="Could not find DEFAULT_THEME"):
            _inject_theme(demo_helpers, theme)


class TestScaffoldService:
    @pytest.fixture
    def scaffold_src(self, tmp_path):
        """Create a minimal scaffold source directory."""
        scaffold_dir = tmp_path / "scaffold_src"
        scaffold_dir.mkdir()
        (scaffold_dir / "playwright.recording.config.ts").write_text("config")
        (scaffold_dir / "package.json").write_text('{"name": "test"}')
        helpers_dir = scaffold_dir / "helpers"
        helpers_dir.mkdir()
        (helpers_dir / "demo-helpers.ts").write_text(
            "const DEFAULT_THEME: DemoTheme = {\n  primaryColor: '#000',\n}"
        )
        return scaffold_dir

    async def test_scaffold_copies_files(self, tmp_path, scaffold_src):
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        with patch(
            "src.services.scripts.scaffold_service.get_scaffold_dir", return_value=scaffold_src
        ):
            svc = ScaffoldService(npm_install_enabled=False)
            scripts_dir = await svc.scaffold(job_dir)

            assert (scripts_dir / "playwright.recording.config.ts").exists()
            assert (scripts_dir / "parts").is_dir()

    async def test_scaffold_with_theme(self, tmp_path, scaffold_src):
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        theme = {f: "#aabbcc" for f in THEME_COLOR_FIELDS}

        with patch(
            "src.services.scripts.scaffold_service.get_scaffold_dir", return_value=scaffold_src
        ):
            svc = ScaffoldService(npm_install_enabled=False)
            scripts_dir = await svc.scaffold(job_dir, theme=theme)

            content = (scripts_dir / "helpers" / "demo-helpers.ts").read_text()
            assert "#aabbcc" in content

    async def test_scaffold_mobile(self, tmp_path):
        job_dir = tmp_path / "job"
        job_dir.mkdir()

        mobile_src = tmp_path / "mobile_src"
        mobile_src.mkdir()
        (mobile_src / "package.json").write_text('{"name": "mobile-test"}')

        with patch(
            "src.services.scripts.scaffold_service.get_mobile_scaffold_dir",
            return_value=mobile_src,
        ):
            svc = ScaffoldService(npm_install_enabled=False)
            scripts_dir = await svc.scaffold_mobile(job_dir)
            assert scripts_dir.exists()
            assert (scripts_dir / "parts").is_dir()

    async def test_npm_install_success(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        svc = ScaffoldService(npm_install_enabled=True, npm_install_timeout=10)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            await svc._npm_install(scripts_dir)

    async def test_npm_install_failure(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        svc = ScaffoldService()

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"Error: fail"))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="npm install failed"):
                await svc._npm_install(scripts_dir)

    async def test_npm_install_timeout(self, tmp_path):
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()

        svc = ScaffoldService(npm_install_timeout=1)

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(side_effect=TimeoutError)
        mock_proc.kill = MagicMock()

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(RuntimeError, match="timed out"):
                await svc._npm_install(scripts_dir)

    async def test_kill_process_tree_guards_mock_pid(self):
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
            await ScaffoldService._kill_process_tree(mock_proc)

        mock_getpgid.assert_not_called()
        mock_killpg.assert_not_called()
