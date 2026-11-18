# About Page Demo Patterns

When recording an about page, focus on the company story, team, and trust-building elements:

## MANDATORY Scrolling Rules

BEFORE highlighting or spotlighting ANY element:
1. Call `await scrollToElement(page, selector)` to scroll it into view
2. Call `await pause(page, 500)` to let the scroll settle
3. THEN call `highlightElement()` or `spotlightElement()`

NEVER call highlightElement/spotlightElement on an element without scrolling to it first. Elements below the fold will be INVISIBLE if you skip this step.

Example — CORRECT pattern:
```typescript
await scrollToElement(page, '.features-section');
await pause(page, 500);
await highlightElement(page, '.features-section h2', 'Key Features');
```

Example — WRONG (highlight may fail silently):
```typescript
await highlightElement(page, '.features-section h2', 'Key Features');
```

## Navigation & Load
1. **Navigate** -- Go to the about page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the company or team introduction

## Content Exploration
4. **Mission statement** -- Use `spotlightElement(page, selector, 'Mission')` on the mission statement or value proposition to emphasize the company purpose
5. **Company story** -- Use `smoothScroll(page)` to scroll through the company narrative, pausing on key paragraphs with `pause(page, 2000)`
6. **Team section** -- Use `highlightElement(page, selector, label)` on 2-3 team member cards with name and role labels -- keep labels concise
7. **History or timeline** -- If present, use `smoothScroll(page)` to scroll through the company timeline and `highlightElement(page, selector, label)` on key milestones
8. **Partnerships and trust** -- Use `highlightElement(page, selector, 'Partners & certifications')` on partnership logos, certifications, or awards

## Interactive Elements
9. **Team member cards** -- Use `slowHover(page, selector)` on team member cards to reveal hover details like bio or social links
10. **Timeline interactions** -- Use `slowClick(page, selector)` on timeline nodes or expandable history sections if they are interactive
11. **CTA links** -- Use `highlightElement(page, selector, label)` on any call-to-action like "Join us", "Careers", or "Contact us"
12. **Video embed** -- If a company video exists, use `highlightElement(page, selector, 'Company video')` to annotate it

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the about page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to team members, milestones, partnerships, and CTAs
- `spotlightElement(page, selector, label)` -- Dim everything except the mission statement or a key team member
- `smoothScroll(page)` -- Scroll through sections naturally to reveal the full company story
- `slowClick(page, selector)` -- Click interactive timeline elements and expandable sections
- `slowHover(page, selector)` -- Hover over team member cards to reveal bio details
- `pause(page, ms)` -- Pause on the mission statement, key milestones, and team highlights
- `waitForVisualStability(page)` -- Wait for team photos, partner logos, and timeline animations to load

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## Timing
- Allow 3 seconds on the mission statement for the viewer to absorb the company purpose
- Pause 1-2 seconds per team member highlight -- name and role are enough
- Allow 2-3 seconds on timeline milestones and partnership logos
- Total part duration should match the estimated duration from the shot list
