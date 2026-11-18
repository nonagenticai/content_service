"""Unit tests for storyline template loading and rendering."""

from __future__ import annotations

from pathlib import Path

from src.api.narrative_schemas import AnalysisInput, PageInput, SiteInput
from src.config import Settings
from src.services.narrative.storyline_generator import _TEMPLATES_DIR, StorylineGenerator


class TestTemplateLoading:
    """Tests for StorylineGenerator._load_template."""

    def test_loads_storyline_template_for_webapp_commercial(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        result = gen._load_template("webapp_commercial", 3.0)
        assert result is not None
        assert "Storyline Development" in result
        # webapp_commercial uses the main storyline.md with mode conditionals
        assert "Context" in result  # Part of the narrative arc
        assert "3.0" in result  # Duration is rendered

    def test_loads_storyline_template_for_tutorial(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        result = gen._load_template("tutorial", 2.0)
        assert result is not None
        assert "Tutorial" in result
        assert "Discover" in result  # Narrative arc
        assert "2.0" in result  # Duration is rendered

    def test_loads_storyline_template_for_company_commercial(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        result = gen._load_template("company_commercial", 2.5)
        assert result is not None
        assert "Company Showcase" in result
        assert "Identity" in result  # Narrative arc
        assert "2.5" in result  # Duration is rendered

    def test_loads_critique_video_template(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Example Site"),
        )
        reviews = {
            "seo": {"score": 65, "findings": [{"title": "Missing meta"}]},
            "accessibility": {"score": 80, "findings": []},
        }
        result = gen._load_template("critique_video", 2.0, analysis=analysis, reviews=reviews)
        assert result is not None
        assert "Critique Video" in result
        assert "Example Site" in result
        assert "https://example.com/" in result
        assert "Seo" in result
        assert "65/100" in result
        assert "2.0" in result  # Duration is rendered

    def test_loads_review_video_template(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Example Site"),
        )
        reviews = {"design": {"strengths": ["good layout"]}}
        result = gen._load_template("review_video", 2.0, analysis=analysis, reviews=reviews)
        assert result is not None
        assert "Review Video" in result
        assert "Example Site" in result
        assert "Design" in result

    def test_unknown_mode_falls_back_to_storyline(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        result = gen._load_template("unknown_mode_xyz", 3.0)
        assert result is not None
        # Falls back to storyline.md, which renders with else branch
        assert "Storyline Development" in result

    def test_returns_none_when_no_templates_exist(
        self, mock_settings: Settings, tmp_path: Path
    ) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        # Patch the function's actual globals dict (may differ from the import
        # when integration tests cause duplicate module loading).
        func_globals = gen._load_template.__func__.__globals__
        original = func_globals["_TEMPLATES_DIR"]
        func_globals["_TEMPLATES_DIR"] = tmp_path / "nonexistent"
        try:
            result = gen._load_template("webapp_commercial", 3.0)
            assert result is None
        finally:
            func_globals["_TEMPLATES_DIR"] = original

    def test_template_renders_duration(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        result = gen._load_template("webapp_commercial", 4.5)
        assert result is not None
        assert "4.5" in result
        assert "Storyline Development" in result


class TestBuildPromptWithTemplates:
    """Tests for _build_prompt using template-driven generation."""

    def test_build_prompt_uses_template(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/", title="Home", page_type="homepage", page_id="p1"
                ),
            ],
            site=SiteInput(description="A test site", tone="professional"),
        )
        prompt = gen._build_prompt(analysis, None, 3.0, "", "webapp_commercial")
        # Template content should be present
        assert "Storyline Development" in prompt
        # Context sections should be appended
        assert "## Site Description" in prompt
        assert "A test site" in prompt
        assert "## Pages Available" in prompt

    def test_build_prompt_falls_back_to_default(
        self, mock_settings: Settings, tmp_path: Path
    ) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/", title="Home", page_type="homepage", page_id="p1"
                ),
            ],
            site=SiteInput(description="Test"),
        )
        func_globals = gen._load_template.__func__.__globals__
        original = func_globals["_TEMPLATES_DIR"]
        func_globals["_TEMPLATES_DIR"] = tmp_path / "nonexistent"
        try:
            prompt = gen._build_prompt(analysis, None, 3.0, "", "webapp_commercial")
        finally:
            func_globals["_TEMPLATES_DIR"] = original
        # Should use the inline default prompt
        assert "video storyline" in prompt.lower()
        assert "JSON object" in prompt

    def test_build_prompt_includes_reviews(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Test"),
        )
        reviews = {"design": {"strengths": ["good layout"]}}
        prompt = gen._build_prompt(analysis, reviews, 3.0, "", "webapp_commercial")
        assert "Website Reviews" in prompt

    def test_build_prompt_includes_message(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[PageInput(url="https://example.com/", title="Home", page_id="p1")],
            site=SiteInput(description="Test"),
        )
        prompt = gen._build_prompt(analysis, None, 3.0, "Focus on pricing", "webapp_commercial")
        assert "Focus on pricing" in prompt

    def test_build_prompt_spa_note(self, mock_settings: Settings) -> None:
        gen = StorylineGenerator(settings=mock_settings)
        analysis = AnalysisInput(
            pages=[
                PageInput(
                    url="https://example.com/#section1",
                    title="Section 1",
                    page_id="p1",
                    is_spa_section=True,
                ),
            ],
            site=SiteInput(description="SPA app"),
        )
        prompt = gen._build_prompt(analysis, None, 3.0, "", "webapp_commercial")
        assert "[SPA]" in prompt


class TestTemplateFiles:
    """Tests that all expected template files exist."""

    def test_storyline_template_exists(self) -> None:
        assert (_TEMPLATES_DIR / "storyline.md").exists()

    def test_critique_video_template_exists(self) -> None:
        assert (_TEMPLATES_DIR / "critique_video.md").exists()

    def test_review_video_template_exists(self) -> None:
        assert (_TEMPLATES_DIR / "review_video.md").exists()
