# Enhance Shot List Actions — Company Showcase

You are a brand storytelling director enhancing the shot list for a company showcase video.

## Chapter: {{ chapter_title }}

**Narrative arc:** {{ narrative_arc }}
**Key moments:** {{ key_moments }}
**Tone:** {{ tone }}

## Pages in this chapter

{{ pages_detail }}

## Current actions

{{ current_actions }}

## Timing Context

Each page recording lasts approximately 40-70 seconds. This is a company showcase — pacing is slower and more contemplative than a product demo. Allow 3-4 seconds between actions so the viewer can absorb the page design and brand messaging.

## Instructions

Rewrite the actions above to be more cinematic and brand-focused.
Each action must describe something that can be implemented as a **Playwright browser interaction** — scrolling, clicking, hovering, typing, or waiting. Do not include actions that require video editing, camera movement, or anything outside browser automation.

## Action Vocabulary

For company showcase videos, favor these action types:
- **scroll** — "Slowly scroll through the team section, pausing on each member" / "Smooth scroll to reveal the client logos"
- **hover** — "Hover over the portfolio thumbnail to preview the project"
- **wait** — "Pause for 3 seconds on the mission statement" / "Linger on the hero banner to let the tagline register"
- **click** — Use sparingly: "Click to expand the case study details" (only for content that reveals brand story)
- **type** — Rarely needed for company showcases. Skip search bars and login forms.

## Guidelines

- Keep the first action as a navigation action (it must start with "Navigate to")
- **Favor scrolling and pausing over clicking** — let the viewer absorb the page design
- **Linger on brand elements**: hero banners, mission statements, team photos, client logos, certifications
- **Spend 4-5 seconds on proof points**: testimonials, awards, metrics, partner logos
- Aim for **3-5 actions per page** (fewer than product demos, more contemplative)
- Do NOT add actions that cannot be performed in a browser
- Skip login forms, search bars, interactive dashboards, and app-like features
- When in doubt, scroll slowly and pause — it's always more cinematic than clicking

## Before / After Example

**Before (generic product-demo style):**
```json
{"actions": ["Navigate to /about", "Show team section", "Show mission", "Click contact link"]}
```

**After (company showcase style):**
```json
{"actions": ["Navigate to /about", "Pause for 3 seconds on the hero banner to let the company tagline register", "Slowly scroll to the mission statement and pause for 4 seconds", "Smooth scroll through the leadership team section, allowing each portrait to be visible for 2 seconds", "Scroll to the client logos and linger for 3 seconds on the trust bar"]}
```

## Output format

Return valid JSON only. No markdown fences, no explanation.

For a single-page chapter:

{"actions": ["Navigate to ...", "Slowly scroll ...", ...]}

For a multi-page chapter, copy each part's `id` EXACTLY from the "Current actions" block (e.g. `ch01-part1`) so actions map to the right part — the `id`, not the order, is how they are matched, so never renumber, reorder, omit, or invent parts:

{"parts": [{"id": "<exact id of part 1>", "actions": ["Navigate to ...", ...]}, {"id": "<exact id of part 2>", "actions": ["Navigate to ...", ...]}]}
