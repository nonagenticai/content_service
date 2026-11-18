"""Unit tests for findings_converter service."""

from __future__ import annotations

import pytest

from src.services.narrative.findings_converter import (
    _category_narrative,
    _chapter_to_category,
    _finding_actions,
    _finding_duration,
    _review_type_title,
    _site_name,
    critique_to_shotlist,
    critique_to_storyline,
    reviews_to_shotlist,
    reviews_to_storyline,
)

# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestSiteName:
    def test_short_description(self) -> None:
        assert _site_name({"description": "Acme Corp is great."}) == "Acme Corp is great"

    def test_long_description_truncates(self) -> None:
        long = "A" * 100 + ". Rest of the text."
        assert _site_name({"description": long}) == "Website"

    def test_no_analysis(self) -> None:
        assert _site_name(None) == "Website"

    def test_empty_analysis(self) -> None:
        assert _site_name({}) == "Website"


class TestCategoryNarrative:
    def test_basic(self) -> None:
        findings = [
            {"severity": "medium", "recommendation": "Fix alt text"},
            {"severity": "high", "recommendation": ""},
        ]
        result = _category_narrative("a11y", findings)
        assert "2 opportunities" in result
        assert "Accessibility Opportunities" in result

    def test_high_severity_mentioned(self) -> None:
        findings = [
            {"severity": "critical", "recommendation": "Urgent fix"},
        ]
        result = _category_narrative("seo", findings)
        assert "high-impact" in result

    def test_unknown_category(self) -> None:
        result = _category_narrative("custom", [{"severity": "low"}])
        assert "custom" in result


class TestReviewTypeTitle:
    def test_known_types(self) -> None:
        assert _review_type_title("design_review") == "Design Strengths & Opportunities"
        assert _review_type_title("feature_suggestions") == "Features That Could Set You Apart"

    def test_unknown_type(self) -> None:
        assert _review_type_title("my_custom_review") == "My Custom Review"


class TestFindingActions:
    def test_full_finding(self) -> None:
        finding = {
            "url": "https://example.com/",
            "selector": "#main",
            "title": "Missing ARIA",
            "recommendation": "Add ARIA labels",
        }
        actions = _finding_actions(finding)
        assert len(actions) == 4
        assert "Navigate to https://example.com/" in actions[0]
        assert "highlight" in actions[1].lower()

    def test_minimal_finding(self) -> None:
        finding = {"title": "Generic issue"}
        actions = _finding_actions(finding)
        assert len(actions) == 1


class TestFindingDuration:
    def test_base_duration(self) -> None:
        assert _finding_duration({}) == 15.0

    def test_with_selector(self) -> None:
        assert _finding_duration({"selector": "#main"}) == 20.0

    def test_with_recommendation(self) -> None:
        assert _finding_duration({"recommendation": "Fix this"}) == 18.0

    def test_with_both(self) -> None:
        assert _finding_duration({"selector": "#main", "recommendation": "Fix"}) == 23.0


class TestChapterToCategory:
    def test_accessibility(self) -> None:
        assert _chapter_to_category("Accessibility Opportunities") == "a11y"

    def test_seo(self) -> None:
        assert _chapter_to_category("SEO Growth Areas") == "seo"

    def test_security(self) -> None:
        assert _chapter_to_category("Security Enhancements") == "security"

    def test_performance(self) -> None:
        assert _chapter_to_category("Performance Optimizations") == "performance"

    def test_ux(self) -> None:
        assert _chapter_to_category("User Experience Refinements") == "ux"

    def test_unknown_fallback(self) -> None:
        assert _chapter_to_category("Custom Title") == "custom"


# ---------------------------------------------------------------------------
# critique_to_storyline
# ---------------------------------------------------------------------------


