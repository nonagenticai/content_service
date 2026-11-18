# Landing Page Demo Patterns

When recording a landing page, focus on the value proposition, conversion flow, and persuasion elements:

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
1. **Navigate** -- Go to the landing page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the hero message, which is the most important element

## Content Exploration
4. **Hero section** -- Use `spotlightElement(page, selector, 'Value proposition')` on the hero headline and subheadline to emphasize the core message
5. **Feature highlights** -- Use `smoothScroll(page)` to scroll to the features section, then `highlightElement(page, selector, label)` on 2-3 key feature blocks
6. **Social proof** -- Use `highlightElement(page, selector, 'Testimonials')` on testimonials, stats counters, or client logos with brief annotations
7. **How it works** -- Scroll to the comparison or how-it-works section and `highlightElement(page, selector, 'Step N')` on each step
8. **Bottom CTA** -- Use `smoothScroll(page)` to reach the bottom signup form or CTA and `highlightElement(page, selector, 'Final call-to-action')` to close the demo

## Interactive Elements
9. **Primary CTA buttons** -- Use `slowClick(page, selector)` on the top hero CTA button with visible cursor movement
10. **Bottom signup form** -- If a form exists, use `slowType(page, locator, 'user@example.com')` to demonstrate the signup input
11. **Video embeds** -- Use `highlightElement(page, selector, 'Product video')` on any embedded product video or demo
12. **Pricing toggle** -- If present, use `slowClick(page, selector)` on monthly/annual toggles or plan selectors

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start since landing pages often open a demo
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to feature blocks, testimonials, and CTA buttons
- `spotlightElement(page, selector, label)` -- Dim everything except the hero headline or primary CTA
- `smoothScroll(page)` -- Scroll between sections naturally to reveal the full persuasion flow
- `slowClick(page, selector)` -- Click CTA buttons and interactive toggles
- `slowType(page, locator, text)` -- Type into signup forms to demonstrate conversion flow
- `pause(page, ms)` -- Pause on the hero message, social proof, and how-it-works steps
- `waitForVisualStability(page)` -- Wait for hero animations, counters, and lazy-loaded sections to settle

## Timing
- Allow 3-4 seconds on the hero section -- the value proposition must be absorbed
- Pause 1-2 seconds per feature highlight
- Allow 2-3 seconds on social proof elements
- Total part duration should match the estimated duration from the shot list
