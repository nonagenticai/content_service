"""Aggregate the per-item ``degraded`` markers into ONE machine-readable signal.

Every fail-open fallback in this service already marks the dict it emits with
``degraded: True`` (``StorylineGenerator._default_storyline``,
``NarrationBuilder``'s six absorbs, ``ShotListBuilder``'s pageless-chapter stub).
That marker is honest but it is *buried*: to learn that a job shipped
boilerplate a caller has to walk the nested content JSON — iterate
``narration.parts[*].degraded``, reach into ``storyline.degraded``,
scan ``shot_list.parts[*].degraded``. The orchestrator does none of that, so a
single worker-management hiccup produces a complete, confident-looking
"Product Tour" built entirely from templates and reported 100% green.

This module lifts those markers to the top of the job/API envelope as::

    {"degraded": true,
     "reasons": [{"code": "narration_fallback", "count": 3}]}

Contract:

* ``degraded`` is the OR of every item marker. False means every item that
  could mark itself did not — the false-positive guard every working job
  depends on.
* ``reasons`` carries STABLE codes (the constants below) with counts, sorted by
  code so the envelope is deterministic and diffable. It is EMPTY when
  ``degraded`` is false; a code never appears with ``count: 0``.
* This is visibility ONLY. Nothing here changes a job's status, and nothing
  here raises: a degraded job is still ``completed``. Honesty, not redness.
"""

from __future__ import annotations

from typing import Final

# --- Stable reason codes ----------------------------------------------------
# These are part of the wire contract with the orchestrator. Append new codes;
# never rename or repurpose an existing one.

#: The whole storyline is ``StorylineGenerator._default_storyline`` boilerplate
#: ("{name} — Product Tour"), not LLM output.
STORYLINE_FABRICATED: Final = "storyline_fabricated"

#: A narration part is deterministic Jinja/template text (or has text but no
#: synthesised audio), not LLM voiceover.
NARRATION_FALLBACK: Final = "narration_fallback"

#: A ``browser_recording`` part has no URL to record — the chapter it came from
#: declared ``pages: []``. There is literally nothing for the recorder to point
#: a browser at.
SHOT_MISSING_URL: Final = "shot_missing_url"

#: A shot-list part marked itself degraded without naming a reason.
SHOT_DEGRADED: Final = "shot_degraded"


def build_report(counts: dict[str, int]) -> dict:
    """Normalise ``{code: count}`` into the envelope shape.

    Zero and negative counts are dropped, so ``reasons`` is empty exactly when
    ``degraded`` is false.
    """
    reasons = [
        {"code": code, "count": count} for code, count in sorted(counts.items()) if count > 0
    ]
    return {"degraded": bool(reasons), "reasons": reasons}


def empty_report() -> dict:
    """The healthy envelope: not degraded, no reasons."""
    return {"degraded": False, "reasons": []}


def storyline_counts(storyline: object) -> dict[str, int]:
    """Count degradation in a storyline result dict."""
    if isinstance(storyline, dict) and storyline.get("degraded"):
        return {STORYLINE_FABRICATED: 1}
    return {}


def narration_counts(narration: object) -> dict[str, int]:
    """Count degraded narration parts in a narration result dict."""
    if not isinstance(narration, dict):
        return {}
    parts = narration.get("parts")
    if not isinstance(parts, list):
        return {}
    n = sum(1 for p in parts if isinstance(p, dict) and p.get("degraded"))
    return {NARRATION_FALLBACK: n} if n else {}


def shotlist_counts(shot_list: object) -> dict[str, int]:
    """Count degraded shot-list parts, grouped by their ``degraded_reason``."""
    if not isinstance(shot_list, dict):
        return {}
    parts = shot_list.get("parts")
    if not isinstance(parts, list):
        return {}
    counts: dict[str, int] = {}
    for part in parts:
        if not (isinstance(part, dict) and part.get("degraded")):
            continue
        reason = part.get("degraded_reason")
        code = reason if isinstance(reason, str) and reason else SHOT_DEGRADED
        counts[code] = counts.get(code, 0) + 1
    return counts


def storyline_report(storyline: object) -> dict:
    """Top-level degradation envelope for a storyline job result."""
    return build_report(storyline_counts(storyline))


def narration_report(narration: object) -> dict:
    """Top-level degradation envelope for a narration job result."""
    return build_report(narration_counts(narration))


def shotlist_report(shot_list: object) -> dict:
    """Top-level degradation envelope for a shot-list response."""
    return build_report(shotlist_counts(shot_list))
