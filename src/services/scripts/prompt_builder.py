"""Build prompts for Claude script generation.

Ported from legacy web_to_video.generator.prompt_builder, adapted for
dict-based part/analysis data used by the microservice.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

# Default completion token — matches the value in GeneratorSettings.
COMPLETION_TOKEN = "<promise>COMPLETE</promise>"  # nosec B105


def _sanitize_md(value: object) -> str:
    """Escape Markdown-special characters in a value before prompt interpolation.

    Prevents raw dict values (scraped from websites) from breaking the prompt
    structure -- e.g. embedded ``**``, ``---``, or backticks.  The value is
    converted to ``str`` first so non-string values are handled safely.
    """
    text = str(value)
    # Replace horizontal-rule markers that could split Markdown sections.
    text = text.replace("---", "\\-\\-\\-")
    # Escape bold/italic markers.
    text = text.replace("**", "\\*\\*")
    text = text.replace("__", "\\_\\_")
    # Escape triple backticks that could break code fences.
    text = text.replace("```", "\\`\\`\\`")
    # Escape heading markers at the start of a line.
    text = text.replace("\n#", "\n\\#")
    if text.startswith("#"):
        text = "\\" + text
    return text


class PromptBuilder:
    """Build prompts from shot list parts + analysis data.

    Unlike the legacy version which consumes ``ShotListPart`` dataclasses,
    this microservice variant works with plain dicts matching the API request
    schema.  All attribute access on ``part`` uses ``.get()`` with safe
    defaults.
    """

    def __init__(
        self,
        templates_dir: Path | None = None,
        auth_enabled: bool = False,
        mode: str = "webapp_commercial",
        source_type: str = "web",
        completion_token: str = COMPLETION_TOKEN,
    ) -> None:
        """Initialize the prompt builder.

        Args:
            templates_dir: Path to Markdown template files.
                Defaults to ``src/templates/scripts/``.
            auth_enabled: Whether to include auth context in prompts.
            mode: Generation mode (e.g. 'webapp_commercial').
            source_type: Platform type ('web', 'ios', 'android').
            completion_token: Token the LLM must include to signal
                complete output.
        """
        self.templates_dir = templates_dir or (
            Path(__file__).parent.parent.parent / "templates" / "scripts"
        )
        self.auth_enabled = auth_enabled
        self.mode = mode
        self.source_type = source_type
        self.completion_token = completion_token

    def with_overrides(
        self,
        *,
        mode: str | None = None,
        source_type: str | None = None,
        auth_enabled: bool | None = None,
    ) -> PromptBuilder:
        """Return a new builder with request-scoped overrides applied."""
        next_mode = mode or self.mode
        next_source_type = source_type or self.source_type
        next_auth_enabled = self.auth_enabled if auth_enabled is None else auth_enabled

        if (
            next_mode == self.mode
            and next_source_type == self.source_type
            and next_auth_enabled == self.auth_enabled
        ):
            return self

        return PromptBuilder(
            templates_dir=self.templates_dir,
            auth_enabled=next_auth_enabled,
            mode=next_mode,
            source_type=next_source_type,
            completion_token=self.completion_token,
        )

    # ------------------------------------------------------------------
    # Template loading
    # ------------------------------------------------------------------

    def _load_template(self, name: str) -> str | None:
        """Load a ``.md`` template file from templates_dir.

        Returns the file content as a string, or ``None`` if the file does not
        exist or does not resolve inside ``templates_dir``.

        Containment: ``name`` is derived from ``page_type``, which is
        attacker-influenceable page-analysis data. ``templates_dir / name``
        happily accepts ``../../etc/x.md`` (traversal) and, because ``Path.__
        truediv__`` with an absolute operand DISCARDS the left side, also
        accepts a fully absolute path. Without this check an arbitrary readable
        ``.md`` file was inlined into the LLM prompt and then persisted by
        ``store_prompt``. The schema constrains ``page_type`` too, but that
        edge is bypassed by every non-HTTP caller, so the check lives here as
        well.
        """
        candidate = self.templates_dir / name
        try:
            root = self.templates_dir.resolve()
            resolved = candidate.resolve()
        except OSError, RuntimeError:
            logger.warning("Could not resolve template path for %r", name)
            return None
        if not resolved.is_relative_to(root):
            logger.warning("Refusing to load template outside templates_dir: %r", name)
            return None
        if resolved.exists():
            return resolved.read_text()
        return None

    # ------------------------------------------------------------------
    # Main build
    # ------------------------------------------------------------------

    def build(
        self,
        part: dict,
        page_analysis: dict[str, dict] | None = None,
        storyline_context: str = "",
        theme: dict | None = None,
        user_context: dict | None = None,
        message: str = "",
    ) -> str:
        """Build a complete prompt for script generation."""
        analysis_pages = page_analysis or {}

        if self.source_type in ("ios", "android"):
            return self._build_mobile(
                part,
                analysis_pages,
                storyline_context,
                message,
            )

        sections: list[str] = [
            self._base_instructions(),
            self._part_details(part),
            self._page_analysis(part, analysis_pages),
            self._page_type_template(part, analysis_pages),
            self._demo_helpers_api(),
            self._storyline_context(storyline_context),
        ]

        if user_context:
            ctx_section = self._user_context_section(user_context)
            if ctx_section:
                sections.append(ctx_section)

        if message:
            sections.append(f"## User Direction\n\n{message}")

        if self.auth_enabled:
            sections.append(self._auth_context())

        sections.append(self._completion_instructions())

        return "\n\n---\n\n".join(s for s in sections if s)

    # ------------------------------------------------------------------
    # Section builders
    # ------------------------------------------------------------------

    def _base_instructions(self) -> str:
        """Load mode-specific base prompt, falling back to legacy name."""
        content = self._load_template(f"base_{self.mode}.md")
        if content is not None:
            return content

        content = self._load_template("base_prompt.md")
        if content is not None:
            return content

        logger.warning(
            "No base prompt template found in %s -- using hardcoded fallback",
            self.templates_dir,
        )
        return (
            "# Playwright Demo Script Generation\n\n"
            "You are generating a Playwright demo script that will be recorded as video.\n\n"
            "## Requirements\n"
            "- Import from `@playwright/test` and `../helpers/demo-helpers`\n"
            "- Use `test.describe()` and `test.step()` for structure\n"
            "- Use demo helpers: `showChapterTitle`, `annotateAndWait`, `highlightElement`, "
            "`scrollToElement`, `enableCursorTrail`, `smoothScroll`, `slowType`, "
            "`pause`, `waitForVisualStability`\n"
            "- CRITICAL: Always call `scrollToElement(page, selector)` before "
            "`highlightElement()` or `spotlightElement()` -- elements below the fold "
            "will be invisible otherwise\n"
            "- Target 1920x1080 viewport with 300ms slowMo\n"
            "- Pace interactions for viewer comprehension (2-3s between actions)\n"
            "- Use annotations to explain what's happening on screen\n"
            "- Always call `waitForVisualStability(page)` after navigation\n"
            f"- End with `{self.completion_token}` token\n"
        )

    def _part_details(self, part: dict) -> str:
        """Render part metadata with duration budget hints."""
        dur = float(part.get("estimated_duration_seconds", 30))
        subtitle = part.get("subtitle", "")
        has_focus = bool(subtitle)

        if dur <= 25:
            budget_hint = "Brief: navigate + highlight 1-2 elements + one annotation"
        elif dur <= 40:
            budget_hint = "Short: navigate + 2-3 highlights + one scroll + 1-2 annotations"
        elif dur <= 60:
            budget_hint = "Medium: explore 2-3 sections + 2-3 annotations"
        else:
            budget_hint = "Full: walkthrough with multiple sections and annotations"

        # When a Chapter Focus is present, clarify that even longer budgets
        # should stay within the focused area instead of roaming the whole page.
        if has_focus:
            budget_hint += (
                " — but ONLY within the Chapter Focus area below. "
                "Do not wander to unrelated page sections even if time allows."
            )

        part_id = part.get("id", "unknown")
        title = part.get("title", "")
        chapter = part.get("chapter") or "N/A"
        pages = part.get("pages", [])
        actions = part.get("actions", [])
        key_elements = part.get("key_elements", [])

        lines = [
            f"# Part: {part_id}",
            f"**Title:** {title}",
            f"**Chapter:** {chapter}",
        ]
        if has_focus:
            lines.extend(
                [
                    f"**Chapter Focus:** {subtitle}",
                    (
                        "The Chapter Focus defines which area of the page this script "
                        "should explore. Target your scrolling, highlighting, and "
                        "annotations to the sections described above. "
                        "Do NOT cover the entire page — stay focused on the "
                        "Chapter Focus area."
                    ),
                ]
            )
        lines.extend(
            [
                f"**Duration budget:** {dur:.0f}s -- {budget_hint}",
                "Keep total pause time under 40% of the budget. Shorter is better than longer.",
                f"**Pages:** {', '.join(pages) if pages else 'N/A'}",
                "",
                "## Required Actions",
            ]
        )

        if has_focus and actions:
            lines.append(
                "Note: If an action references a specific page section "
                "(e.g. \"Highlight 'Features'\"), scroll to that section first "
                "with `scrollToElement` and concentrate all interactions there "
                "before moving on."
            )

        for i, action in enumerate(actions, 1):
            clean = _sanitize_md(action.lstrip("# "))
            lines.append(f"{i}. {clean}")

        if key_elements:
            lines.append("\n## Key Element Selectors")
            for sel in key_elements:
                lines.append(f"- `{sel}`")

        return "\n".join(lines)

    def _page_analysis(self, part: dict, analysis_pages: dict[str, dict]) -> str:
        """Render page analysis with sanitized values."""
        refs = part.get("pages", [])
        if not refs:
            return "# Page Analysis\n\nNo page analysis available."

        lines = ["# Page Analysis"]
        for ref in refs:
            page_data = analysis_pages.get(ref, {})
            if not page_data:
                logger.debug("Page analysis ref %r not found in analysis_pages", ref)
                continue
            lines.append(f"\n## {ref}")
            if isinstance(page_data, dict):
                for key, value in page_data.items():
                    sanitized = _sanitize_md(value)
                    lines.append(f"- **{key}:** {sanitized}")
        return "\n".join(lines)

    def _page_type_template(self, part: dict, analysis_pages: dict[str, dict]) -> str:
        """Load page-type-specific template if available."""
        page_type = "generic"
        refs = part.get("pages", [])
        if refs:
            for ref in refs:
                page_data = analysis_pages.get(ref, {})
                if isinstance(page_data, dict) and page_data.get("page_type"):
                    raw = page_data["page_type"]
                    # Handle Enum objects (e.g. from dataclasses.asdict())
                    page_type = raw.value if isinstance(raw, Enum) else str(raw)
                    break

        content = self._load_template(f"{page_type}.md")
        if content is not None:
            return content

        # Fall back to generic
        generic = self._load_template("generic.md")
        if generic is not None:
            logger.debug("No template for page type '%s', using generic.md", page_type)
            return generic

        logger.warning(
            "No page-type template for '%s' and no generic.md fallback -- "
            "script generation may produce suboptimal results",
            page_type,
        )
        return ""

    def _demo_helpers_api(self) -> str:
        """Return a condensed cheat sheet of the demo helpers API.

        Instead of dumping full TypeScript declarations (200+ lines that
        overwhelm smaller models), this returns a compact table with only the
        functions that matter, one usage pattern, and one critical rule.
        """
        return (
            "# Demo Helpers Cheat Sheet\n\n"
            "## Core Functions (use ONLY these)\n\n"
            "| Function | When to use |\n"
            "|----------|-------------|\n"
            "| `scrollToElement(page, selector)` | **ALWAYS before highlight/spotlight** |\n"
            "| `safeHighlight(page, selector, label?, ms?)` | Draw pulsing border + label on element |\n"
            "| `spotlightElement(page, selector, label?, ms?)` | Dim page, spotlight one element |\n"
            "| `annotateAndWait(page, text, ms?, pos?)` | Floating label overlay |\n"
            "| `smoothScroll(page, { direction, distance, duration })` | Animated page scroll |\n"
            "| `safeClick(page, selector)` | Click (skips if element missing) |\n"
            "| `safeType(page, selector, text)` | Type into form field |\n"
            "| `pause(page, ms)` | Fixed delay |\n\n"
            "## Setup (always do these)\n"
            "- `enableCursorTrail(page)` at script start\n"
            "- `waitForVisualStability(page)` after every `page.goto()`\n\n"
            "## CRITICAL RULE\n"
            "**ALWAYS** call `scrollToElement(page, selector)` then `pause(page, 500)` "
            "BEFORE `safeHighlight()` or `spotlightElement()`. "
            "Elements below the fold are INVISIBLE without scrolling first.\n\n"
            "## Pattern\n"
            "```typescript\n"
            "await scrollToElement(page, '.section');\n"
            "await pause(page, 500);\n"
            "await safeHighlight(page, '.section h2', 'Section Title', 3000);\n"
            "```\n"
        )

    def _storyline_context(self, context: str) -> str:
        """Render storyline context section."""
        if not context:
            return ""
        return f"# Storyline Context\n\n{context}"

    def _auth_context(self) -> str:
        """Render authentication context section."""
        return (
            "# Authentication Context\n\n"
            "This site requires authentication. The browser session is **already logged in** "
            "via Playwright's `storageState` mechanism -- cookies and "
            "local storage are pre-loaded.\n\n"
            "**Important:**\n"
            "- Do NOT include any login steps in your script\n"
            "- Do NOT navigate to the login page\n"
            "- Start directly from the authenticated page URL\n"
            "- If the page redirects to login, the auth setup has "
            "failed -- do not try to handle it\n"
        )

    def _user_context_section(self, ctx: dict) -> str:
        """Build a prompt section from user-provided context.

        Accepts a dict with optional keys: ``target_audience``, ``goals``,
        ``narration_guidance`` (which may contain ``key_messages``).
        """
        lines: list[str] = ["# User Context"]

        target_audience = ctx.get("target_audience")
        if target_audience:
            lines.append(f"\n**Target Audience:** {_sanitize_md(target_audience)}")

        goals = ctx.get("goals")
        if goals:
            lines.append("\n## Goals")
            for goal in goals:
                lines.append(f"- {_sanitize_md(goal)}")

        narration_guidance = ctx.get("narration_guidance", {})
        key_messages = (
            narration_guidance.get("key_messages", [])
            if isinstance(narration_guidance, dict)
            else []
        )
        if key_messages:
            lines.append("\n## Key Things to Highlight")
            for msg in key_messages:
                lines.append(f"- {_sanitize_md(msg)}")

        # Only return the section if there is meaningful content beyond the heading.
        if len(lines) <= 1:
            return ""
        return "\n".join(lines)

    def _completion_instructions(self) -> str:
        """Render completion token section."""
        return (
            "# Completion\n\n"
            "Output the complete TypeScript file in a ```typescript code block.\n"
            f"End your response with: `{self.completion_token}`\n"
        )

    # ------------------------------------------------------------------
    # Mobile prompt building
    # ------------------------------------------------------------------

    def _build_mobile(
        self,
        part: dict,
        analysis_pages: dict[str, dict],
        storyline_context: str,
        message: str = "",
    ) -> str:
        """Build a prompt for mobile (WebdriverIO) script generation."""
        sections: list[str] = [
            self._base_instructions_mobile(),
            self._part_details(part),
            self._page_analysis(part, analysis_pages),
            self._screen_type_template(part, analysis_pages),
            self._mobile_helpers_api(),
            self._storyline_context(storyline_context),
        ]
        if message:
            sections.append(f"## User Direction\n\n{message}")
        sections.append(self._completion_instructions())
        return "\n\n---\n\n".join(s for s in sections if s)

    def _base_instructions_mobile(self) -> str:
        """Load mobile base prompt template."""
        content = self._load_template("mobile/base_mobile.md")
        if content is not None:
            platform = self.source_type
            platform_display = "iOS" if platform == "ios" else "Android"
            automation = "XCUITest" if platform == "ios" else "UiAutomator2"
            return (
                content.replace("{{ platform }}", platform)
                .replace("{{ platform_display }}", platform_display)
                .replace("{{ automation_name }}", automation)
                .replace("{{ app_identifier }}", "")
            )
        logger.warning("No mobile base template found -- using hardcoded fallback")
        return (
            "# Mobile App Demo Script Generation\n\n"
            "You are generating a WebdriverIO test script for a mobile app demo.\n\n"
            "## Requirements\n"
            "- Import from `../helpers/mobile-demo-helpers`\n"
            "- Use `describe/it` pattern for structure\n"
            "- Use mobile helpers: `tapElement`, `typeText`, `swipeScreen`, "
            "`scrollToElement`, `pause`, `waitForScreen`, `assertVisible`, `goBack`\n"
            f"- End with `{self.completion_token}` token\n"
        )

    def _screen_type_template(self, part: dict, analysis_pages: dict[str, dict]) -> str:
        """Load screen-type-specific template for mobile."""
        screen_type = "unknown"
        refs = part.get("pages", [])
        if refs:
            for ref in refs:
                page_data = analysis_pages.get(ref, {})
                if isinstance(page_data, dict) and page_data.get("screen_type"):
                    raw = page_data["screen_type"]
                    screen_type = raw.value if isinstance(raw, Enum) else str(raw)
                    break

        # Map screen types to template files
        template_map = {
            "home": "home.md",
            "login": "login.md",
            "list": "list.md",
            "detail": "detail.md",
            "form": "form.md",
            "settings": "settings.md",
        }
        template_name = template_map.get(screen_type, "home.md")
        content = self._load_template(f"mobile/{template_name}")
        if content is not None:
            return content

        # Fall back to home as default
        fallback = self._load_template("mobile/home.md")
        if fallback is not None:
            return fallback
        return ""

    def _mobile_helpers_api(self) -> str:
        """Return a condensed cheat sheet of mobile demo helpers."""
        return (
            "# Mobile Demo Helpers Cheat Sheet\n\n"
            "## Core Functions (use ONLY these)\n\n"
            "| Function | When to use |\n"
            "|----------|-------------|\n"
            "| `tapElement(selector, description?)` | Tap by accessibility ID |\n"
            "| `tapElementBy(strategy, value, description?)` | Tap by custom strategy |\n"
            "| `typeText(selector, text, charDelay?)` | Type character by character |\n"
            "| `swipeScreen(direction, distance?, duration?)` | Swipe gesture |\n"
            "| `scrollToElement(selector, maxScrolls?, direction?)` | Scroll until visible |\n"
            "| `waitForScreen(timeout?)` | Wait for screen stability |\n"
            "| `assertVisible(selector, description?)` | Verify element visible |\n"
            "| `goBack()` | Press system back button |\n"
            "| `pause(ms)` | Fixed delay |\n\n"
            "## Setup\n"
            "- `waitForScreen()` at start of each test\n"
            "- `assertVisible()` before interacting with elements\n\n"
            "## CRITICAL RULES\n"
            "- Use accessibility IDs (`~selector`) as primary selector strategy\n"
            "- Add `pause(1000)` between major demo steps for recording clarity\n"
            "- Prefer `swipeScreen('up')` over scroll for mobile apps\n"
        )
