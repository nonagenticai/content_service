"""Tests for chapter planner validation and rebalancing logic."""

from __future__ import annotations

from src.services.narrative.chapter_planner import MIN_CHAPTER_DURATION_S, ChapterPlanner


class TestChapterPlannerValidate:
    """Test validate() warnings for various storyline shapes."""

    def setup_method(self) -> None:
        self.planner = ChapterPlanner()

    def test_no_chapters_warning(self) -> None:
        """Warns when no chapters are defined."""
        warnings = self.planner.validate({"chapters": []})
        assert any("No chapters defined" in w for w in warnings)

    def test_too_many_chapters_warning(self) -> None:
        """Warns when more than 10 chapters are defined."""
        chapters = [
            {"number": i, "title": f"Ch{i}", "pages": [f"p{i}"], "estimated_duration_seconds": 20}
            for i in range(1, 13)
        ]
        warnings = self.planner.validate(
            {
                "target_duration_minutes": 10.0,
                "intro": {"duration_seconds": 5},
                "outro": {"duration_seconds": 5},
                "chapters": chapters,
            }
        )
        assert any("Too many chapters" in w for w in warnings)

    def test_valid_storyline_no_warnings(self) -> None:
        """A well-structured storyline produces no warnings."""
        storyline = {
            "target_duration_minutes": 3.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "Overview",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 60,
                },
                {
                    "number": 2,
                    "title": "Features",
                    "pages": ["https://example.com/features"],
                    "estimated_duration_seconds": 60,
                },
            ],
        }
        warnings = self.planner.validate(storyline)
        assert warnings == []

    def test_duration_overage_warning(self) -> None:
        """Warns when estimated duration exceeds target by threshold."""
        storyline = {
            "target_duration_minutes": 1.0,  # 60s target
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "Long Chapter",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 300,  # 300s >> 60s * 1.5
                },
            ],
        }
        warnings = self.planner.validate(storyline)
        assert any("exceeds target" in w for w in warnings)

    def test_chapter_with_no_pages_warning(self) -> None:
        """Warns when a chapter has no pages assigned."""
        storyline = {
            "target_duration_minutes": 3.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "Empty",
                    "pages": [],
                    "estimated_duration_seconds": 60,
                },
            ],
        }
        warnings = self.planner.validate(storyline)
        assert any("has no pages" in w for w in warnings)

    def test_custom_overage_threshold(self) -> None:
        """A stricter threshold catches smaller overages."""
        planner = ChapterPlanner(duration_overage_threshold=1.1)
        storyline = {
            "target_duration_minutes": 1.0,  # 60s
            "intro": {"duration_seconds": 5},
            "outro": {"duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Ch1",
                    "pages": ["p1"],
                    "estimated_duration_seconds": 70,  # total=80s > 60*1.1=66s
                },
            ],
        }
        warnings = planner.validate(storyline)
        assert any("exceeds target" in w for w in warnings)

    def test_defaults_used_for_missing_fields(self) -> None:
        """Validation uses defaults when intro/outro/target are absent."""
        storyline = {
            "chapters": [
                {
                    "number": 1,
                    "title": "Only chapter",
                    "pages": ["p1"],
                    "estimated_duration_seconds": 60,
                },
            ],
        }
        # Should not raise; uses defaults
        warnings = self.planner.validate(storyline)
        assert isinstance(warnings, list)


