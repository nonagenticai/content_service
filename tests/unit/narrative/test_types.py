"""Tests for rich domain types."""

from src.api.narrative_types import (
    FeaturePriority,
    NarrationGuidance,
    NarrationPart,
    NarrationScript,
    Storyline,
    StorylineChapter,
    StorylineIntro,
    StorylineOutro,
    UserContext,
    VisualPreferences,
)


class TestStorylineTypes:
    def test_storyline_defaults(self) -> None:
        s = Storyline()
        assert s.video_title == ""
        assert s.target_duration_minutes == 5.0
        assert s.chapters == []
        assert s.intro.duration_seconds == 8.0
        assert s.outro.duration_seconds == 6.0

    def test_storyline_with_chapters(self) -> None:
        s = Storyline(
            video_title="Demo",
            chapters=[
                StorylineChapter(number=1, title="Ch1", pages=["p1"]),
                StorylineChapter(number=2, title="Ch2"),
            ],
        )
        assert len(s.chapters) == 2
        assert s.chapters[0].pages == ["p1"]

    def test_storyline_serialization(self) -> None:
        s = Storyline(
            video_title="Test",
            intro=StorylineIntro(title="Welcome"),
            chapters=[StorylineChapter(number=1, title="Ch1")],
            outro=StorylineOutro(title="Bye"),
        )
        data = s.model_dump()
        assert data["video_title"] == "Test"
        assert data["intro"]["title"] == "Welcome"
        assert len(data["chapters"]) == 1

        restored = Storyline(**data)
        assert restored.video_title == "Test"
        assert restored.chapters[0].title == "Ch1"


class TestNarrationTypes:
    def test_narration_part(self) -> None:
        p = NarrationPart(part_id="intro", text="Hello", word_count=1)
        assert p.estimated_duration_seconds == 0.0

    def test_narration_script_sync_totals(self) -> None:
        script = NarrationScript(
            parts=[
                NarrationPart(
                    part_id="p1", text="a b c", word_count=3, estimated_duration_seconds=1.2
                ),
                NarrationPart(
                    part_id="p2", text="d e", word_count=2, estimated_duration_seconds=0.8
                ),
            ],
            total_words=0,
            estimated_total_duration_seconds=0.0,
        )
        assert script.total_words == 5
        assert abs(script.estimated_total_duration_seconds - 2.0) < 0.01

    def test_narration_script_already_correct(self) -> None:
        script = NarrationScript(
            parts=[
                NarrationPart(part_id="p1", word_count=10, estimated_duration_seconds=4.0),
            ],
            total_words=10,
            estimated_total_duration_seconds=4.0,
        )
        assert script.total_words == 10
        assert script.estimated_total_duration_seconds == 4.0

    def test_narration_script_serialization(self) -> None:
        script = NarrationScript(
            parts=[
                NarrationPart(
                    part_id="intro", text="Hi", word_count=1, estimated_duration_seconds=0.4
                )
            ],
            tone="casual",
        )
        data = script.model_dump()
        restored = NarrationScript(**data)
        assert restored.tone == "casual"
        assert len(restored.parts) == 1


class TestUserContext:
    def test_defaults(self) -> None:
        ctx = UserContext()
        assert ctx.goals == []
        assert ctx.target_audience == ""
        assert ctx.feature_priorities == []
        assert ctx.narration_guidance.tone == ""
        assert ctx.visual_preferences.style == ""

    def test_with_data(self) -> None:
        ctx = UserContext(
            goals=["increase signups", "show features"],
            target_audience="developers",
            site_type="SaaS",
            feature_priorities=[
                FeaturePriority(feature="pricing", priority="high"),
                FeaturePriority(feature="blog", priority="skip"),
            ],
            exclude_pages=["/admin", "/login"],
            narration_guidance=NarrationGuidance(tone="casual", key_messages=["fast", "reliable"]),
            visual_preferences=VisualPreferences(style="modern"),
        )
        assert len(ctx.feature_priorities) == 2
        assert ctx.feature_priorities[0].priority == "high"
        assert ctx.narration_guidance.key_messages == ["fast", "reliable"]
        assert ctx.exclude_pages == ["/admin", "/login"]

    def test_serialization_roundtrip(self) -> None:
        ctx = UserContext(
            goals=["demo"],
            narration_guidance=NarrationGuidance(avoid_topics=["competitors"]),
        )
        data = ctx.model_dump()
        restored = UserContext(**data)
        assert restored.goals == ["demo"]
        assert restored.narration_guidance.avoid_topics == ["competitors"]

    def test_feature_priority_default(self) -> None:
        fp = FeaturePriority(feature="pricing")
        assert fp.priority == "medium"
