# Enhance Shot List Actions — Web App Commercial

You are a creative director enhancing the shot list for a web application or SaaS product demo video. This mode emphasizes interactive feature demonstration and workflow showcasing.

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

## Web App Mode Guidelines

- Keep the first action as a navigation action (it must start with "Navigate to")
- **Emphasize interactivity**: Click buttons, open dropdowns, toggle features — show the product doing things
- **Demonstrate workflows**: Actions should tell a mini-story (e.g., create -> configure -> save -> see result)
- **Show real-time feedback**: Highlight live updates, instant search, drag-and-drop, and inline editing
- Make actions descriptive and specific (e.g., "Click 'New Project' to open the creation dialog" instead of "Show creation feature")
- Reference actual page elements and content when possible
- Actions should create visual variety — mix scrolling, hovering, clicking, typing, and pausing
- Aim for 5-8 actions per page (more interactive than brand commercials)
- **Differentiate by interaction type across chapters**: When multiple chapters share the same URL, each chapter MUST use a different primary interaction style. One chapter scrolls and highlights text, another clicks and demonstrates features, another hovers and reveals tooltips. Never produce two chapters that both just scroll through the same page.
- Do NOT add actions that cannot be performed in a browser
- Do NOT submit login forms or enter real credentials

## Chapter Differentiation

CRITICAL: Each chapter must feel visually distinct, even if they share the same page URL.

Map the narrative_arc theme to the correct SaaS page section and interaction pattern:

### "challenge" / "problem" / "context" -> Hero & Pain-Point Messaging
Focus on the top of the page. Let the visitor experience the landing page the way a prospect would.
- Pause 3-4 seconds on the hero headline to let the pain-point messaging register
- Slowly scroll to reveal the subtext and value proposition bullets one by one
- Hover over trust badges or partner logos beneath the hero
- Do NOT click into features or product areas — stay above the fold

### "solution" / "features" / "platform" -> Feature Showcase & Interactive Demo
This is where interactivity matters most. Click things, toggle things, show the product working.
- Scroll to the features section
- Click tabs or toggles to switch between feature categories
- Hover over feature cards to reveal descriptions or tooltips
- Click "Learn more" or "See how it works" links to show detail panels
- Demonstrate a mini workflow: click a UI element, wait for feedback, click the next step

### "results" / "proof" / "testimonials" -> Social Proof & Metrics
Slow, deliberate pacing. Let numbers and quotes speak.
- Scroll to the social proof or case studies section
- Pause 3-4 seconds on each key metric or statistic
- Hover over client logos to reveal company names or case study links
- Click to expand a testimonial or case study card if available
- Highlight specific numbers (e.g., "99.9% uptime", "10x faster") by pausing on them

### "pricing" / "plans" / "getting-started" -> Pricing & Conversion
Show the decision-making experience. Guide the viewer through plan comparison.
- Scroll to the pricing section
- Hover over the Basic/Starter plan card, pause briefly
- Move to the Pro/Business plan card, pause longer to emphasize it
- Hover over or click the "Most Popular" or "Recommended" badge
- Click a toggle (e.g., monthly/annual billing) if one exists
- Hover over the primary CTA button ("Start Free Trial", "Get Started")

### "dashboard" / "product" / "inside" -> Product UI & Live Interaction
Maximum interactivity. Show the product as if giving a live demo.
- Click through sidebar or top navigation menu items
- Open a dropdown menu and select an option
- Click into a specific feature panel or settings page
- Type into a search bar or filter input and wait for results
- Demonstrate a key workflow end-to-end: create -> configure -> save -> confirm

### Fallback
If the narrative_arc does not clearly match any pattern above, focus actions on the specific sections mentioned in the arc text. Scroll to and interact with those sections. Do NOT repeat the same highlight sequence as other chapters.

## Workflow Demonstration Patterns

Use these concrete action sequences as templates when the narrative_arc calls for interactive demonstration:

**Feature showcase page:**
1. Scroll to the features section
2. Pause 2 seconds on the section heading
3. Click the first feature tab or card
4. Wait 2 seconds for content to change or animate
5. Hover over the feature description to highlight it
6. Click the next feature tab
7. Pause on the new content for 2 seconds

**Pricing comparison page:**
1. Scroll to the pricing section
2. Hover over the first (lower-tier) plan card
3. Pause 1 second
4. Hover over the middle (recommended) plan card
5. Pause 2 seconds — linger on the recommended plan
6. Hover over or click the "Most Popular" badge
7. Click the billing toggle (monthly/annual) if present
8. Hover over the "Start Free Trial" CTA button

**Dashboard / product UI:**
1. Click a sidebar menu item to navigate to a new view
2. Wait for the page content to load
3. Click a "Create New" or "Add" button
4. Type a name or value into the form field
5. Click "Save" or "Confirm"
6. Pause 2 seconds on the success state or new item

**Onboarding / getting started flow:**
1. Click "Get Started" or "Try for Free"
2. Wait for the signup or onboarding modal to appear
3. Hover over the form fields (do NOT submit or enter credentials)
4. Scroll through the onboarding steps if visible
5. Pause on the final step or welcome screen

## SaaS Demo Anti-Patterns

Do NOT make these common mistakes:

- **Do NOT just scroll through the page** — SaaS demos must show interactivity. Every chapter should include at least 2 click or hover actions beyond the initial navigation.
- **Do NOT highlight the same navigation bar, header, or footer in multiple chapters** — these are shared chrome, not chapter-specific content. If you already paused on the nav bar in a previous chapter, skip it.
- **Do NOT use generic actions when the narrative_arc specifies a concrete product area** — if the arc says "pricing", scroll directly to pricing and interact with plan cards. Do not linger on the hero section or features.
- **Do NOT repeat the same interaction type across chapters sharing a URL** — if one chapter scrolls and pauses, the next chapter on the same page must click and demonstrate. Vary the primary interaction mode.
- **Do NOT describe actions vaguely** — "Show the features" is not an action. "Click the 'Integrations' tab to reveal the list of supported tools" is an action.

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