class TestCritiqueToStoryline:
    def test_basic_conversion(self) -> None:
        findings = [
            {
                "category": "seo",
                "severity": "high",
                "title": "Missing meta",
                "url": "https://example.com/",
            },
            {
                "category": "seo",
                "severity": "medium",
                "title": "Slow load",
                "url": "https://example.com/about",
            },
            {
                "category": "a11y",
                "severity": "critical",
                "title": "No alt text",
                "url": "https://example.com/",
            },
        ]
        scores = {"seo": 65.0, "a11y": 40.0}
        result = critique_to_storyline(findings, scores=scores, target_minutes=2.0)

        assert result["video_title"].endswith("Growth Opportunities Report")
        assert result["tone"].startswith("constructive")
        assert len(result["chapters"]) == 2
        # a11y should be first (lowest score = biggest opportunity)
        assert "Accessibility" in result["chapters"][0]["title"]
        assert "SEO" in result["chapters"][1]["title"]

    def test_positive_framing(self) -> None:
        findings = [
            {"category": "security", "severity": "high", "title": "XSS vuln", "url": ""},
        ]
        result = critique_to_storyline(findings)
        assert "Growth Opportunities" in result["video_title"]
        assert "Roadmap to Excellence" in result["outro"]["title"]

    def test_empty_findings_raises_instead_of_shipping_zero_chapters(self) -> None:
        """No findings => no chapters => refuse, do not return a 200-shaped stub.

        This test previously ASSERTED `chapters == []` — it pinned the defect.
        A zero-chapter storyline builds an intro+outro-only shot list, i.e. a
        ~11-second video reported `completed`. The route turns this into a 422.
        """
        with pytest.raises(ValueError, match="zero-chapter storyline"):
            critique_to_storyline([])

    def test_duration_calculation(self) -> None:
        findings = [
            {"category": "ux", "severity": "low", "title": f"Issue {i}", "url": ""}
            for i in range(10)
        ]
        result = critique_to_storyline(findings, target_minutes=3.0)
        total_ch_dur = sum(ch["estimated_duration_seconds"] for ch in result["chapters"])
        # Should be roughly in the right ballpark for 3 minutes
        assert total_ch_dur > 0

    def test_with_analysis(self) -> None:
        findings = [
            {"category": "seo", "severity": "medium", "title": "Issue", "url": ""},
        ]
        result = critique_to_storyline(findings, analysis={"description": "Acme Corp SaaS."})
        assert "Acme Corp SaaS" in result["video_title"]


# ---------------------------------------------------------------------------
# reviews_to_storyline
# ---------------------------------------------------------------------------


class TestReviewsToStoryline:
    def test_basic_conversion(self) -> None:
        reviews = {
            "design_review": {
                "strengths": ["Great colors", "Clean layout"],
                "opportunities": ["Better contrast"],
            },
            "usability_review": {
                "strengths": ["Intuitive nav"],
            },
        }
        result = reviews_to_storyline(reviews, target_minutes=2.0)

        assert "Expert Review Highlights" in result["video_title"]
        assert len(result["chapters"]) == 2
        assert "Design" in result["chapters"][0]["title"]

    def test_empty_reviews_raises_instead_of_shipping_zero_chapters(self) -> None:
        """Same #119 guard on the reviews converter; previously pinned as `== []`."""
        with pytest.raises(ValueError, match="zero-chapter storyline"):
            reviews_to_storyline({})

    def test_filters_string_reviews(self) -> None:
        reviews = {
            "design_review": {"strengths": ["Good"]},
            "error_review": "Failed to generate",
        }
        result = reviews_to_storyline(reviews)
        assert len(result["chapters"]) == 1

    def test_extracts_urls(self) -> None:
        reviews = {
            "feature_review": {
                "items": [
                    {"url": "https://example.com/features", "text": "Good"},
                ],
            },
        }
        result = reviews_to_storyline(reviews)
        assert "https://example.com/features" in result["chapters"][0]["pages"]


# ---------------------------------------------------------------------------
# critique_to_shotlist
# ---------------------------------------------------------------------------


