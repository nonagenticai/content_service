# Enhance Shot List Actions

You are a creative director enhancing the shot list for a website demo video.

## Chapter: {{ chapter_title }}

**Narrative arc:** {{ narrative_arc }}
**Key moments:** {{ key_moments }}
**Tone:** {{ tone }}

## Pages in this chapter

{{ pages_detail }}

## Current actions

{{ current_actions }}

## Timing Context

Each page recording lasts approximately 30-60 seconds. Structure actions to fill this time naturally — don't rush through 8 actions in 15 seconds, and don't leave long gaps between actions in a 60-second recording.

## Instructions

Rewrite the actions above to be more creative, visually engaging, and specific.
Each action must describe something that can be implemented as a **Playwright browser interaction** — scrolling, clicking, hovering, typing, or waiting. Do not include actions that require video editing, camera movement, or anything outside browser automation.

## Action Vocabulary

These action types work well and create visual variety:
- **scroll** — "Slowly scroll to reveal..." / "Scroll down to the pricing section"
- **hover** — "Hover over the feature card to reveal the tooltip" / "Mouse over the navigation menu"
- **click** — "Click the 'Get Started' button" / "Click to expand the FAQ accordion"
- **type** — "Type 'demo@example.com' in the email field"
- **wait** — "Pause for 2 seconds on the hero section" / "Wait for the animation to complete"

## Guidelines

- Keep the first action as a navigation action (it must start with "Navigate to")
- Make actions descriptive and specific (e.g., "Slowly scroll to reveal the pricing cards one by one" instead of "Show pricing tiers")
- Reference actual page elements and content when possible
- Actions should create visual variety — mix scrolling, hovering, clicking, and pausing
- Aim for 4-8 actions per page
- When multiple chapters share the same URL, each chapter's actions MUST focus on completely different page regions. If chapter 1 covers the hero and features, chapter 2 must NOT revisit those sections — it should pick up where the previous chapter left off or focus on an entirely separate area (e.g., pricing, testimonials, footer CTA). Treat the narrative_arc as a hard constraint on which part of the page to interact with.
- Do NOT add actions that cannot be performed in a browser
- Do NOT submit login forms, contact forms, or enter real credentials

## Chapter Differentiation

CRITICAL: Each chapter must feel visually distinct, even if they share the same page URL.
Focus actions on the specific sections described in the narrative arc. Use the narrative_arc keywords to select the right Playwright action pattern:

### Narrative Arc → Action Pattern Map

- **"hero" / "introduction" / "identity" / "brand"** → Pause on the hero section for 2-3 seconds to let the viewer absorb the headline. Hover over the main heading and subheading. Slowly scroll past value proposition text or hero imagery. If there is a hero CTA button, hover over it without clicking.
- **"features" / "capabilities" / "solution" / "how it works"** → Scroll directly to the features grid or feature section. Hover over individual feature cards or icons to reveal tooltips or highlight states. Pause on key differentiators. If features are in tabs or accordions, click to expand 1-2 of them.
- **"pricing" / "plans" / "tiers"** → Scroll to the pricing section. Hover over each pricing tier card in sequence (left to right). Pause longer on the recommended/highlighted plan. If there is a toggle (monthly/annual), click it to show the price change.
- **"testimonials" / "proof" / "trust" / "social proof" / "reviews"** → Scroll to the testimonials or social proof section. Pause on each customer quote for 2-3 seconds. Hover over client logos or avatar images. If there is a carousel, click to advance through 2-3 testimonials.
- **"CTA" / "getting started" / "contact" / "signup" / "conversion"** → Scroll to the primary CTA section (often near the bottom). Hover over the main action button to show its hover state. If there is a form, click into the email field (but do NOT submit). Pause on the final call-to-action message.
- **"navigation" / "overview" / "structure"** → Hover over top navigation menu items to reveal dropdowns. Slowly scroll the full page length to show overall layout. Pause briefly at each major section boundary.
- **"footer" / "resources" / "links"** → Scroll to the bottom of the page. Hover over footer link groups. Pause on any trust badges, certifications, or partner logos in the footer.

If the narrative_arc does not match any of the above, use it as a plain-language description of which page region to focus on. Never fall back to a generic full-page scroll.

### Action Sequencing

Structure actions as a natural viewing flow. For each section the chapter focuses on, follow this sequence:

1. **Scroll to section** — Navigate the viewport to bring the target section into view
2. **Pause to orient** — Wait 1-2 seconds so the viewer can take in the section layout
3. **Highlight specific element** — Hover over or interact with the most important element in that section (a card, button, heading, image)
4. **Explore details** — Interact with 1-2 secondary elements (expand an accordion, hover a second card, click a tab)
5. **Transition out** — Slowly scroll toward the next section or pause before the chapter ends

Not every action sequence needs all 5 steps, but the order should feel like a person naturally exploring that part of the page, not a robot executing a checklist.

### Anti-Patterns — Do NOT

- Do NOT use the same scroll → highlight → scroll pattern for every chapter. Vary the interaction types: some chapters should hover and explore, others should click and reveal, others should pause and let animations play.
- Do NOT highlight generic elements like "the heading" or "the main content area". Be specific: "Hover over the second feature card titled 'Analytics Dashboard'" is good; "Highlight the features heading" is not.
- Do NOT ignore the narrative_arc. It tells you WHICH part of the page this chapter owns. If the narrative_arc says "pricing", do not spend actions on the hero section.
- Do NOT repeat interactions from other chapters. If another chapter already scrolled through the hero, this chapter must start its meaningful interactions elsewhere.

## Output format

Return valid JSON only. No markdown fences, no explanation.
{% if num_parts > 1 %}

This chapter has {{ num_parts }} parts. Return the multi-page format below. For EACH part you MUST copy its `id` EXACTLY as shown in the "Current actions" block above (e.g. `ch01-part1`). The `id` — not the order — is how your actions are matched back to the right part, so never renumber, reorder, omit, or invent parts:
{"parts": [{"id": "<exact id of part 1>", "actions": [...]}, {"id": "<exact id of part 2>", "actions": [...]}]}
{% else %}

For a single-page chapter:

{"actions": ["Navigate to ...", "Slowly scroll ...", ...]}

For a multi-page chapter, copy each part's `id` EXACTLY from the "Current actions" block so actions map to the right part:

{"parts": [{"id": "<exact id of part 1>", "actions": ["Navigate to ...", ...]}, {"id": "<exact id of part 2>", "actions": ["Navigate to ...", ...]}]}
{% endif %}
