"""A chapter with no pages must not yield a confident-looking recording plan.

``ShotListBuilder._build_chapter`` emits a stub ``browser_recording`` part when a
chapter declares ``pages: []``. It used to carry the chapter's FULL duration
budget, no ``pages`` key, and no marker, so a recorder could not tell it from a
real plan and simply burned the whole budget on "navigate somewhere".
"""

from __future__ import annotations

import logging

from src.api.narrative_schemas import AnalysisInput, SiteInput
from src.services.narrative.shotlist_builder import ShotListBuilder

_PAGELESS = {
    "intro": {"title": "I", "duration_seconds": 5},
    "chapters": [
        {
            "number": 1,
            "title": "No Pages",
            "pages": [],
            "estimated_duration_seconds": 240.0,
        }
    ],
    "outro": {"title": "O", "duration_seconds": 5},
}


def _browser_part(builder: ShotListBuilder) -> dict:
    result = builder.build(_PAGELESS, AnalysisInput(pages=[], site=SiteInput(description="T")))
    parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
    assert len(parts) == 1
    return parts[0]


class TestPagelessChapterStub:
    def test_stub_is_marked_degraded(self) -> None:
        assert _browser_part(ShotListBuilder())["degraded"] is True

    def test_stub_declares_an_explicit_empty_pages_list(self) -> None:
        """Absent is unmeasured; an explicit `[]` is the expressible gap."""
        part = _browser_part(ShotListBuilder())
        assert "pages" in part
        assert part["pages"] == []

    def test_stub_does_not_consume_the_full_chapter_budget(self) -> None:
        builder = ShotListBuilder()
        part = _browser_part(builder)
        assert part["estimated_duration_seconds"] == builder.min_part_duration
        assert part["estimated_duration_seconds"] < 240.0

    def test_stub_logs_a_warning_naming_the_chapter(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="src.services.narrative.shotlist_builder"):
            _browser_part(ShotListBuilder())
        warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("declared no pages" in m and "No Pages" in m for m in warnings), warnings

    def test_a_real_chapter_is_not_marked_degraded(self) -> None:
        """The healthy case must stay indistinguishable from before."""
        storyline = {
            "intro": {"title": "I", "duration_seconds": 5},
            "chapters": [
                {
                    "number": 1,
                    "title": "Has Pages",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 60.0,
                }
            ],
            "outro": {"title": "O", "duration_seconds": 5},
        }
        result = ShotListBuilder().build(
            storyline, AnalysisInput(pages=[], site=SiteInput(description="T"))
        )
        parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(parts) == 1
        assert parts[0].get("degraded", False) is False
        assert parts[0]["pages"] == ["https://example.com/"]