class TestChapterPlannerRebalance:
    """Test rebalance() proportional duration scaling."""

    def setup_method(self) -> None:
        self.planner = ChapterPlanner()

    def test_rebalance_scales_proportionally(self) -> None:
        """Chapters are scaled to fit available time after intro/outro."""
        storyline = {
            "target_duration_minutes": 2.0,  # 120s total
            "intro": {"duration_seconds": 10},
            "outro": {"duration_seconds": 10},
            # Available: 120 - 20 = 100s for chapters
            "chapters": [
                {"number": 1, "title": "A", "estimated_duration_seconds": 100},
                {"number": 2, "title": "B", "estimated_duration_seconds": 100},
            ],
        }
        result = self.planner.rebalance(storyline)
        ch1_dur = result["chapters"][0]["estimated_duration_seconds"]
        ch2_dur = result["chapters"][1]["estimated_duration_seconds"]
        # Each chapter should be roughly 50s (100s available / 200s original * 100)
        assert ch1_dur == ch2_dur
        assert abs(ch1_dur - 50.0) < 1.0

    def test_rebalance_does_not_mutate_original(self) -> None:
        """rebalance returns a deep copy; original dict is unchanged."""
        storyline = {
            "target_duration_minutes": 1.0,
            "intro": {"duration_seconds": 5},
            "outro": {"duration_seconds": 5},
            "chapters": [
                {"number": 1, "title": "Ch1", "estimated_duration_seconds": 200},
            ],
        }
        original_duration = storyline["chapters"][0]["estimated_duration_seconds"]
        self.planner.rebalance(storyline)
        assert storyline["chapters"][0]["estimated_duration_seconds"] == original_duration

    def test_rebalance_enforces_minimum_duration(self) -> None:
        """Chapters are not scaled below MIN_CHAPTER_DURATION_S."""
        storyline = {
            "target_duration_minutes": 0.5,  # 30s total
            "intro": {"duration_seconds": 10},
            "outro": {"duration_seconds": 10},
            # Available: 10s for chapters, each would scale to 5s < 15s min
            "chapters": [
                {"number": 1, "title": "A", "estimated_duration_seconds": 50},
                {"number": 2, "title": "B", "estimated_duration_seconds": 50},
            ],
        }
        result = self.planner.rebalance(storyline)
        for ch in result["chapters"]:
            assert ch["estimated_duration_seconds"] >= MIN_CHAPTER_DURATION_S

    def test_rebalance_no_chapters_returns_unchanged(self) -> None:
        """rebalance with no chapters returns the storyline as-is."""
        storyline = {
            "target_duration_minutes": 3.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [],
        }
        result = self.planner.rebalance(storyline)
        assert result["chapters"] == []

    def test_rebalance_zero_total_chapter_duration(self) -> None:
        """Handles chapters with zero estimated_duration_seconds gracefully."""
        storyline = {
            "target_duration_minutes": 3.0,
            "intro": {"duration_seconds": 8},
            "outro": {"duration_seconds": 6},
            "chapters": [
                {"number": 1, "title": "Zero", "estimated_duration_seconds": 0},
            ],
        }
        result = self.planner.rebalance(storyline)
        # total_chapter_s is 0, should return early
        assert result["chapters"][0]["estimated_duration_seconds"] == 0

    def test_rebalance_defaults_when_missing_fields(self) -> None:
        """Uses defaults for missing target_duration, intro, outro."""
        storyline = {
            "chapters": [
                {"number": 1, "title": "Ch1", "estimated_duration_seconds": 120},
            ],
        }
        result = self.planner.rebalance(storyline)
        # Default: 3min=180s, intro=8s, outro=6s => available=166s
        # ratio = 166 / 120 = 1.383
        # scaled = 120 * 1.383 = 166.0
        assert result["chapters"][0]["estimated_duration_seconds"] == 166.0


def test_chapter_plan_request_rejects_non_positive_bounds() -> None:
    """duration_overage_threshold / target_duration_minutes are verdict knobs; a
    non-positive value inverts the overage warning, so the schema must 422 it."""
    import pytest
    from pydantic import ValidationError

    from src.api.narrative_schemas import ChapterPlanRequest

    with pytest.raises(ValidationError):
        ChapterPlanRequest(storyline={}, duration_overage_threshold=0)
    with pytest.raises(ValidationError):
        ChapterPlanRequest(storyline={}, duration_overage_threshold=-1.5)
    with pytest.raises(ValidationError):
        ChapterPlanRequest(storyline={}, target_duration_minutes=0)
    # A valid request still constructs.
    ok = ChapterPlanRequest(storyline={}, duration_overage_threshold=1.5)
    assert ok.duration_overage_threshold == 1.5
