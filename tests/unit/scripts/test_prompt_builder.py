"""Unit tests for PromptBuilder."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.services.scripts.prompt_builder import COMPLETION_TOKEN, PromptBuilder, _sanitize_md

# ============================================================================
# Sanitization Tests
# ============================================================================


class TestSanitizeMd:
    def test_escapes_horizontal_rules(self) -> None:
        assert "\\-\\-\\-" in _sanitize_md("some --- text")

    def test_escapes_bold_markers(self) -> None:
        assert "\\*\\*" in _sanitize_md("**bold**")

    def test_escapes_underline_markers(self) -> None:
        assert "\\_\\_" in _sanitize_md("__underline__")

    def test_escapes_triple_backticks(self) -> None:
        assert "\\`\\`\\`" in _sanitize_md("```code```")

    def test_escapes_heading_at_start(self) -> None:
        result = _sanitize_md("# Heading")
        assert result.startswith("\\#")

    def test_escapes_heading_after_newline(self) -> None:
        result = _sanitize_md("line\n# Heading")
        assert "\\#" in result

    def test_leaves_normal_text_unchanged(self) -> None:
        assert _sanitize_md("hello world") == "hello world"

    def test_handles_non_string_values(self) -> None:
        assert _sanitize_md(42) == "42"
        assert _sanitize_md(None) == "None"
        assert _sanitize_md(True) == "True"


# ============================================================================
# Template Loading Tests
# ============================================================================


class TestTemplateLoading:
    def test_load_existing_template(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        (scripts_dir / "test.md").write_text("template content")
        builder = PromptBuilder(templates_dir=scripts_dir)
        result = builder._load_template("test.md")
        assert result == "template content"

    def test_load_missing_template_returns_none(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path / "scripts"
        scripts_dir.mkdir()
        builder = PromptBuilder(templates_dir=scripts_dir)
        result = builder._load_template("nonexistent.md")
        assert result is None


# ============================================================================
# Base Instructions Tests
# ============================================================================


class TestBaseInstructions:
    def test_loads_mode_specific_template(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path
        (scripts_dir / "base_webapp_commercial.md").write_text("Mode-specific template")
        builder = PromptBuilder(templates_dir=scripts_dir, mode="webapp_commercial")
        result = builder._base_instructions()
        assert result == "Mode-specific template"

    def test_falls_back_to_base_prompt(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path
        (scripts_dir / "base_prompt.md").write_text("Fallback template")
        builder = PromptBuilder(templates_dir=scripts_dir, mode="nonexistent_mode")
        result = builder._base_instructions()
        assert result == "Fallback template"

    def test_hardcoded_fallback_when_no_templates(self, tmp_path: Path) -> None:
        scripts_dir = tmp_path
        builder = PromptBuilder(templates_dir=scripts_dir, mode="nonexistent_mode")
        result = builder._base_instructions()
        assert "Playwright Demo Script Generation" in result
        assert COMPLETION_TOKEN in result


# ============================================================================
# Part Details Tests
# ============================================================================


class TestPartDetails:
    def test_basic_part(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {
            "id": "ch01-part1",
            "title": "Homepage Tour",
            "chapter": 1,
            "pages": ["https://example.com"],
            "actions": ["Navigate to homepage", "Highlight hero section"],
            "key_elements": [".hero", ".nav"],
            "estimated_duration_seconds": 30,
        }
        result = builder._part_details(part)
        assert "ch01-part1" in result
        assert "Homepage Tour" in result
        assert "30s" in result
        assert "Navigate to homepage" in result
        assert "Highlight hero section" in result
        assert "`.hero`" in result
        assert "`.nav`" in result

    def test_brief_budget_hint(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "estimated_duration_seconds": 20, "actions": []}
        result = builder._part_details(part)
        assert "Brief" in result

    def test_short_budget_hint(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "estimated_duration_seconds": 35, "actions": []}
        result = builder._part_details(part)
        assert "Short" in result

    def test_medium_budget_hint(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "estimated_duration_seconds": 50, "actions": []}
        result = builder._part_details(part)
        assert "Medium" in result

    def test_full_budget_hint(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "estimated_duration_seconds": 90, "actions": []}
        result = builder._part_details(part)
        assert "Full" in result

    def test_actions_sanitized(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {
            "id": "t",
            "actions": ["## Bold heading **text**"],
            "estimated_duration_seconds": 30,
        }
        result = builder._part_details(part)
        assert "\\*\\*" in result

    def test_missing_optional_fields(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "minimal"}
        result = builder._part_details(part)
        assert "minimal" in result
        assert "N/A" in result  # chapter and pages fall back to N/A


# ============================================================================
# Page Analysis Tests
# ============================================================================


class TestPageAnalysis:
    def test_no_refs(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t"}
        result = builder._page_analysis(part, {})
        assert "No page analysis available" in result

    def test_with_refs_and_data(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["page-001"]}
        analysis = {"page-001": {"url": "https://example.com", "title": "Home"}}
        result = builder._page_analysis(part, analysis)
        assert "page-001" in result
        assert "https://example.com" in result
        assert "Home" in result

    def test_sanitizes_values(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["p1"]}
        analysis = {"p1": {"desc": "**bold** text"}}
        result = builder._page_analysis(part, analysis)
        assert "\\*\\*" in result

    def test_missing_ref_skipped(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["missing-page"]}
        result = builder._page_analysis(part, {})
        assert "# Page Analysis" in result


# ============================================================================
# Page Type Template Tests
# ============================================================================


class TestPageTypeTemplate:
    def test_loads_specific_page_type(self, tmp_path: Path) -> None:
        (tmp_path / "homepage.md").write_text("Homepage instructions")
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["p1"]}
        analysis = {"p1": {"page_type": "homepage"}}
        result = builder._page_type_template(part, analysis)
        assert result == "Homepage instructions"

    def test_falls_back_to_generic(self, tmp_path: Path) -> None:
        (tmp_path / "generic.md").write_text("Generic instructions")
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["p1"]}
        analysis = {"p1": {"page_type": "some_unusual_type"}}
        result = builder._page_type_template(part, analysis)
        assert result == "Generic instructions"

    def test_empty_when_no_templates(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t", "pages": ["p1"]}
        analysis = {"p1": {"page_type": "dashboard"}}
        result = builder._page_type_template(part, analysis)
        assert result == ""

    def test_defaults_to_generic_without_refs(self, tmp_path: Path) -> None:
        (tmp_path / "generic.md").write_text("Generic fallback")
        builder = PromptBuilder(templates_dir=tmp_path)
        part = {"id": "t"}
        result = builder._page_type_template(part, {})
        assert result == "Generic fallback"


# ============================================================================
# Helpers API Tests
# ============================================================================


class TestDemoHelpersApi:
    def test_contains_core_functions(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._demo_helpers_api()
        assert "scrollToElement" in result
        assert "safeHighlight" in result
        assert "spotlightElement" in result
        assert "annotateAndWait" in result
        assert "smoothScroll" in result
        assert "safeClick" in result
        assert "safeType" in result
        assert "pause" in result

    def test_contains_critical_rule(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._demo_helpers_api()
        assert "CRITICAL RULE" in result
        assert "ALWAYS" in result


# ============================================================================
# Storyline / Auth / User Context Tests
# ============================================================================


class TestContextSections:
    def test_storyline_context_empty(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        assert builder._storyline_context("") == ""

    def test_storyline_context_populated(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._storyline_context("Opening chapter")
        assert "Storyline Context" in result
        assert "Opening chapter" in result

    def test_auth_context(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._auth_context()
        assert "Authentication Context" in result
        assert "already logged in" in result
        assert "Do NOT include any login steps" in result

    def test_user_context_empty_returns_empty(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        assert builder._user_context_section({}) == ""

    def test_user_context_with_audience(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        ctx = {"target_audience": "Enterprise SaaS buyers"}
        result = builder._user_context_section(ctx)
        assert "Target Audience" in result
        assert "Enterprise SaaS buyers" in result

    def test_user_context_with_goals(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        ctx = {"goals": ["Show dashboard", "Highlight analytics"]}
        result = builder._user_context_section(ctx)
        assert "Goals" in result
        assert "Show dashboard" in result
        assert "Highlight analytics" in result

    def test_user_context_with_key_messages(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        ctx = {"narration_guidance": {"key_messages": ["Speed is critical", "Easy onboarding"]}}
        result = builder._user_context_section(ctx)
        assert "Key Things to Highlight" in result
        assert "Speed is critical" in result

    def test_user_context_sanitizes_values(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        ctx = {"target_audience": "**Bold** audience"}
        result = builder._user_context_section(ctx)
        assert "\\*\\*" in result


# ============================================================================
# Completion Instructions Tests
# ============================================================================


class TestCompletionInstructions:
    def test_contains_completion_token(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._completion_instructions()
        assert COMPLETION_TOKEN in result
        assert "typescript" in result

    def test_custom_completion_token(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path, completion_token="<DONE>")
        result = builder._completion_instructions()
        assert "<DONE>" in result


# ============================================================================
# Full Build Assembly Tests
# ============================================================================


class TestBuild:
    @pytest.fixture()
    def templates_dir(self, tmp_path: Path) -> Path:
        d = tmp_path
        (d / "base_webapp_commercial.md").write_text("# Base Instructions\nGenerate a script.")
        (d / "generic.md").write_text("# Generic Page\nHandle any page type.")
        return d

    def test_full_build_returns_joined_sections(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, mode="webapp_commercial")
        part = {
            "id": "ch01-part1",
            "title": "Homepage",
            "actions": ["Navigate to homepage"],
            "pages": ["p1"],
            "estimated_duration_seconds": 30,
        }
        analysis = {"p1": {"page_type": "generic", "url": "https://example.com"}}
        result = builder.build(part, page_analysis=analysis, storyline_context="Intro")
        assert "# Base Instructions" in result
        assert "ch01-part1" in result
        assert "Page Analysis" in result
        assert "Demo Helpers Cheat Sheet" in result
        assert "Storyline Context" in result
        assert "Completion" in result
        # Sections joined by horizontal rules
        assert "\n\n---\n\n" in result

    def test_build_includes_auth_when_enabled(self, templates_dir: Path) -> None:
        builder = PromptBuilder(
            templates_dir=templates_dir,
            mode="webapp_commercial",
            auth_enabled=True,
        )
        part = {"id": "t", "actions": []}
        result = builder.build(part)
        assert "Authentication Context" in result

    def test_build_excludes_auth_when_disabled(self, templates_dir: Path) -> None:
        builder = PromptBuilder(
            templates_dir=templates_dir,
            mode="webapp_commercial",
            auth_enabled=False,
        )
        part = {"id": "t", "actions": []}
        result = builder.build(part)
        assert "Authentication Context" not in result

    def test_build_includes_user_direction(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, mode="webapp_commercial")
        part = {"id": "t", "actions": []}
        result = builder.build(part, message="Focus on pricing page")
        assert "User Direction" in result
        assert "Focus on pricing page" in result

    def test_build_includes_user_context(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, mode="webapp_commercial")
        part = {"id": "t", "actions": []}
        user_ctx = {"target_audience": "Developers", "goals": ["Show API docs"]}
        result = builder.build(part, user_context=user_ctx)
        assert "User Context" in result
        assert "Developers" in result

    def test_build_empty_user_context_excluded(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, mode="webapp_commercial")
        part = {"id": "t", "actions": []}
        result = builder.build(part, user_context={})
        assert "User Context" not in result


class TestPromptBuilderOverrides:
    def test_with_overrides_creates_new_instance(self, tmp_path: Path) -> None:
        builder = PromptBuilder(
            templates_dir=tmp_path,
            mode="webapp_commercial",
            source_type="web",
            auth_enabled=False,
        )
        overridden = builder.with_overrides(mode="tutorial", source_type="ios", auth_enabled=True)
        assert overridden is not builder
        assert overridden.mode == "tutorial"
        assert overridden.source_type == "ios"
        assert overridden.auth_enabled is True

    def test_with_overrides_returns_same_instance_when_unchanged(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        overridden = builder.with_overrides()
        assert overridden is builder


# ============================================================================
# Mobile Build Tests
# ============================================================================


class TestMobileBuild:
    @pytest.fixture()
    def templates_dir(self, tmp_path: Path) -> Path:
        d = tmp_path
        mobile_dir = d / "mobile"
        mobile_dir.mkdir()
        (mobile_dir / "base_mobile.md").write_text(
            "# Mobile {{ platform_display }} Script\nAutomation: {{ automation_name }}"
        )
        (mobile_dir / "home.md").write_text("# Home Screen Template")
        return d

    def test_mobile_build_ios(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, source_type="ios")
        part = {"id": "mobile-1", "actions": ["Tap login"], "estimated_duration_seconds": 20}
        result = builder.build(part, storyline_context="App intro")
        assert "iOS" in result
        assert "XCUITest" in result
        assert "mobile-1" in result
        assert "Mobile Demo Helpers" in result
        assert "Completion" in result

    def test_mobile_build_android(self, templates_dir: Path) -> None:
        builder = PromptBuilder(templates_dir=templates_dir, source_type="android")
        part = {"id": "mobile-2", "actions": ["Swipe up"]}
        result = builder.build(part)
        assert "Android" in result
        assert "UiAutomator2" in result

    def test_mobile_fallback_when_no_template(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path, source_type="ios")
        part = {"id": "m1", "actions": []}
        result = builder.build(part)
        assert "Mobile App Demo Script Generation" in result
        assert "WebdriverIO" in result


class TestScreenTypeTemplate:
    def test_loads_specific_screen_type(self, tmp_path: Path) -> None:
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        (mobile_dir / "login.md").write_text("Login screen template")
        builder = PromptBuilder(templates_dir=tmp_path, source_type="ios")
        part = {"id": "t", "pages": ["s1"]}
        analysis = {"s1": {"screen_type": "login"}}
        result = builder._screen_type_template(part, analysis)
        assert result == "Login screen template"

    def test_falls_back_to_home(self, tmp_path: Path) -> None:
        mobile_dir = tmp_path / "mobile"
        mobile_dir.mkdir()
        (mobile_dir / "home.md").write_text("Home fallback")
        builder = PromptBuilder(templates_dir=tmp_path, source_type="android")
        part = {"id": "t", "pages": ["s1"]}
        analysis = {"s1": {"screen_type": "unknown"}}
        result = builder._screen_type_template(part, analysis)
        assert result == "Home fallback"

    def test_returns_empty_when_no_templates(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path, source_type="ios")
        part = {"id": "t"}
        result = builder._screen_type_template(part, {})
        assert result == ""


class TestMobileHelpersApi:
    def test_contains_mobile_functions(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._mobile_helpers_api()
        assert "tapElement" in result
        assert "typeText" in result
        assert "swipeScreen" in result
        assert "scrollToElement" in result
        assert "waitForScreen" in result
        assert "assertVisible" in result
        assert "goBack" in result
        assert "pause" in result

    def test_contains_critical_rules(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        result = builder._mobile_helpers_api()
        assert "CRITICAL RULES" in result
        assert "accessibility IDs" in result


# ============================================================================
# Constructor / Defaults Tests
# ============================================================================


class TestConstructorDefaults:
    def test_default_templates_dir(self) -> None:
        builder = PromptBuilder()
        expected = Path(__file__).parent.parent.parent.parent / "src" / "templates" / "scripts"
        assert builder.templates_dir == expected

    def test_custom_templates_dir(self, tmp_path: Path) -> None:
        builder = PromptBuilder(templates_dir=tmp_path)
        assert builder.templates_dir == tmp_path

    def test_default_mode(self) -> None:
        builder = PromptBuilder()
        assert builder.mode == "webapp_commercial"

    def test_default_source_type(self) -> None:
        builder = PromptBuilder()
        assert builder.source_type == "web"

    def test_default_auth_disabled(self) -> None:
        builder = PromptBuilder()
        assert builder.auth_enabled is False

    def test_default_completion_token(self) -> None:
        builder = PromptBuilder()
        assert builder.completion_token == COMPLETION_TOKEN
