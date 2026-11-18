"""Tests for template loading infrastructure and template file integrity."""

import pytest

from src.templates import get_templates_dir

# All 31 expected template files organized by category
BASE_TEMPLATES = [
    "base_prompt.md",
    "base_commercial.md",
    "base_company_commercial.md",
    "base_webapp_commercial.md",
    "base_tutorial.md",
    "base_findings.md",
]

PAGE_TYPE_TEMPLATES = [
    "homepage.md",
    "landing.md",
    "product.md",
    "product_listing.md",
    "blog_post.md",
    "blog_index.md",
    "pricing.md",
    "form.md",
    "dashboard.md",
    "documentation.md",
    "about.md",
    "contact.md",
    "faq.md",
    "login.md",
    "gallery.md",
    "generic.md",
]

FINDINGS_TEMPLATES = [
    "critique_finding.md",
    "review_finding.md",
]

MOBILE_TEMPLATES = [
    "mobile/base_mobile.md",
    "mobile/home.md",
    "mobile/login.md",
    "mobile/list.md",
    "mobile/detail.md",
    "mobile/form.md",
    "mobile/settings.md",
]

ALL_TEMPLATES = BASE_TEMPLATES + PAGE_TYPE_TEMPLATES + FINDINGS_TEMPLATES + MOBILE_TEMPLATES


class TestGetTemplatesDir:
    """Tests for the get_templates_dir() helper function."""

    def test_returns_valid_directory(self) -> None:
        templates_dir = get_templates_dir()
        assert templates_dir.exists(), f"Templates directory does not exist: {templates_dir}"
        assert templates_dir.is_dir(), f"Templates path is not a directory: {templates_dir}"

    def test_scripts_subdirectory_exists(self) -> None:
        scripts_dir = get_templates_dir() / "scripts"
        assert scripts_dir.exists(), f"Scripts directory does not exist: {scripts_dir}"
        assert scripts_dir.is_dir(), f"Scripts path is not a directory: {scripts_dir}"

    def test_mobile_subdirectory_exists(self) -> None:
        mobile_dir = get_templates_dir() / "scripts" / "mobile"
        assert mobile_dir.exists(), f"Mobile directory does not exist: {mobile_dir}"
        assert mobile_dir.is_dir(), f"Mobile path is not a directory: {mobile_dir}"


class TestTemplateFilesExist:
    """Tests that all 31 expected template files exist."""

    def test_total_template_count(self) -> None:
        assert len(ALL_TEMPLATES) == 31, f"Expected 31 templates, got {len(ALL_TEMPLATES)}"

    @pytest.mark.parametrize("template_file", ALL_TEMPLATES)
    def test_template_file_exists(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        assert template_path.exists(), f"Template file missing: {template_path}"

    @pytest.mark.parametrize("template_file", BASE_TEMPLATES)
    def test_base_template_exists(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        assert template_path.exists(), f"Base template missing: {template_path}"

    @pytest.mark.parametrize("template_file", PAGE_TYPE_TEMPLATES)
    def test_page_type_template_exists(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        assert template_path.exists(), f"Page-type template missing: {template_path}"

    @pytest.mark.parametrize("template_file", MOBILE_TEMPLATES)
    def test_mobile_template_exists(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        assert template_path.exists(), f"Mobile template missing: {template_path}"


class TestTemplateFilesNonEmpty:
    """Tests that all template files contain content."""

    @pytest.mark.parametrize("template_file", ALL_TEMPLATES)
    def test_template_file_is_non_empty(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        content = template_path.read_text(encoding="utf-8")
        assert len(content.strip()) > 0, f"Template file is empty: {template_path}"

    @pytest.mark.parametrize("template_file", ALL_TEMPLATES)
    def test_template_file_is_markdown(self, template_file: str) -> None:
        template_path = get_templates_dir() / "scripts" / template_file
        content = template_path.read_text(encoding="utf-8")
        assert content.startswith("#") or content.startswith(">") or content.startswith("<!--"), (
            f"Template file does not start with markdown heading, blockquote, or comment: {template_path}"
        )


class TestTemplateLoading:
    """Tests that templates can be loaded and read correctly."""

    def test_load_base_template(self) -> None:
        template_path = get_templates_dir() / "scripts" / "base_prompt.md"
        content = template_path.read_text(encoding="utf-8")
        assert "Playwright" in content, "base_prompt.md should reference Playwright"
        assert "demo" in content.lower(), "base_prompt.md should reference demos"

    def test_load_page_type_template(self) -> None:
        template_path = get_templates_dir() / "scripts" / "homepage.md"
        content = template_path.read_text(encoding="utf-8")
        assert "Homepage" in content or "homepage" in content, (
            "homepage.md should reference homepage"
        )

    def test_load_mobile_template(self) -> None:
        template_path = get_templates_dir() / "scripts" / "mobile" / "base_mobile.md"
        content = template_path.read_text(encoding="utf-8")
        assert "Mobile" in content or "mobile" in content, "base_mobile.md should reference mobile"

    def test_load_findings_template(self) -> None:
        template_path = get_templates_dir() / "scripts" / "critique_finding.md"
        content = template_path.read_text(encoding="utf-8")
        assert "finding" in content.lower(), "critique_finding.md should reference findings"

    def test_all_templates_are_utf8_readable(self) -> None:
        scripts_dir = get_templates_dir() / "scripts"
        for template_file in ALL_TEMPLATES:
            template_path = scripts_dir / template_file
            try:
                content = template_path.read_text(encoding="utf-8")
                assert isinstance(content, str)
            except UnicodeDecodeError:
                pytest.fail(f"Template file is not valid UTF-8: {template_path}")
