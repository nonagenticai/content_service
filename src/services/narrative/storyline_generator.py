"""Generate video storylines via the WM LLM gateway.

w2v services NEVER call openai/anthropic/inference-router directly — every
LLM call goes through ``worker_management.POST /workers/{name}/message``
so cost is attributed per tenant and providers can be swapped server-side.
See memory ``wm-as-sole-llm-gateway.md`` for the canonical pattern.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import jinja2
from w2v_common.clients import WMClient

from src.api.narrative_schemas import AnalysisInput, LLMConfigOverride, SiteInput
from src.api.narrative_types import UserContext
from src.config import Settings, get_settings
from src.services.narrative.llm_retry import LLMExhaustedError, LLMRetryConfig, call_llm_with_retry

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates" / "storyline"

_STORYLINE_WORKER_NAME = "storyline-generator"

_STORYLINE_SYSTEM_PROMPT = (
    "You are a video storyline architect. You design demo video storylines "
    "for websites, returning a single JSON object with keys video_title, "
    "video_description, target_duration_minutes, tone, intro, chapters, and "
    "outro. Output ONLY the JSON object — no prose, no markdown fences."
)


def _extract_chapters_flexibly(data: dict) -> list[dict] | None:
    """Extract a chapters list from LLM-generated JSON using common key variations."""
    for key in ("chapters", "sections", "parts", "video_chapters", "scenes", "segments"):
        if key in data and isinstance(data[key], list) and len(data[key]) > 0:
            return data[key]
    for value in data.values():
        if (
            isinstance(value, list)
            and len(value) > 0
            and isinstance(value[0], dict)
            and any(k in value[0] for k in ("title", "description", "page_url", "url"))
        ):
            return value
    return None


class StorylineGenerator:
    """Generate video storylines via the WM LLM gateway."""

    def __init__(
        self,
        settings: Settings | None = None,
        wm_client: WMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._wm = (
            wm_client
            if wm_client is not None
            else WMClient(timeout=float(self.settings.narrative_llm_timeout_seconds))
        )

    async def generate(
        self,
        analysis: AnalysisInput,
        reviews: Mapping[str, object] | None = None,
        duration_minutes: float = 3.0,
        message: str = "",
        user_context: UserContext | None = None,
        mode: str = "webapp_commercial",
        llm_config: LLMConfigOverride | None = None,
    ) -> dict:
        """Generate a video storyline from website analysis via LLM.

        Args:
            analysis: Website analysis input with pages and site metadata.
            reviews: Optional review data keyed by review type.
            duration_minutes: Target video duration (0 for auto-detection).
            message: Optional user direction text.
            user_context: Optional user preferences for tone, audience, etc.
            mode: Storyline template mode (e.g. webapp_commercial, critique_video).

        Returns:
            Storyline dict with intro, chapters, and outro.
        """
        t0 = time.monotonic()
        logger.info(
            "StorylineGenerator.generate: mode=%s, pages=%d, duration=%.1f min",
            mode,
            len(analysis.pages),
            duration_minutes,
        )

        analysis, duration_minutes, message = self._prepare_input(
            analysis, duration_minutes, message, user_context
        )
        prompt = self._build_prompt(analysis, reviews, duration_minutes, message, mode)
        result = await self._call_llm_with_retry(
            prompt, analysis, duration_minutes, llm_config=llm_config
        )
        result = self._post_process(result, analysis, user_context)

        elapsed = time.monotonic() - t0
        logger.info(
            "StorylineGenerator.generate: completed in %.2fs, %d chapters",
            elapsed,
            len(result.get("chapters", [])),
        )
        return result

    def _prepare_input(
        self,
        analysis: AnalysisInput,
        duration_minutes: float,
        message: str,
        user_context: UserContext | None,
    ) -> tuple[AnalysisInput, float, str]:
        """Prepare inputs: override duration, auto-detect, build context, filter pages."""
        if user_context and user_context.narration_guidance.target_length_minutes > 0:
            duration_minutes = user_context.narration_guidance.target_length_minutes

        if duration_minutes == 0:
            duration_minutes = self._auto_detect_duration(len(analysis.pages))
            logger.info(
                "Auto target duration: %.1f min for %d pages",
                duration_minutes,
                len(analysis.pages),
            )

        if user_context:
            context_section = self._build_context_section(user_context)
            if context_section:
                message = f"{context_section}\n\n{message}" if message else context_section

        if user_context and user_context.exclude_pages:
            filtered_pages = [
                p
                for p in analysis.pages
                if not any(excl in p.url for excl in user_context.exclude_pages)
            ]
            if len(filtered_pages) < len(analysis.pages):
                logger.info(
                    "Excluded %d pages from storyline",
                    len(analysis.pages) - len(filtered_pages),
                )
                analysis = AnalysisInput(pages=filtered_pages, site=analysis.site)

        return analysis, duration_minutes, message

    async def _call_llm_with_retry(
        self,
        prompt: str,
        analysis: AnalysisInput,
        duration_minutes: float,
        llm_config: LLMConfigOverride | None = None,
    ) -> dict:
        """Call WM with retry logic, returning parsed result or fallback."""
        model_override = llm_config.model if (llm_config and llm_config.model) else None

        config = LLMRetryConfig(
            max_attempts=self.settings.storyline.max_retry_attempts,
            error_correction_prompt=(
                "The previous response was not valid JSON. "
                "Your response MUST be a single JSON object:\n"
                '{"video_title": "...", "chapters": [...], '
                '"intro": {...}, "outro": {...}}'
            ),
            timeout_seconds=self.settings.narrative_llm_timeout_seconds,
            service_name="storyline",
        )
        try:
            result = await call_llm_with_retry(
                wm=self._wm,
                worker_name=_STORYLINE_WORKER_NAME,
                system_prompt=_STORYLINE_SYSTEM_PROMPT,
                prompt=prompt,
                config=config,
                model_override=model_override,
            )
            parsed = self._parse_storyline(result.content, analysis, duration_minutes)
            if result.token_usage:
                parsed["token_usage"] = result.token_usage
            return parsed
        except LLMExhaustedError:
            logger.warning("All storyline attempts exhausted, using fallback")
            return self._default_storyline(analysis, duration_minutes)

    def _post_process(
        self, result: dict, analysis: AnalysisInput, user_context: UserContext | None
    ) -> dict:
        """Validate URLs, apply feature weights, rebalance chapters."""
        result = self._validate_chapter_urls(result, analysis)

        if user_context and user_context.feature_priorities:
            result = self._apply_feature_weights(result, user_context)

        from src.services.narrative.chapter_planner import ChapterPlanner

        planner = ChapterPlanner(
            min_chapter_duration_seconds=self.settings.storyline.min_chapter_duration_seconds,
        )
        warnings = planner.validate(result)
        for w in warnings:
            logger.warning("Storyline: %s", w)
        return planner.rebalance(result)

    @staticmethod
    def _format_page_list(analysis: AnalysisInput) -> str:
        page_lines: list[str] = []
        for i, p in enumerate(analysis.pages[:60]):
            pid = p.page_id or f"page-{i + 1:03d}"
            spa_tag = " [SPA]" if p.is_spa_section else ""
            page_lines.append(
                f"- {pid}: {p.title or 'Untitled'} ({p.page_type}) -- {p.url}{spa_tag}"
            )
        return "\n".join(page_lines)

    @staticmethod
    def _format_page_content_block(analysis: AnalysisInput) -> str:
        page_content_lines: list[str] = []
        for i, p in enumerate(analysis.pages[:20]):
            content_parts: list[str] = []
            if p.headings:
                content_parts.append(f"  Headings: {', '.join(p.headings[:25])}")
            if p.content_sections:
                section_names = [
                    s.get("heading", s.get("section_type", ""))
                    for s in p.content_sections[:20]
                    if isinstance(s, dict)
                ]
                if section_names:
                    content_parts.append(f"  Sections: {', '.join(section_names)}")
            if content_parts:
                pid = p.page_id or f"page-{i + 1:03d}"
                page_content_lines.append(f"### {pid}: {p.title}\n" + "\n".join(content_parts))
        if not page_content_lines:
            return ""
        return (
            "\n\n## Page Content Details\n\n"
            "Use these headings and sections to generate SPECIFIC key_moments "
            "that reference real page elements:\n\n" + "\n\n".join(page_content_lines)
        )

    @staticmethod
    def _format_reviews_section(reviews: Mapping[str, object] | None) -> str:
        if not reviews:
            return ""
        summaries: list[str] = []
        for rt, rd in reviews.items():
            if isinstance(rd, dict) and "error" not in rd:
                review_json = json.dumps(rd, indent=2)[:8000]
                summaries.append(f"### {rt.replace('_', ' ').title()}\n{review_json}")
        if not summaries:
            return ""
        return "\n\n## Website Reviews\n" + "\n\n".join(summaries)

    def _build_prompt(
        self,
        analysis: AnalysisInput,
        reviews: Mapping[str, object] | None,
        duration_minutes: float,
        message: str,
        mode: str = "webapp_commercial",
    ) -> str:
        """Build the complete LLM prompt from template and analysis data."""
        site = analysis.site
        page_list = self._format_page_list(analysis)
        page_content_block = self._format_page_content_block(analysis)
        reviews_section = self._format_reviews_section(reviews)

        spa_detected = any(p.is_spa_section for p in analysis.pages)
        spa_note = (
            "\n\nNote: Pages marked [SPA] appear to be sections of a "
            "single-page application. Consider combining them into fewer "
            "chapters.\n"
            if spa_detected
            else ""
        )
        message_section = f"\n\n## User Direction\n{message}\n" if message else ""

        # Try loading a Jinja2 template
        template_prompt = self._load_template(
            mode, duration_minutes, analysis=analysis, reviews=reviews
        )
        if template_prompt:
            context = (
                f"\n\n## Site Description\n{site.description}"
                f"\n\n## Target Audience\n"
                f"{', '.join(site.target_audience) if site.target_audience else 'Unknown'}"
                f"\n\n## Use Cases\n"
                + ("\n".join(f"- {uc}" for uc in site.use_cases) if site.use_cases else "N/A")
                + f"\n\n## Pages Available\n{page_list}{spa_note}"
                f"\n\n## Tone\n{site.tone}"
                f"{reviews_section}{message_section}"
                f"{page_content_block}"
            )
            return template_prompt + context

        return self._default_prompt(
            site,
            page_list,
            spa_note,
            reviews_section,
            message_section,
            duration_minutes,
            page_content_block,
        )

    @staticmethod
    def _populate_critique_template_vars(
        template_vars: dict[str, object], reviews: Mapping[str, object]
    ) -> None:
        categories = [rt.replace("_", " ").title() for rt in reviews]
        template_vars["categories"] = ", ".join(categories)
        total_findings = 0
        for rd in reviews.values():
            if isinstance(rd, dict):
                rd_cast = cast(dict[str, object], rd)
                findings = rd_cast.get("findings", [])
                if isinstance(findings, list):
                    total_findings += len(findings)
        template_vars["total_findings"] = str(total_findings)
        score_lines: list[str] = []
        for rt, rd in reviews.items():
            if isinstance(rd, dict):
                rd_typed = cast(dict[str, object], rd)
                if "score" in rd_typed:
                    score_lines.append(f"- {rt.replace('_', ' ').title()}: {rd_typed['score']}/100")
        template_vars["scores"] = "\n".join(score_lines) if score_lines else "N/A"

    @staticmethod
    def _populate_review_template_vars(
        template_vars: dict[str, object], reviews: Mapping[str, object]
    ) -> None:
        review_types = [rt.replace("_", " ").title() for rt in reviews]
        template_vars["review_types"] = ", ".join(review_types)
        summary_lines: list[str] = []
        for rt, rd in reviews.items():
            if isinstance(rd, dict) and "error" not in rd:
                preview = json.dumps(rd, indent=2)[:500]
                summary_lines.append(f"### {rt.replace('_', ' ').title()}\n{preview}")
        template_vars["reviews_summary"] = "\n\n".join(summary_lines) if summary_lines else "N/A"

    def _load_template(
        self,
        mode: str,
        duration_minutes: float,
        analysis: AnalysisInput | None = None,
        reviews: Mapping[str, object] | None = None,
    ) -> str | None:
        """Load and render a Jinja2 storyline template for the given mode."""
        # Mode-specific template (e.g. critique_video.md, review_video.md)
        template_path = _TEMPLATES_DIR / f"{mode}.md"
        if not template_path.exists():
            # Fall back to the main storyline.md which uses Jinja2 conditionals for mode
            template_path = _TEMPLATES_DIR / "storyline.md"
        if not template_path.exists():
            logger.debug("No storyline template found for mode '%s'", mode)
            return None

        template_vars: dict[str, object] = {
            "mode": mode,
            "target_duration_minutes": duration_minutes,
        }

        # Populate site context vars from analysis
        if analysis:
            template_vars["site_name"] = analysis.site.description or ""
            template_vars["url"] = analysis.pages[0].url if analysis.pages else ""

        # Populate mode-specific vars from reviews
        if mode == "critique_video" and reviews:
            self._populate_critique_template_vars(template_vars, reviews)
        if mode == "review_video" and reviews:
            self._populate_review_template_vars(template_vars, reviews)

        try:
            template_source = template_path.read_text()
            tpl = jinja2.Template(template_source, undefined=jinja2.Undefined)
            return tpl.render(**template_vars)
        except jinja2.TemplateError as exc:
            logger.warning("Storyline template rendering failed for mode '%s': %s", mode, exc)
            return None

    @staticmethod
    def _default_prompt(
        site: SiteInput,
        page_list: str,
        spa_note: str,
        reviews_section: str,
        message_section: str,
        duration_minutes: float,
        page_content_block: str = "",
    ) -> str:
        """Inline fallback prompt when no template is available."""
        return (
            "You are creating a demo video storyline for a website.\n"
            "Structure the video with an intro title card, 2-5 chapters, and an outro.\n"
            "Each chapter should focus on a logical theme and reference specific pages.\n"
            f"Target approximately {duration_minutes:.1f} minutes total duration.\n\n"
            "Respond with a JSON object:\n"
            "{\n"
            '  "video_title": "string",\n'
            '  "video_description": "string",\n'
            f'  "target_duration_minutes": {duration_minutes},\n'
            '  "tone": "string",\n'
            '  "intro": {"title": "string", "subtitle": "string", '
            '"duration_seconds": 8, "narrative_text": "string"},\n'
            '  "chapters": [\n'
            "    {\n"
            '      "number": 1,\n'
            '      "title": "string",\n'
            '      "subtitle": "string",\n'
            '      "pages": ["page_id_or_url"],\n'
            '      "narrative_arc": "string",\n'
            '      "key_moments": ["string"],\n'
            '      "estimated_duration_seconds": 60\n'
            "    }\n"
            "  ],\n"
            '  "outro": {"title": "string", "subtitle": "string", '
            '"duration_seconds": 6, "narrative_text": "string"}\n'
            "}\n\n"
            f"## Site Description\n{site.description}\n\n"
            "## Target Audience\n"
            f"{', '.join(site.target_audience) if site.target_audience else 'Unknown'}\n\n"
            "## Use Cases\n"
            + ("\n".join(f"- {uc}" for uc in site.use_cases) if site.use_cases else "N/A")
            + f"\n\n## Pages Available\n{page_list}{spa_note}\n\n"
            f"## Tone\n{site.tone}"
            f"{reviews_section}{message_section}"
            f"{page_content_block}\n\n"
            "Respond with ONLY the JSON object."
        )

    def _parse_storyline(self, raw: str, analysis: AnalysisInput, duration_minutes: float) -> dict:
        """Parse raw LLM JSON output into a validated storyline dict.

        Args:
            raw: Raw LLM response text expected to contain JSON.
            analysis: Analysis input for fallback storyline generation.
            duration_minutes: Target duration for fallback.

        Returns:
            Parsed storyline dict with intro, chapters, and outro.
        """
        try:
            start = raw.find("{")
            end = raw.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(raw[start:end])
                chapters_data = _extract_chapters_flexibly(data)
                if not chapters_data:
                    raise ValueError("No chapters found")

                intro = data.get("intro", {})
                outro = data.get("outro", {})

                # ⚠️ The emptiness check above is on the RAW extraction, not on
                # what survives the ``isinstance(ch, dict)`` filter below — and
                # ``_extract_chapters_flexibly`` accepts ANY non-empty list. So an
                # LLM that answers ``{"chapters": ["Intro", "Features"]}`` (a list
                # of plain strings — a routine failure mode when the model
                # summarises instead of emitting objects) sailed past the guard and
                # then had every element filtered out, yielding a storyline with
                # ``"chapters": []``.
                #
                # That is a MEASUREMENT GAP scoring as a real measurement:
                # ``_default_storyline`` — the intended fallback, and the ONLY
                # thing that sets ``degraded: True`` — was never reached, so
                # ``storyline._is_degraded`` returned False and the empty
                # storyline was written to the tenant result cache and replayed to
                # every identical request for ``STORYLINE_CACHE_TTL``. Downstream,
                # a zero-chapter storyline produces an intro+outro-only shot list,
                # i.e. a ~14-second video, reported ``completed``.
                #
                # Validate what we ACTUALLY built. Raising here lands in the
                # ``except`` below and returns the degraded fallback, which is
                # correct AND uncacheable.
                chapters = [
                    {
                        "number": int(ch.get("number", i + 1)),
                        "title": str(ch.get("title", f"Chapter {i + 1}")),
                        "subtitle": str(ch.get("subtitle", "")),
                        "pages": list(ch.get("pages", [])),
                        "narrative_arc": str(ch.get("narrative_arc", "")),
                        "key_moments": list(ch.get("key_moments", [])),
                        "estimated_duration_seconds": float(
                            ch.get("estimated_duration_seconds", 60)
                        ),
                    }
                    for i, ch in enumerate(chapters_data)
                    if isinstance(ch, dict)
                ]
                if not chapters:
                    raise ValueError(
                        f"LLM returned {len(chapters_data)} chapter entries but none were "
                        "JSON objects — refusing to ship a zero-chapter storyline as success"
                    )

                return {
                    "video_title": str(data.get("video_title", "Website Demo")),
                    "video_description": str(data.get("video_description", "")),
                    "target_duration_minutes": float(
                        data.get("target_duration_minutes", duration_minutes)
                    ),
                    "tone": str(data.get("tone", "professional")),
                    "intro": {
                        "title": str(intro.get("title", "")),
                        "subtitle": str(intro.get("subtitle", "")),
                        "duration_seconds": float(intro.get("duration_seconds", 8)),
                        "narrative_text": str(intro.get("narrative_text", "")),
                    },
                    "chapters": chapters,
                    "outro": {
                        "title": str(outro.get("title", "")),
                        "subtitle": str(outro.get("subtitle", "")),
                        "duration_seconds": float(outro.get("duration_seconds", 6)),
                        "narrative_text": str(outro.get("narrative_text", "")),
                    },
                }
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            logger.warning("Failed to parse storyline: %s", exc)

        return self._default_storyline(analysis, duration_minutes)

    def _default_storyline(self, analysis: AnalysisInput, duration_minutes: float) -> dict:
        """Build a fallback storyline when LLM generation fails."""
        pages = analysis.pages
        site = analysis.site
        target_seconds = duration_minutes * 60
        available_seconds = target_seconds - 14.0

        # Derive a short brand name from the page title or site description
        raw_name = ""
        if pages and pages[0].title:
            # Take text before common separators (|, -, —, :)
            raw_name = (
                pages[0].title.split("|")[0].split(" - ")[0].split(" — ")[0].split(":")[0].strip()
            )
        if not raw_name or len(raw_name) > 40:
            raw_name = (
                site.description.split(".")[0][:40].strip() if site.description else "Website"
            )
        name = raw_name or "Website"

        chapters = []
        capped_pages = pages[:5]
        for i, page in enumerate(capped_pages):
            ch_duration = max(available_seconds / max(len(capped_pages), 1), 20.0)
            # Generate key_moments from page headings
            key_moments = [h for h in (page.headings or []) if 4 < len(h) < 80][:4]
            if not key_moments and page.content_sections:
                key_moments = [
                    s.get("heading", "")
                    for s in page.content_sections[:4]
                    if isinstance(s, dict) and s.get("heading")
                ]
            chapters.append(
                {
                    "number": i + 1,
                    "title": (
                        page.title.split("|")[0].split(" - ")[0].strip()[:50]
                        if page.title
                        else page.page_type.replace("_", " ").title() or f"Page {i + 1}"
                    ),
                    "subtitle": page.meta_description[:80] if page.meta_description else "",
                    "pages": [page.url],
                    "narrative_arc": (
                        page.meta_description or site.description or f"Exploring {name}"
                    ),
                    "key_moments": key_moments,
                    "estimated_duration_seconds": ch_duration,
                }
            )
        if not chapters:
            chapters = [
                {
                    "number": 1,
                    "title": f"{name} Overview",
                    "subtitle": "",
                    "pages": [],
                    "narrative_arc": site.description or "Website tour",
                    "key_moments": [],
                    "estimated_duration_seconds": max(available_seconds, 30.0),
                }
            ]

        subtitle = site.description or "See it in action"
        if len(subtitle) > 60:
            subtitle = subtitle[:57] + "..."

        return {
            # Fail-open visibility: this is deterministic placeholder content
            # produced when LLM generation failed or returned unparseable output.
            # The job is still persisted as status="completed", so this marker lets
            # callers distinguish a real storyline from a degraded fallback. Response
            # models ignore unknown keys (pydantic extra="ignore"), so it survives in
            # the stored JSON without changing the typed API contract.
            "degraded": True,
            "video_title": f"{name} — Product Tour",
            "video_description": f"A guided tour of {name}",
            "target_duration_minutes": duration_minutes,
            "tone": site.tone or "professional",
            "intro": {
                "title": name,
                "subtitle": subtitle,
                "duration_seconds": 8.0,
                "narrative_text": (
                    f"Welcome to {name}. "
                    f"{(site.description or 'Let us show you what it can do.')[:120]}"
                ),
            },
            "chapters": chapters,
            "outro": {
                "title": "Get Started",
                "subtitle": "Take the next step",
                "duration_seconds": 6.0,
                "narrative_text": (
                    f"That wraps up our tour of {name}. "
                    "Visit the website to learn more and get started."
                ),
            },
        }

    @staticmethod
    def _build_context_section(user_context: UserContext) -> str:
        """Build a user direction section from UserContext."""
        parts: list[str] = []
        if user_context.goals:
            parts.append("**Goals:** " + "; ".join(user_context.goals))
        if user_context.target_audience:
            parts.append(f"**Target Audience:** {user_context.target_audience}")
        if user_context.site_type:
            parts.append(f"**Site Type:** {user_context.site_type}")
        if user_context.narration_guidance.tone:
            parts.append(f"**Tone:** {user_context.narration_guidance.tone}")
        if not parts:
            return ""
        return "## User Direction\n" + "\n".join(parts)

    @staticmethod
    def _auto_detect_duration(page_count: int) -> float:
        """Dynamic duration when not specified (duration=0)."""
        if page_count <= 5:
            return 2.0
        if page_count <= 15:
            return 3.0
        return min(5.0, 1.5 + page_count * 0.2)

    def _validate_chapter_urls(self, storyline: dict, analysis: AnalysisInput) -> dict:
        """Drop hallucinated page references from chapters."""
        crawled_ids = set()
        for i, p in enumerate(analysis.pages):
            pid = p.page_id or f"page-{i + 1:03d}"
            crawled_ids.add(pid)
        crawled_urls = {p.url for p in analysis.pages}

        for chapter in storyline.get("chapters", []):
            valid_pages = []
            for page in chapter.get("pages", []):
                page_str = str(page)
                if page_str in crawled_ids or page_str in crawled_urls:
                    valid_pages.append(page_str)
                else:
                    logger.warning(
                        "Chapter %s '%s': dropping unrecognised page %r",
                        chapter.get("number", "?"),
                        chapter.get("title", ""),
                        page_str,
                    )
            chapter["pages"] = valid_pages
        return storyline

    @staticmethod
    def _resolve_chapter_weight(
        chapter: dict, user_context: UserContext, weight_map: dict[str, float]
    ) -> tuple[float, str]:
        searchable = " ".join(
            [
                chapter.get("title", "").lower(),
                chapter.get("narrative_arc", "").lower(),
                " ".join(str(p).lower() for p in chapter.get("pages", [])),
            ]
        )
        best_weight = 1.0
        best_priority = "medium"
        for fp in user_context.feature_priorities:
            if fp.feature.lower() not in searchable:
                continue
            w = weight_map.get(fp.priority, 1.0)
            if fp.priority == "skip":
                return 0.0, "skip"
            if (fp.priority == "high" and best_priority != "high") or (
                w != 1.0 and best_priority not in ("high", "skip")
            ):
                best_weight = w
                best_priority = fp.priority
        return best_weight, best_priority

    def _apply_feature_weights(self, storyline: dict, user_context: UserContext) -> dict:
        """Adjust chapter durations based on feature priorities."""
        weight_map = {"high": 1.4, "medium": 1.0, "low": 0.6, "skip": 0.0}
        high_min_duration = self.settings.storyline.high_priority_min_duration

        kept_chapters: list[dict] = []
        for chapter in storyline.get("chapters", []):
            best_weight, best_priority = self._resolve_chapter_weight(
                chapter, user_context, weight_map
            )
            if best_weight == 0.0:
                logger.info(
                    "Skipping chapter %s '%s' (skip-priority feature)",
                    chapter.get("number", "?"),
                    chapter.get("title", ""),
                )
                continue
            if best_weight != 1.0:
                original = float(chapter.get("estimated_duration_seconds", 60))
                chapter["estimated_duration_seconds"] = round(original * best_weight, 1)
            if (
                best_priority == "high"
                and float(chapter.get("estimated_duration_seconds", 0)) < high_min_duration
            ):
                chapter["estimated_duration_seconds"] = high_min_duration
            kept_chapters.append(chapter)

        for i, ch in enumerate(kept_chapters):
            ch["number"] = i + 1
        storyline["chapters"] = kept_chapters
        return storyline
