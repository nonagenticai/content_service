# Pricing Page Demo Patterns

When recording a pricing page, focus on plan comparison, value differentiation, and the conversion path:

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
1. **Navigate** -- Go to the pricing page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the pricing headline

## Content Exploration
4. **Pricing tiers** -- Use `highlightElement(page, selector, label)` on each pricing tier card, pausing briefly so viewers can read the plan name, price, and key features
5. **Recommended plan** -- Use `spotlightElement(page, selector, 'Most popular plan')` on the recommended or most popular plan badge to draw attention to the suggested choice
6. **Feature comparison** -- Use `smoothScroll(page)` to scroll to the feature comparison table, then `highlightElement(page, selector, label)` on key differentiating rows
7. **FAQ or details** -- If pricing FAQ exists below the tiers, scroll to it and `pause(page, 2000)` on relevant questions
8. **Enterprise/custom** -- Use `highlightElement(page, selector, 'Enterprise plan')` on any custom or enterprise plan callout

## Interactive Elements
9. **Billing toggle** -- Use `slowClick(page, selector)` on the monthly/annual billing toggle to show both pricing options
10. **CTA per tier** -- Use `slowClick(page, selector)` on the CTA button of the recommended plan to demonstrate the signup flow
11. **Feature tooltips** -- Use `slowHover(page, selector)` on feature info icons to reveal tooltip explanations
12. **Plan expansion** -- Use `slowClick(page, selector)` on "See all features" or expandable sections within a tier

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if pricing opens a new chapter in the demo
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to plan names, prices, CTA buttons, and the recommended badge
- `spotlightElement(page, selector, label)` -- Dim everything except the recommended plan card
- `smoothScroll(page)` -- Scroll to the feature comparison table and pricing FAQ
- `slowClick(page, selector)` -- Click the billing toggle and tier CTA buttons
- `slowHover(page, selector)` -- Hover over feature tooltips and plan detail links
- `pause(page, ms)` -- Pause on each pricing tier so viewers can read prices and feature lists
- `waitForVisualStability(page)` -- Wait for pricing animations and dynamic price updates after toggle

## Timing
- Allow 3-4 seconds per pricing tier so viewers can read prices and features
- Pause 2 seconds after toggling billing to show the price difference
- Allow 1-2 seconds on each feature comparison row
- Total part duration should match the estimated duration from the shot list
