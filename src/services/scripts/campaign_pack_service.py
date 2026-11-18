"""Template-driven expansion for campaign packs.

Builds multiple short ad variant parts from one seed part + page analysis.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import ClassVar


class CampaignPackService:
    """Expand one seed analysis into many campaign-oriented part variants."""

    DEFAULT_STYLES = ("webapp_commercial", "commercial", "company_commercial")
    DEFAULT_HOOKS = (
        "Start with the strongest user pain point in one sentence.",
        "Lead with the fastest visible outcome the user gets.",
        "Open by contrasting before vs after in a single line.",
    )
    DEFAULT_CTAS = (
        "Invite viewers to start a free trial now.",
        "Ask viewers to book a short product walkthrough.",
        "Prompt viewers to explore the main feature immediately.",
    )

    STYLE_ALIASES: ClassVar[dict[str, str]] = {
        "webapp": "webapp_commercial",
        "web": "webapp_commercial",
        "webapp_commercial": "webapp_commercial",
        "commercial": "commercial",
        "company": "company_commercial",
        "company_commercial": "company_commercial",
        "tutorial": "tutorial",
    }

    STYLE_ANGLES: ClassVar[dict[str, tuple[str, ...]]] = {
        "webapp_commercial": (
            "Show speed and product clarity.",
            "Show confidence through workflow simplicity.",
            "Show measurable impact after a few clicks.",
        ),
        "commercial": (
            "Focus on emotional payoff and momentum.",
            "Focus on social proof and trust cues.",
            "Focus on a simple transformation arc.",
        ),
        "company_commercial": (
            "Focus on brand credibility and mission.",
            "Focus on team expertise and reliability.",
            "Focus on long-term partnership value.",
        ),
        "tutorial": (
            "Focus on one clear how-to step.",
            "Focus on setup friction reduction.",
            "Focus on fast onboarding confidence.",
        ),
    }

    def build_parts(
        self,
        *,
        seed_part: dict | None,
        page_analysis: dict[str, dict] | None,
        script_styles: list[str],
        variants_per_style: int,
        variant_duration_seconds: float,
        hooks: list[str] | None = None,
        ctas: list[str] | None = None,
    ) -> tuple[list[dict], list[dict[str, object]]]:
        """Create concrete part dicts + variant metadata for a campaign pack."""
        normalized_styles = self._normalize_styles(script_styles)
        seed = self._resolve_seed_part(seed_part, page_analysis, variant_duration_seconds)

        hook_pool = hooks or list(self.DEFAULT_HOOKS)
        cta_pool = ctas or list(self.DEFAULT_CTAS)

        parts: list[dict] = []
        variant_meta: list[dict[str, object]] = []

        serial = 0
        for style in normalized_styles:
            angles = self.STYLE_ANGLES.get(style, self.STYLE_ANGLES["commercial"])
            for variant_index in range(1, variants_per_style + 1):
                serial += 1
                hook = hook_pool[(serial - 1) % len(hook_pool)]
                cta = cta_pool[(serial - 1) % len(cta_pool)]
                angle = angles[(variant_index - 1) % len(angles)]

                part = deepcopy(seed)
                part_id = self._build_part_id(seed["id"], style, variant_index)
                part["id"] = part_id
                part["title"] = self._build_title(
                    seed.get("title", "Campaign Ad"), style, variant_index
                )
                part["estimated_duration_seconds"] = variant_duration_seconds
                part["actions"] = self._build_actions(seed.get("actions", []), hook, angle, cta)
                part["generation_mode"] = style
                part["campaign_variant"] = {
                    "style": style,
                    "variant_index": variant_index,
                    "hook": hook,
                    "angle": angle,
                    "cta": cta,
                }

                parts.append(part)
                variant_meta.append(
                    {
                        "part_id": part_id,
                        "style": style,
                        "variant_index": variant_index,
                        "hook": hook,
                        "cta": cta,
                    }
                )

        return parts, variant_meta

    def _resolve_seed_part(
        self,
        seed_part: dict | None,
        page_analysis: dict[str, dict] | None,
        variant_duration_seconds: float,
    ) -> dict:
        """Build a safe seed part if caller did not provide one."""
        if seed_part:
            seed = deepcopy(seed_part)
            # Campaign-pack variants are always Playwright-recorded scripts.
            # If callers pass title-card seeds, coerce to browser_recording so
            # downstream scaffold + recorder contracts remain valid.
            seed["part_type"] = "browser_recording"
            seed.setdefault("actions", [])
            seed.setdefault("pages", [])
            seed.setdefault("key_elements", [])
            seed.setdefault("estimated_duration_seconds", variant_duration_seconds)
            seed.setdefault("title", "Campaign Ad")
            seed.setdefault("id", "campaign")
            return seed

        page_ids = list((page_analysis or {}).keys())
        first_page = (page_analysis or {}).get(page_ids[0], {}) if page_ids else {}

        title = str(first_page.get("title") or "Campaign Ad")
        return {
            "id": "campaign",
            "part_type": "browser_recording",
            "title": title,
            "chapter": 1,
            "pages": page_ids[:2],
            "actions": [
                "Open on the strongest above-the-fold section.",
                "Highlight one key proof point quickly.",
                "Close with a clear action-oriented CTA.",
            ],
            "key_elements": [],
            "estimated_duration_seconds": variant_duration_seconds,
        }

    def _build_actions(self, base_actions: list[str], hook: str, angle: str, cta: str) -> list[str]:
        """Compose ad-focused action list for one variant."""
        actions = list(base_actions)
        actions.extend(
            [
                f"Variant hook: {hook}",
                f"Narrative angle: {angle}",
                "Keep this variant concise and ad-focused (single value story).",
                f"Ending CTA: {cta}",
            ]
        )
        return actions

    def _normalize_styles(self, styles: list[str]) -> list[str]:
        """Normalize and deduplicate style names while preserving order."""
        resolved: list[str] = []
        seen: set[str] = set()
        for raw in styles or list(self.DEFAULT_STYLES):
            key = str(raw).strip().lower()
            if not key:
                continue
            mode = self.STYLE_ALIASES.get(key, key)
            if mode in seen:
                continue
            seen.add(mode)
            resolved.append(mode)

        return resolved or list(self.DEFAULT_STYLES)

    def _build_part_id(self, seed_id: str, style: str, variant_index: int) -> str:
        """Create deterministic and DB-safe part IDs."""
        seed_slug = self._slug(seed_id) or "campaign"
        style_slug = self._slug(style) or "style"
        part_id = f"{seed_slug}-{style_slug}-v{variant_index}"
        return part_id[:250]

    @staticmethod
    def _build_title(seed_title: str, style: str, variant_index: int) -> str:
        """Create human-readable title for one campaign variant."""
        label = style.replace("_", " ").title()
        return f"{seed_title} [{label} V{variant_index}]"

    @staticmethod
    def _slug(value: str) -> str:
        """Slugify arbitrary value into lowercase identifier tokens."""
        cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower())
        return cleaned.strip("-")
