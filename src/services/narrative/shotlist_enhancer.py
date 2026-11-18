"""AI-powered creative enhancement of shot list actions via the WM LLM gateway.

w2v services NEVER call openai/anthropic/inference-router directly — every
LLM call goes through ``worker_management.POST /workers/{name}/message``.
See memory ``wm-as-sole-llm-gateway.md`` for the canonical pattern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx
import jinja2
from w2v_common.clients import WMClient

from src.api.narrative_schemas import AnalysisInput, LLMConfigOverride
from src.config import Settings, get_settings
from src.services.narrative.narration_builder import (
    _accumulate_usage,
    _token_accumulator,
)

logger = logging.getLogger(__name__)

_SHOTLIST_WORKER_NAME = "shotlist-enhancer"

_SHOTLIST_SYSTEM_PROMPT = (
    "You are a creative director enhancing a shot list for a demo video. "
    "Given current actions for parts in a chapter, return more vivid, "
    "specific, on-tone actions as a JSON object. Output ONLY the JSON object "
    "— no prose, no markdown fences."
)


def _safe_int(val: object) -> int:
    """Coerce *val* to ``int``, returning ``0`` for non-numeric types."""
    if isinstance(val, int):
        return val
    if isinstance(val, (float, str)):
        try:
            return int(val)
        except TypeError, ValueError:
            return 0
    try:
        return int(str(val))
    except TypeError, ValueError:
        return 0


def _as_list(val: object) -> list:
    """Return *val* if it is a list, else ``[]``.

    Shot-list parts come from upstream JSON and may carry a malformed
    non-iterable value (e.g. an ``int``) where a list is expected. Coercing to
    ``[]`` degrades that part instead of raising ``TypeError`` and 500ing the
    whole enhance request (these accesses are OUTSIDE the chapter-level
    ``except`` that catches WM failures).
    """
    return val if isinstance(val, list) else []


def _as_str_list(val: object) -> list[str]:
    """Return the elements of *val* as strings, or ``[]`` if it isn't a list.

    Guards ``", ".join(...)``: a non-list ``key_moments`` (dict/int) or a list
    with non-str elements would otherwise raise ``TypeError`` inside ``join``.
    """
    if not isinstance(val, list):
        return []
    return [item if isinstance(item, str) else str(item) for item in val]


def _extract_usage_from_metrics(metrics: dict[str, object] | None) -> dict[str, int]:
    """Extract token usage from a WM ``usage_metrics`` dict."""
    if not metrics:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return {
        "prompt_tokens": _safe_int(metrics.get("prompt_tokens", 0)),
        "completion_tokens": _safe_int(metrics.get("completion_tokens", 0)),
        "total_tokens": _safe_int(metrics.get("total_tokens", 0)),
    }


def _parse_json_from_response(raw: str) -> dict | list | None:
    """Extract JSON from LLM response, handling markdown fences."""
    text = raw.strip()
    text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
    text = re.sub(r"\n?```$", "", text)
    text = text.strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            start = text.find(start_char)
            end = text.rfind(end_char)
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start : end + 1])
                except json.JSONDecodeError:
                    continue
    return None


class ShotListEnhancer:
    """Enhance shot list actions with AI-generated creative descriptions via WM."""

    def __init__(
        self,
        settings: Settings | None = None,
        mode: str = "webapp_commercial",
        wm_client: WMClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.timeout_seconds = self.settings.shotlist.timeout_seconds
        self._wm = (
            wm_client if wm_client is not None else WMClient(timeout=float(self.timeout_seconds))
        )

        templates_dir = Path(__file__).parent.parent.parent / "templates" / "shotlist"
        template_path = templates_dir / f"enhance_{mode}.md"
        if not template_path.exists():
            template_path = templates_dir / "enhance_actions.md"

        # Token totals are NOT stored as instance state — see
        # ``_token_accumulator`` ContextVar wired up in ``enhance()`` so
        # concurrent calls against this shared instance get isolated counts.

        if template_path.exists():
            self._template_source = template_path.read_text()
            undefined_cls = (
                jinja2.StrictUndefined if not self.settings.is_production() else jinja2.Undefined
            )
            self._jinja_template = jinja2.Template(self._template_source, undefined=undefined_cls)
        else:
            self._template_source = ""
            self._jinja_template = None
            logger.warning("Enhancement template not found at %s", template_path)

    async def enhance(
        self,
        shot_list: dict,
        storyline: dict,
        analysis: AnalysisInput,
        llm_config: LLMConfigOverride | None = None,
    ) -> dict:
        """Enhance actions for each chapter in the shot list.

        Returns the shot list dict with actions replaced by AI-generated ones.
        On any AI failure, original actions are preserved for that chapter.
        """
        if not self._jinja_template:
            logger.warning("No enhancement template; returning original")
            return shot_list

        local_tokens: dict[str, int] = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        }
        ctx_token = _token_accumulator.set(local_tokens)
        try:
            return await self._enhance_inner(
                shot_list, storyline, analysis, llm_config, local_tokens
            )
        finally:
            _token_accumulator.reset(ctx_token)

    async def _enhance_inner(
        self,
        shot_list: dict,
        storyline: dict,
        analysis: AnalysisInput,
        llm_config: LLMConfigOverride | None,
        local_tokens: dict[str, int],
    ) -> dict:
        t0 = time.monotonic()
        logger.info("ShotListEnhancer.enhance: starting enhancement")
        page_lookup = self._build_page_lookup(analysis)

        # ``shot_list``/``storyline`` are caller-supplied raw dicts (unvalidated),
        # so tolerate malformed elements instead of 500-ing: skip a non-dict part,
        # a part whose ``chapter`` is unhashable (list/dict), and a non-dict
        # storyline chapter — valid elements are still processed.
        chapters: dict[object, list[dict]] = {}
        parts_in = shot_list.get("parts", [])
        for part in parts_in if isinstance(parts_in, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("part_type") == "title_card":
                continue
            ch = part.get("chapter")
            if ch is None:
                continue
            try:
                chapters.setdefault(ch, []).append(part)
            except TypeError:
                # Unhashable chapter key (e.g. a list/dict) — skip this part.
                logger.warning("Skipping shot-list part with unhashable chapter %r", ch)

        chapters_in = storyline.get("chapters", [])
        storyline_chapters = {
            (ch.get("number", i + 1)): ch
            for i, ch in enumerate(chapters_in if isinstance(chapters_in, list) else [])
            if isinstance(ch, dict)
        }

        for ch_num, parts in chapters.items():
            story_ch = storyline_chapters.get(ch_num)
            if not story_ch:
                continue
            try:
                await self._enhance_chapter(
                    parts, story_ch, storyline, page_lookup, llm_config=llm_config
                )
            except (OSError, TimeoutError, ValueError, RuntimeError, httpx.HTTPError) as exc:
                logger.warning(
                    "AI enhancement failed for chapter %d, keeping original: %s",
                    ch_num,
                    exc,
                )

        elapsed = time.monotonic() - t0
        logger.info(
            "ShotListEnhancer.enhance: completed in %.2fs, %d chapters processed",
            elapsed,
            len(chapters),
        )
        if local_tokens["total_tokens"] > 0:
            shot_list["token_usage"] = dict(local_tokens)
        return shot_list

    @staticmethod
    def _build_page_lookup(analysis: AnalysisInput) -> dict:
        """Map both ``page_id`` AND ``url`` to each page.

        Shot list parts reference pages by URL (see ``_enhance_chapter``), while
        ``page_id`` is a separate identifier. Keying by both makes the per-page
        detail lookup resolve — it was previously keyed by ``page_id`` only, so the
        URL lookup always missed and all page detail was silently dropped.
        """
        page_lookup: dict = {}
        for p in analysis.pages:
            if p.page_id:
                page_lookup[p.page_id] = p
            if p.url:
                page_lookup[p.url] = p
        return page_lookup

    async def _enhance_chapter(
        self,
        parts: list[dict],
        story_chapter: dict,
        storyline: dict,
        page_lookup: dict,
        llm_config: LLMConfigOverride | None = None,
    ) -> None:
        """Enhance actions for all parts in a single chapter."""
        if self._jinja_template is None:
            return

        pages_detail_lines: list[str] = []
        current_actions_lines: list[str] = []

        for i, part in enumerate(parts):
            part_id = part.get("id", "")
            current_actions_lines.append(f"Part {i + 1} [id: {part_id}] ({part.get('title', '')}):")
            for action in _as_list(part.get("actions")):
                current_actions_lines.append(f"  - {action}")

            for page_url in _as_list(part.get("pages")):
                page = page_lookup.get(page_url)
                if page:
                    pages_detail_lines.append(f"- {page.title} ({page.page_type}): {page.url}")

        prompt = self._jinja_template.render(
            chapter_title=story_chapter.get("title", ""),
            narrative_arc=story_chapter.get("narrative_arc", ""),
            key_moments=", ".join(_as_str_list(story_chapter.get("key_moments"))) or "N/A",
            tone=storyline.get("tone", ""),
            pages_detail="\n".join(pages_detail_lines) if pages_detail_lines else "N/A",
            current_actions="\n".join(current_actions_lines),
            num_parts=len(parts),
        )

        model_override = llm_config.model if (llm_config and llm_config.model) else None

        llm_t0 = time.monotonic()
        logger.info(
            "WM enhance call for chapter '%s'",
            story_chapter.get("title", ""),
        )
        wm_response = await asyncio.wait_for(
            self._wm.message(
                worker_name=_SHOTLIST_WORKER_NAME,
                message=prompt,
                system_prompt=_SHOTLIST_SYSTEM_PROMPT,
                model_override=model_override,
            ),
            timeout=self.timeout_seconds,
        )
        usage = _extract_usage_from_metrics(wm_response.usage_metrics)
        _accumulate_usage(usage)
        logger.info(
            "WM enhance call completed in %.2fs",
            time.monotonic() - llm_t0,
        )
        output = wm_response.output
        if isinstance(output, dict):
            # Provider obeyed an output_schema (none was set) and returned a dict.
            # Apply the structured response directly.
            self._apply_enhanced_actions(parts, output)
            return
        raw = str(output) if output is not None else ""
        if not raw or not raw.strip():
            return

        parsed = _parse_json_from_response(raw)
        if not parsed:
            logger.warning(
                "Failed to parse AI response for chapter '%s'",
                story_chapter.get("title", ""),
            )
            return

        self._apply_enhanced_actions(parts, parsed)

    @staticmethod
    def _build_part_key_index(
        parts: list[dict],
    ) -> tuple[dict[str, dict], dict[str, dict | None]]:
        """Index parts by their stable ``id`` and by UNIQUE ``title``.

        Non-unique titles map to ``None`` so an ambiguous title can never
        resolve to a single part (and is therefore treated as no match).
        """
        by_id: dict[str, dict] = {}
        by_title: dict[str, dict | None] = {}
        title_counts: dict[str, int] = {}
        for part in parts:
            pid = part.get("id")
            if isinstance(pid, str) and pid:
                by_id[pid] = part
            title = part.get("title")
            if isinstance(title, str) and title:
                title_counts[title] = title_counts.get(title, 0) + 1
                by_title[title] = part
        for title, count in title_counts.items():
            if count > 1:
                by_title[title] = None
        return by_id, by_title

    @staticmethod
    def _match_part(
        ai_part: dict[str, object],
        by_id: dict[str, dict],
        by_title: dict[str, dict | None],
    ) -> dict | None:
        """Resolve an AI part to its ORIGINAL part by echoed ``id`` then ``title``.

        Returns ``None`` when neither key resolves to exactly one original part,
        so the caller keeps that part's original actions instead of guessing.
        """
        key_id = ai_part.get("id")
        if isinstance(key_id, str) and key_id in by_id:
            return by_id[key_id]
        key_title = ai_part.get("title")
        if isinstance(key_title, str):
            matched = by_title.get(key_title)
            if matched is not None:
                return matched
        return None

    @staticmethod
    def _apply_parts_field(parts: list[dict], ai_parts: object) -> None:
        """Apply enhanced actions, aligning each AI part to its ORIGINAL by KEY.

        The LLM is instructed to echo each part's ``id`` (falling back to its
        ``title``). Actions are matched to the original part by that stable key
        — never by list position — so a reordered / omitted / added AI part can
        no longer land its enhanced actions on the WRONG shot (the @clip
        misalignment class). Any AI part whose key does not resolve to exactly
        one still-unclaimed original part is skipped, leaving that part's
        original actions intact.
        """
        if not isinstance(ai_parts, list):
            return
        by_id, by_title = ShotListEnhancer._build_part_key_index(parts)
        consumed: set[int] = set()
        for ai_part in ai_parts:
            if not isinstance(ai_part, dict):
                continue
            ai_part_typed = cast(dict[str, object], ai_part)
            new_actions = ai_part_typed.get("actions")
            if not (isinstance(new_actions, list) and new_actions):
                continue
            target = ShotListEnhancer._match_part(ai_part_typed, by_id, by_title)
            # Skip unmatched keys AND duplicate keys (target already claimed):
            # never silently shift actions onto the wrong part.
            if target is None or id(target) in consumed:
                continue
            consumed.add(id(target))
            ShotListEnhancer._replace_actions(target, new_actions)

    @staticmethod
    def _apply_global_actions(parts: list[dict], new_actions: object) -> None:
        """Apply a flat ``{"actions": [...]}`` response.

        A bare action list carries no per-part structure, so it can only be
        attributed unambiguously when the chapter has exactly ONE part. For a
        multi-part chapter, broadcasting the same actions to every part would
        collapse per-part distinction and put one part's actions on all shots,
        so we treat it as unparseable and keep the originals instead.
        """
        if len(parts) != 1:
            return
        if isinstance(new_actions, list) and new_actions:
            ShotListEnhancer._replace_actions(parts[0], new_actions)

    @staticmethod
    def _apply_enhanced_actions(parts: list[dict], parsed: Mapping[str, object] | list) -> None:
        """Apply parsed AI actions to parts, preserving navigation actions."""
        if isinstance(parsed, list):
            # A top-level array is either a list of per-part OBJECTS (align each
            # to its original by echoed id/title, exactly like {"parts": [...]})
            # or a flat list of action STRINGS (broadcast only to a single-part
            # chapter, like {"actions": [...]}). The old code did str(a) on every
            # item and slammed it onto parts[0]: a list of part dicts became
            # "{'part_id': ...}" garbage actions on the wrong (and only) shot,
            # bypassing both the key-matching and the single-part guard.
            if any(isinstance(item, dict) for item in parsed):
                ShotListEnhancer._apply_parts_field(parts, parsed)
            else:
                ShotListEnhancer._apply_global_actions(parts, parsed)
            return
        if "parts" in parsed:
            ShotListEnhancer._apply_parts_field(parts, parsed["parts"])
        elif "actions" in parsed:
            ShotListEnhancer._apply_global_actions(parts, parsed["actions"])

    @staticmethod
    def _replace_actions(part: dict, new_actions: list) -> None:
        """Replace a part's actions, preserving the first navigation action."""
        str_actions = [str(a) for a in new_actions if a]
        if not str_actions:
            return

        original_nav = ""
        for action in part.get("actions", []):
            if str(action).startswith("Navigate to"):
                original_nav = str(action)
                break

        if original_nav and not any(a.startswith("Navigate to") for a in str_actions):
            str_actions.insert(0, original_nav)

        part["actions"] = str_actions
