"""The ``degraded`` marker must survive the API response models.

Producers of deterministic boilerplate set ``degraded: True`` on the dict they
emit, and the job is still persisted as ``status="completed"``. If the response
model does not DECLARE the field, pydantic silently strips it and the caller
cannot tell a real artifact from a fail-open placeholder — the whole point of
the marker. ``NarrationPartResponse`` declared it; ``StorylineResponse`` and
``ShotListPartResponse`` did not.
"""

from __future__ import annotations

from src.api.narrative_schemas import (
    ShotListPartResponse,
    ShotListResponse,
    StorylineResponse,
    StorylineResultResponse,
)


class TestStorylineResponseDegraded:
    def test_degraded_true_survives_the_response_model(self) -> None:
        resp = StorylineResponse(video_title="Acme — Product Tour", degraded=True)
        assert resp.degraded is True
        assert resp.model_dump()["degraded"] is True

    def test_degraded_defaults_false_for_a_real_storyline(self) -> None:
        resp = StorylineResponse(video_title="Acme — Product Tour")
        assert resp.degraded is False

    def test_degraded_survives_the_polling_envelope(self) -> None:
        """The job-poll route is where a caller actually reads this."""
        result = StorylineResultResponse(
            job_id="j1",
            status="completed",
            storyline=StorylineResponse(video_title="T", degraded=True),
        )
        assert result.storyline is not None
        assert result.storyline.degraded is True
        assert result.model_dump()["storyline"]["degraded"] is True


class TestShotListPartResponseDegraded:
    def test_degraded_true_survives_the_response_model(self) -> None:
        part = ShotListPartResponse(id="ch01-part1", part_type="browser_recording", degraded=True)
        assert part.degraded is True

    def test_degraded_survives_the_shotlist_envelope(self) -> None:
        resp = ShotListResponse(
            parts=[
                ShotListPartResponse(id="ch01-part1", part_type="browser_recording", degraded=True)
            ]
        )
        assert resp.model_dump()["parts"][0]["degraded"] is True

    def test_degraded_defaults_false(self) -> None:
        part = ShotListPartResponse(id="ch01-part1", part_type="browser_recording")
        assert part.degraded is False
