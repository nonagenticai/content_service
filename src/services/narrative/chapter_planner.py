"""Plan, validate, and rebalance chapter structure for video storylines."""

from __future__ import annotations

import copy
import logging

logger = logging.getLogger(__name__)

MIN_CHAPTER_DURATION_S = 15.0


class ChapterPlanner:
    """Refine and validate chapter structure."""

    def __init__(
        self,
        duration_overage_threshold: float = 1.5,
        min_chapter_duration_seconds: float = MIN_CHAPTER_DURATION_S,
    ) -> None:
        self.duration_overage_threshold = duration_overage_threshold
        self.min_chapter_duration_seconds = min_chapter_duration_seconds

    def validate(self, storyline: dict) -> list[str]:
        """Validate storyline structure. Returns list of warnings."""
        warnings: list[str] = []

        chapters = storyline.get("chapters", [])
        if not chapters:
            warnings.append("No chapters defined")
        if len(chapters) > 10:
            warnings.append(f"Too many chapters ({len(chapters)}), consider consolidating")

        intro = storyline.get("intro", {})
        outro = storyline.get("outro", {})
        total_duration = (
            float(intro.get("duration_seconds", 8))
            + sum(float(ch.get("estimated_duration_seconds", 60)) for ch in chapters)
            + float(outro.get("duration_seconds", 6))
        )

        target_minutes = float(storyline.get("target_duration_minutes", 3.0))
        if total_duration > target_minutes * 60 * self.duration_overage_threshold:
            warnings.append(
                f"Estimated duration ({total_duration:.0f}s) exceeds target "
                f"({target_minutes * 60:.0f}s) by "
                f">{int((self.duration_overage_threshold - 1) * 100)}%"
            )

        for ch in chapters:
            if not ch.get("pages"):
                warnings.append(
                    f"Chapter {ch.get('number', '?')} '{ch.get('title', '')}' has no pages assigned"
                )

        return warnings

    def rebalance(self, storyline: dict) -> dict:
        """Rebalance chapter durations to fit target.

        Creates a deep copy so the original is not mutated. Applies a
        proportional ratio so total chapter time fits the target duration
        minus intro/outro time. Enforces MIN_CHAPTER_DURATION_S floor.
        """
        result = copy.deepcopy(storyline)

        target_s = float(result.get("target_duration_minutes", 3.0)) * 60
        intro = result.get("intro", {})
        outro = result.get("outro", {})
        fixed_s = float(intro.get("duration_seconds", 8)) + float(outro.get("duration_seconds", 6))
        available_s = max(0, target_s - fixed_s)

        chapters = result.get("chapters", [])
        if not chapters:
            return result

        total_chapter_s = sum(float(ch.get("estimated_duration_seconds", 60)) for ch in chapters)
        if total_chapter_s <= 0:
            return result

        ratio = available_s / max(1.0, total_chapter_s)
        for ch in chapters:
            original = float(ch.get("estimated_duration_seconds", 60))
            scaled = round(original * ratio, 1)
            if scaled < self.min_chapter_duration_seconds:
                logger.info(
                    "Chapter %s '%s' duration %.1fs below minimum; raising to %.1fs",
                    ch.get("number", "?"),
                    ch.get("title", ""),
                    scaled,
                    self.min_chapter_duration_seconds,
                )
                scaled = self.min_chapter_duration_seconds
            ch["estimated_duration_seconds"] = scaled

        return result
