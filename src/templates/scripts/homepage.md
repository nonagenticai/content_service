# Homepage Demo Patterns

When recording a homepage, focus on the first impression, value proposition, and key conversion paths:

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
1. **Navigate** -- Go to the homepage URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the value proposition

## Content Exploration
4. **Hero section** -- Use `highlightElement(page, selector, 'Hero headline')` on the main hero area to emphasize the value proposition
5. **Navigation bar** -- Use `slowHover(page, selector)` across 2-3 main menu items to reveal the site structure
6. **Features grid** -- Use `smoothScroll(page)` to scroll to the features section, then `highlightElement(page, selector, label)` on each feature card briefly
7. **Social proof** -- Scroll to testimonials, client logos, or stats and use `spotlightElement(page, selector, 'Customer testimonials')` to draw attention
8. **Footer overview** -- Use `smoothScroll(page)` to reach the footer and `pause(page, 2000)` so the viewer can see contact info and social links

## Interactive Elements
9. **Primary CTA** -- Use `slowClick(page, selector)` on the main call-to-action button in the hero section
10. **Secondary CTAs** -- Use `highlightElement(page, selector, label)` on any secondary CTAs (e.g., "Learn More", "See Demo")
11. **Navigation links** -- Use `slowHover(page, selector)` on dropdown menus to reveal sub-navigation
12. **Sticky header** -- If present, scroll down and `highlightElement(page, selector, 'Sticky navigation')` to show the persistent nav

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start since the homepage is typically the first page in a demo
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to hero headline, feature cards, and CTA buttons
- `spotlightElement(page, selector, label)` -- Dim everything except testimonials or key stats
- `smoothScroll(page)` -- Scroll through sections naturally to reveal the full page layout
- `slowClick(page, selector)` -- Click the primary CTA with visible cursor movement
- `slowHover(page, selector)` -- Hover over navigation items to reveal dropdowns
- `pause(page, ms)` -- Pause on hero text, feature descriptions, and social proof
- `waitForVisualStability(page)` -- Wait for hero animations, image carousels, and lazy-loaded content to settle

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## Timing
- Allow 3-4 seconds on the hero section so the viewer absorbs the value proposition
- Pause 1-2 seconds after each feature highlight
- Allow 2-3 seconds on testimonials and social proof
- Total part duration should match the estimated duration from the shot list
