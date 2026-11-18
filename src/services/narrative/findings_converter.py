"""Deterministic conversion of critique/review findings into Storyline and ShotList dicts."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import cast

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Positive category titles — never use negative framing
_CATEGORY_TITLES: dict[str, str] = {
    "seo": "SEO Growth Areas",
    "security": "Security Enhancements",
    "a11y": "Accessibility Opportunities",
    "performance": "Performance Optimizations",
    "ux": "User Experience Refinements",
}

# Category ordering: biggest opportunity first
_CATEGORY_ORDER: dict[str, int] = {
    "a11y": 0,
    "seo": 1,
    "security": 2,
    "performance": 3,
    "ux": 4,
}

# Minimum duration per finding (seconds)
_MIN_FINDING_DURATION_S = 15.0
_TITLE_CARD_DURATION_S = 5.0
_SCORE_CARD_DURATION_S = 8.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _site_name(analysis: dict | None) -> str:
    """Extract a display name for the site."""
    if analysis and analysis.get("description"):
        desc = analysis["description"].strip()
        first_sentence = desc.split(".")[0].strip()
        if len(first_sentence) <= 60:
            return first_sentence
    return "Website"


def _category_narrative(category: str, findings: list[dict]) -> str:
    """Build a narrative arc for a category chapter."""
    high_sev = [f for f in findings if f.get("severity") in ("critical", "high")]
    recs = [f.get("recommendation") for f in findings if f.get("recommendation")]

    parts = []
    parts.append(
        f"Explore {len(findings)} opportunities in {_CATEGORY_TITLES.get(category, category)}."
    )
    if high_sev:
        parts.append(f"{len(high_sev)} high-impact items could make a significant difference.")
    if recs:
        parts.append("Actionable recommendations are highlighted for each area.")
    return " ".join(parts)


def _review_type_title(review_type: str) -> str:
    """Convert review type key to a positive display title."""
    titles: dict[str, str] = {
        "design_review": "Design Strengths & Opportunities",
        "usability_review": "Usability Insights",
        "feature_review": "Feature Landscape",
        "content_quality_review": "Content Quality Highlights",
        "feature_suggestions": "Features That Could Set You Apart",
    }
    return titles.get(review_type, review_type.replace("_", " ").title())


def _finding_actions(finding: dict) -> list[str]:
    """Build action list for a single finding."""
    actions: list[str] = []
    if finding.get("url"):
        actions.append(f"Navigate to {finding['url']}")
    if finding.get("selector"):
        actions.append(f"Scroll to and highlight element: {finding['selector']}")
    actions.append(f"Show finding card: {finding.get('title', 'Finding')}")
    if finding.get("recommendation"):
        actions.append(f"Display recommendation: {finding['recommendation']}")
    return actions


def _finding_duration(finding: dict) -> float:
    """Estimate recording duration for a single finding."""
    base = _MIN_FINDING_DURATION_S
    if finding.get("selector"):
        base += 5.0  # Time for scroll + highlight
    if finding.get("recommendation"):
        base += 3.0  # Time for recommendation overlay
    return base


def _chapter_to_category(title: str) -> str:
    """Map a chapter title back to its finding category key (e.g. 'seo', 'a11y')."""
    title_lower = title.lower()
    mapping = {
        "seo": "seo",
        "security": "security",
        "accessibility": "a11y",
        "a11y": "a11y",
        "performance": "performance",
        "user experience": "ux",
        "ux": "ux",
    }
    for keyword, cat in mapping.items():
        if keyword in title_lower:
            return cat
    return title_lower.split()[0] if title_lower else "unknown"


# ---------------------------------------------------------------------------
# Storyline converters
# ---------------------------------------------------------------------------


def critique_to_storyline(
    findings: list[dict],
    scores: dict[str, float] | None = None,
    analysis: dict | None = None,
    target_minutes: float = 3.0,
) -> dict:
    """Convert critique findings into a Storyline dict.

    Groups findings by category, orders chapters by score (lowest first =
    biggest opportunity), and uses positive framing throughout.
    """
    site = _site_name(analysis)
    scores = scores or {}

    # Group findings by category
    by_category: dict[str, list[dict]] = defaultdict(list)
    for finding in findings:
        by_category[finding.get("category", "unknown")].append(finding)

    # Order categories: lowest score first (biggest opportunity)
    scored_cats = []
    for cat in by_category:
        score = scores.get(cat, 50.0)
        scored_cats.append((cat, score))
    scored_cats.sort(key=lambda x: (x[1], _CATEGORY_ORDER.get(x[0], 99)))

    # Build chapters
    chapters: list[dict] = []
    total_findings = len(findings)
    time_per_finding = (target_minutes * 60) / max(total_findings, 1) if total_findings else 30.0
    time_per_finding = max(10.0, min(time_per_finding, 45.0))  # Clamp

    for i, (cat, _score) in enumerate(scored_cats):
        cat_findings = by_category[cat]
        # Unique URLs from this category's findings
        pages = list(dict.fromkeys(f.get("url") for f in cat_findings if f.get("url")))
        chapter_duration = len(cat_findings) * time_per_finding + 5.0  # +5 for title card

        chapters.append(
            {
                "number": i + 1,
                # Carry the raw category key so the shotlist converter can match
                # findings/scores directly instead of lossily reverse-mapping the
                # display title (e.g. "Accessibility" -> "a11y") which drops every
                # finding whose raw category != its canonical key.
                "category": cat,
                "title": _CATEGORY_TITLES.get(cat) or cat.title(),
                "subtitle": f"{len(cat_findings)} opportunities to enhance",
                "pages": pages,
                "narrative_arc": _category_narrative(cat, cat_findings),
                "key_moments": [f.get("title", "") for f in cat_findings[:3]],
                "estimated_duration_seconds": chapter_duration,
            }
        )

    # #119's zero-chapter guard, applied to the sync converter route as well.
    # `findings: []` (or findings that yield no category at all) previously
    # returned 200 with a real-looking intro + outro and `chapters: []`. That is
    # a MEASUREMENT GAP scoring as a measurement: downstream a zero-chapter
    # storyline builds an intro+outro-only shot list, i.e. a ~11-second video
    # reported `completed`. Raising here lands in the route's
    # `except (AttributeError, KeyError, TypeError, ValueError)` and returns 422 —
    # same contract as `StorylineGenerator._parse_storyline_response`.
    if not chapters:
        raise ValueError(
            f"{total_findings} findings produced zero chapters — refusing to ship a "
            "zero-chapter storyline as success"
        )

    return {
        "video_title": f"{site} — Growth Opportunities Report",
        "video_description": (
            f"A positive analysis of {total_findings} opportunities "
            f"across {len(chapters)} categories for {site}."
        ),
        "target_duration_minutes": target_minutes,
        "tone": "constructive consultant — positive, encouraging, actionable",
        "intro": {
            "title": f"{site} — Growth Opportunities",
            "subtitle": "Areas where your site can truly excel",
            "duration_seconds": 6.0,
            "narrative_text": (
                f"Let's explore the opportunities that could take {site} to the next level."
            ),
        },
        "chapters": chapters,
        "outro": {
            "title": "Your Roadmap to Excellence",
            "subtitle": "Every great site starts with small improvements",
            "duration_seconds": 5.0,
            "narrative_text": (
                f"With {total_findings} actionable opportunities identified, "
                f"{site} is well-positioned for growth."
            ),
        },
    }


def _extract_review_pages(review_data: object) -> list[str]:
    pages: list[str] = []
    if not isinstance(review_data, dict):
        return pages
    rd = cast(dict[str, object], review_data)
    for val in rd.values():
        if isinstance(val, str) and val.startswith("http"):
            pages.append(val)
        elif isinstance(val, list):
            for item in val:
                if isinstance(item, dict):
                    item_dict = cast(dict[str, object], item)
                    url_val = item_dict.get("url")
                    if isinstance(url_val, str):
                        pages.append(url_val)
    return pages


def _split_review_strengths_opportunities(review_data: object) -> tuple[list[str], list[str]]:
    strengths: list[str] = []
    opportunities: list[str] = []
    if not isinstance(review_data, dict):
        return strengths, opportunities
    rd = cast(dict[str, object], review_data)
    for key_str, val in rd.items():
        if not isinstance(key_str, str) or not isinstance(val, list):
            continue
        key_lower = key_str.lower()
        target = (
            strengths
            if any(w in key_lower for w in ("strength", "positive", "good"))
            else opportunities
        )
        for item in val:
            target.append(item if isinstance(item, str) else str(item))
    return strengths, opportunities


def _build_review_chapter(
    index: int,
    review_type: str,
    review_data: object,
    time_per_review: float,
) -> dict:
    pages = _extract_review_pages(review_data)
    strengths, opportunities = _split_review_strengths_opportunities(review_data)

    arc_parts: list[str] = []
    if strengths:
        arc_parts.append(f"Celebrating {len(strengths)} strong points.")
    if opportunities:
        arc_parts.append(f"Exploring {len(opportunities)} areas for growth.")
    narrative_arc = (
        " ".join(arc_parts)
        if arc_parts
        else (f"A thorough look at {_review_type_title(review_type).lower()}.")
    )

    return {
        "number": index + 1,
        "title": _review_type_title(review_type),
        "subtitle": "Strengths and opportunities",
        "pages": pages[:5],
        "narrative_arc": narrative_arc,
        "key_moments": strengths[:2] + opportunities[:1],
        "estimated_duration_seconds": time_per_review,
    }


def reviews_to_storyline(
    reviews: Mapping[str, object],
    analysis: dict | None = None,
    target_minutes: float = 3.0,
) -> dict:
    """Convert review results into a Storyline dict.

    Highlights strengths first, then opportunities for each review type.
    """
    site = _site_name(analysis)

    meaningful_reviews = {k: v for k, v in reviews.items() if v and not isinstance(v, str)}

    if not meaningful_reviews:
        logger.warning("No meaningful reviews to build storyline from")

    time_per_review = (target_minutes * 60) / max(len(meaningful_reviews), 1)
    time_per_review = max(20.0, min(time_per_review, 90.0))

    chapters: list[dict] = [
        _build_review_chapter(i, review_type, review_data, time_per_review)
        for i, (review_type, review_data) in enumerate(meaningful_reviews.items())
    ]

    # Same #119 zero-chapter guard as `critique_to_storyline` above: no
    # meaningful review => no chapters => an intro+outro-only "success". The
    # route turns this ValueError into a 422.
    if not chapters:
        raise ValueError(
            f"{len(reviews)} reviews produced zero chapters — refusing to ship a "
            "zero-chapter storyline as success"
        )

    return {
        "video_title": f"{site} — Expert Review Highlights",
        "video_description": (
            f"AI-powered review of {site} covering {len(chapters)} areas of analysis."
        ),
        "target_duration_minutes": target_minutes,
        "tone": "expert reviewer — celebratory of strengths, encouraging about growth",
        "intro": {
            "title": f"{site} — Expert Review",
            "subtitle": "Celebrating strengths, discovering opportunities",
            "duration_seconds": 6.0,
            "narrative_text": (
                f"An expert review of {site}, highlighting what works well "
                f"and where there's room to shine even brighter."
            ),
        },
        "chapters": chapters,
        "outro": {
            "title": "The Path Forward",
            "subtitle": "Building on a strong foundation",
            "duration_seconds": 5.0,
            "narrative_text": (
                f"With clear strengths to build on, {site} has an exciting "
                f"roadmap of possibilities ahead."
            ),
        },
    }


# ---------------------------------------------------------------------------
# ShotList converters
# ---------------------------------------------------------------------------


def _build_url_part(
    ch_num: int, part_num: int, chapter_title: str, url: str, url_findings: list[dict]
) -> tuple[dict, float]:
    all_actions: list[str] = []
    all_selectors: list[str] = []
    part_duration = 0.0
    for f in url_findings:
        all_actions.extend(_finding_actions(f))
        if f.get("selector"):
            all_selectors.append(f["selector"])
        part_duration += _finding_duration(f)
    part = {
        "id": f"ch{ch_num:02d}-part{part_num}",
        "part_type": "browser_recording",
        "chapter": ch_num,
        "title": f"{chapter_title} — {url_findings[0].get('title', '')}",
        "pages": [url],
        "actions": all_actions,
        "key_elements": all_selectors,
        "estimated_duration_seconds": part_duration,
    }
    return part, part_duration


def _build_no_url_part(
    ch_num: int, part_num: int, chapter: dict, no_url: list[dict]
) -> tuple[dict, float]:
    actions: list[str] = []
    for f in no_url:
        actions.append(f"Show finding card: {f.get('title', 'Finding')}")
        if f.get("recommendation"):
            actions.append(f"Display recommendation: {f['recommendation']}")
    ch_pages = chapter.get("pages", [])
    duration = len(no_url) * _MIN_FINDING_DURATION_S
    part = {
        "id": f"ch{ch_num:02d}-part{part_num}",
        "part_type": "browser_recording",
        "chapter": ch_num,
        "title": f"{chapter.get('title', '')} — Additional Opportunities",
        "pages": ch_pages[:1] if ch_pages else [],
        "actions": actions,
        "estimated_duration_seconds": duration,
    }
    return part, duration


def _process_chapter_for_shotlist(
    chapter: dict,
    by_category: dict[str, list[dict]],
    scores: dict[str, float] | None,
) -> tuple[list[dict], list[str], float]:
    parts: list[dict] = []
    order: list[str] = []
    duration = 0.0
    ch_num = chapter.get("number", 0)
    chapter_title = chapter.get("title", "")

    ch_title_id = f"ch{ch_num:02d}-title"
    parts.append(
        {
            "id": ch_title_id,
            "part_type": "title_card",
            "chapter": ch_num,
            "title": chapter_title,
            "subtitle": chapter.get("subtitle", ""),
            "estimated_duration_seconds": _TITLE_CARD_DURATION_S,
        }
    )
    order.append(ch_title_id)
    duration += _TITLE_CARD_DURATION_S

    # Prefer the raw category carried on the chapter (exact match against the
    # raw-keyed by_category/scores); fall back to reverse-mapping the title for
    # storylines built elsewhere (e.g. by the LLM) that lack the field.
    cat_key = chapter.get("category") or _chapter_to_category(chapter_title)
    cat_findings = by_category.get(cat_key, [])

    by_url: dict[str, list[dict]] = defaultdict(list)
    no_url: list[dict] = []
    for f in cat_findings:
        if f.get("url"):
            by_url[f["url"]].append(f)
        else:
            no_url.append(f)

    part_num = 0
    for url, url_findings in by_url.items():
        part_num += 1
        part, pd = _build_url_part(ch_num, part_num, chapter_title, url, url_findings)
        parts.append(part)
        order.append(part["id"])
        duration += pd

    if no_url:
        part_num += 1
        part, pd = _build_no_url_part(ch_num, part_num, chapter, no_url)
        parts.append(part)
        order.append(part["id"])
        duration += pd

    if scores and cat_key in scores:
        score_id = f"ch{ch_num:02d}-score"
        parts.append(
            {
                "id": score_id,
                "part_type": "browser_recording",
                "chapter": ch_num,
                "title": f"{chapter_title} — Score Overview",
                "actions": [
                    f"Show score dashboard with {cat_key} score: {scores[cat_key]:.1f}/100",
                ],
                "estimated_duration_seconds": _SCORE_CARD_DURATION_S,
            }
        )
        order.append(score_id)
        duration += _SCORE_CARD_DURATION_S

    return parts, order, duration


def critique_to_shotlist(
    storyline: dict,
    findings: list[dict],
    scores: dict[str, float] | None = None,
) -> dict:
    """Convert a Storyline dict and findings into a ShotList dict.

    Groups findings by page URL within each category chapter, creating
    one browser recording part per unique URL.
    """
    parts: list[dict] = []
    recording_order: list[str] = []
    total_duration = 0.0

    intro = storyline.get("intro", {})
    intro_id = "findings-intro"
    parts.append(
        {
            "id": intro_id,
            "part_type": "title_card",
            "title": intro.get("title", ""),
            "subtitle": intro.get("subtitle", ""),
            "estimated_duration_seconds": intro.get("duration_seconds", 6.0),
        }
    )
    recording_order.append(intro_id)
    total_duration += intro.get("duration_seconds", 6.0)

    by_category: dict[str, list[dict]] = defaultdict(list)
    for f in findings:
        by_category[f.get("category", "unknown")].append(f)

    for chapter in storyline.get("chapters", []):
        ch_parts, ch_order, ch_duration = _process_chapter_for_shotlist(
            chapter, by_category, scores
        )
        parts.extend(ch_parts)
        recording_order.extend(ch_order)
        total_duration += ch_duration

    outro = storyline.get("outro", {})
    outro_id = "findings-outro"
    parts.append(
        {
            "id": outro_id,
            "part_type": "title_card",
            "title": outro.get("title", ""),
            "subtitle": outro.get("subtitle", ""),
            "estimated_duration_seconds": outro.get("duration_seconds", 5.0),
        }
    )
    recording_order.append(outro_id)
    total_duration += outro.get("duration_seconds", 5.0)

    return {
        "parts": parts,
        "recording_order": recording_order,
        "total_parts": len(parts),
        "estimated_total_duration_seconds": total_duration,
    }


def reviews_to_shotlist(
    storyline: dict,
    reviews: Mapping[str, object],
) -> dict:
    """Convert a Storyline dict and reviews into a ShotList dict.

    Creates browser recording parts for each review type showing
    relevant pages with strengths and opportunities.
    """
    parts: list[dict] = []
    recording_order: list[str] = []
    total_duration = 0.0

    intro = storyline.get("intro", {})

    # Intro title card
    intro_id = "review-intro"
    parts.append(
        {
            "id": intro_id,
            "part_type": "title_card",
            "title": intro.get("title", ""),
            "subtitle": intro.get("subtitle", ""),
            "estimated_duration_seconds": intro.get("duration_seconds", 6.0),
        }
    )
    recording_order.append(intro_id)
    total_duration += intro.get("duration_seconds", 6.0)

    for chapter in storyline.get("chapters", []):
        ch_num = chapter.get("number", 0)

        # Chapter title card
        ch_title_id = f"ch{ch_num:02d}-title"
        parts.append(
            {
                "id": ch_title_id,
                "part_type": "title_card",
                "chapter": ch_num,
                "title": chapter.get("title", ""),
                "subtitle": chapter.get("subtitle", ""),
                "estimated_duration_seconds": _TITLE_CARD_DURATION_S,
            }
        )
        recording_order.append(ch_title_id)
        total_duration += _TITLE_CARD_DURATION_S

        # Browser recording parts based on chapter pages
        ch_pages = chapter.get("pages", [])
        if ch_pages:
            for j, url in enumerate(ch_pages):
                part_id = f"ch{ch_num:02d}-part{j + 1}"
                actions = [
                    f"Navigate to {url}",
                    "Highlight key areas discussed in this review",
                    "Show strengths with positive annotations",
                    "Highlight opportunities for enhancement",
                ]
                part_duration = max(
                    20.0,
                    chapter.get("estimated_duration_seconds", 60) / max(len(ch_pages), 1),
                )

                parts.append(
                    {
                        "id": part_id,
                        "part_type": "browser_recording",
                        "chapter": ch_num,
                        "title": f"{chapter.get('title', '')} — Page {j + 1}",
                        "pages": [url],
                        "actions": actions,
                        "estimated_duration_seconds": part_duration,
                    }
                )
                recording_order.append(part_id)
                total_duration += part_duration
        else:
            # No specific pages — single summary part
            part_id = f"ch{ch_num:02d}-part1"
            key_moments = chapter.get("key_moments", [])
            parts.append(
                {
                    "id": part_id,
                    "part_type": "browser_recording",
                    "chapter": ch_num,
                    "title": f"{chapter.get('title', '')} — Overview",
                    "actions": key_moments[:5]
                    if key_moments
                    else [
                        "Show review highlights",
                    ],
                    "estimated_duration_seconds": chapter.get("estimated_duration_seconds", 60),
                }
            )
            recording_order.append(part_id)
            total_duration += chapter.get("estimated_duration_seconds", 60)

    outro = storyline.get("outro", {})

    # Outro title card
    outro_id = "review-outro"
    parts.append(
        {
            "id": outro_id,
            "part_type": "title_card",
            "title": outro.get("title", ""),
            "subtitle": outro.get("subtitle", ""),
            "estimated_duration_seconds": outro.get("duration_seconds", 5.0),
        }
    )
    recording_order.append(outro_id)
    total_duration += outro.get("duration_seconds", 5.0)

    return {
        "parts": parts,
        "recording_order": recording_order,
        "total_parts": len(parts),
        "estimated_total_duration_seconds": total_duration,
    }