class TestCritiqueToShotlist:
    @pytest.fixture
    def sample_storyline(self) -> dict:
        return {
            "intro": {"title": "Test Site — Growth", "subtitle": "Sub", "duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "SEO Growth Areas",
                    "subtitle": "2 opportunities",
                    "pages": ["https://example.com/"],
                    "estimated_duration_seconds": 60,
                },
            ],
            "outro": {"title": "Roadmap", "subtitle": "Next steps", "duration_seconds": 5},
        }

    def test_basic_structure(self, sample_storyline: dict) -> None:
        findings = [
            {
                "category": "seo",
                "severity": "high",
                "title": "Missing meta",
                "url": "https://example.com/",
                "selector": "",
                "recommendation": "Add meta tags",
            },
        ]
        result = critique_to_shotlist(sample_storyline, findings)

        assert "findings-intro" in result["recording_order"]
        assert "findings-outro" in result["recording_order"]
        assert result["total_parts"] == len(result["parts"])
        assert result["estimated_total_duration_seconds"] > 0

    def test_noncanonical_category_findings_not_dropped(self) -> None:
        """End-to-end critique->storyline->shotlist must not drop findings whose
        RAW category differs from its canonical key. Regression: 'accessibility'
        -> chapter title 'Accessibility ...' -> reverse-map to 'a11y', looked up
        in the raw-'accessibility'-keyed index -> [] -> the chapter emitted only
        a title card and every finding vanished, while the storyline still
        advertised N opportunities. Existing tests all used the canonical 'a11y'
        so the round-trip never broke."""
        findings = [
            {
                "category": "accessibility",
                "severity": "high",
                "title": "Missing alt text",
                "url": "https://example.com/",
                "recommendation": "Add alt attributes",
            },
        ]
        storyline = critique_to_storyline(findings)
        result = critique_to_shotlist(storyline, findings)

        # The finding must reach a real recording part, not vanish behind a
        # lone title card.
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) >= 1
        all_actions = " ".join(
            a for p in result["parts"] for a in p.get("actions", []) if isinstance(a, str)
        )
        assert "Missing alt text" in all_actions

    def test_groups_by_url(self, sample_storyline: dict) -> None:
        findings = [
            {
                "category": "seo",
                "severity": "high",
                "title": "Issue 1",
                "url": "https://example.com/",
            },
            {
                "category": "seo",
                "severity": "medium",
                "title": "Issue 2",
                "url": "https://example.com/",
            },
            {
                "category": "seo",
                "severity": "low",
                "title": "Issue 3",
                "url": "https://example.com/about",
            },
        ]
        result = critique_to_shotlist(sample_storyline, findings)

        # Should have intro + ch title + 2 browser recordings (grouped by URL) + outro
        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 2

    def test_score_card_added(self, sample_storyline: dict) -> None:
        findings = [
            {
                "category": "seo",
                "severity": "high",
                "title": "Issue",
                "url": "https://example.com/",
            },
        ]
        scores = {"seo": 72.5}
        result = critique_to_shotlist(sample_storyline, findings, scores=scores)

        score_parts = [p for p in result["parts"] if "score" in p["id"]]
        assert len(score_parts) == 1
        assert "72.5" in score_parts[0]["actions"][0]

    def test_no_url_findings(self, sample_storyline: dict) -> None:
        findings = [
            {"category": "seo", "severity": "high", "title": "Generic issue", "url": ""},
        ]
        result = critique_to_shotlist(sample_storyline, findings)
        # Should create summary part for no-URL findings
        summary_parts = [p for p in result["parts"] if "Additional" in p.get("title", "")]
        assert len(summary_parts) == 1


# ---------------------------------------------------------------------------
# reviews_to_shotlist
# ---------------------------------------------------------------------------


class TestReviewsToShotlist:
    @pytest.fixture
    def sample_storyline(self) -> dict:
        return {
            "intro": {"title": "Expert Review", "subtitle": "Sub", "duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "Design Strengths",
                    "subtitle": "Beautiful",
                    "pages": ["https://example.com/"],
                    "key_moments": ["Color scheme"],
                    "estimated_duration_seconds": 60,
                },
            ],
            "outro": {"title": "Path Forward", "subtitle": "Go", "duration_seconds": 5},
        }

    def test_basic_structure(self, sample_storyline: dict) -> None:
        reviews = {"design_review": {"strengths": ["Good layout"]}}
        result = reviews_to_shotlist(sample_storyline, reviews)

        assert "review-intro" in result["recording_order"]
        assert "review-outro" in result["recording_order"]
        assert result["total_parts"] == len(result["parts"])

    def test_chapter_with_pages(self, sample_storyline: dict) -> None:
        reviews = {}
        result = reviews_to_shotlist(sample_storyline, reviews)

        browser_parts = [p for p in result["parts"] if p["part_type"] == "browser_recording"]
        assert len(browser_parts) == 1
        assert "Navigate to" in browser_parts[0]["actions"][0]

    def test_chapter_without_pages(self) -> None:
        storyline = {
            "intro": {"title": "Test", "subtitle": "Sub", "duration_seconds": 6},
            "chapters": [
                {
                    "number": 1,
                    "title": "Overview",
                    "subtitle": "Summary",
                    "pages": [],
                    "key_moments": ["Point 1", "Point 2"],
                    "estimated_duration_seconds": 40,
                },
            ],
            "outro": {"title": "End", "subtitle": "Bye", "duration_seconds": 5},
        }
        reviews = {}
        result = reviews_to_shotlist(storyline, reviews)

        overview_parts = [
            p
            for p in result["parts"]
            if p.get("part_type") == "browser_recording" and "Overview" in p.get("title", "")
        ]
        assert len(overview_parts) >= 1
        # Should use key_moments as actions
        assert "Point 1" in overview_parts[0]["actions"]
