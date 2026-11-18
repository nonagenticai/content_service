# Gallery Page Demo Patterns

When recording a gallery or portfolio page, focus on the visual grid, lightbox interactions, and filtering:

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
1. **Navigate** -- Go to the gallery page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the visual content

## Content Exploration
4. **Grid overview** -- Use `pause(page, 3000)` to let the viewer absorb the initial gallery grid layout
5. **Image hover states** -- Use `slowHover(page, selector)` on 2-3 gallery thumbnails to reveal hover effects, titles, or overlay descriptions
6. **Full scroll** -- Use `smoothScroll(page)` to scroll through the entire gallery to show the range and volume of visual content
7. **Category labels** -- Use `highlightElement(page, selector, label)` on category or collection labels if the gallery is organized into sections
8. **Content variety** -- Use `highlightElement(page, selector, label)` on items from different categories to demonstrate the breadth of the portfolio

## Interactive Elements
9. **Open lightbox** -- Use `slowClick(page, selector)` on 2-3 gallery items to open the lightbox or detail view
10. **Navigate lightbox** -- Use `slowClick(page, selector)` on the next/previous arrows within the lightbox to cycle through images, pausing on each
11. **Close lightbox** -- Use `slowClick(page, selector)` on the close button or press Escape to return to the grid
12. **Filter controls** -- Use `slowClick(page, selector)` on filter or category buttons to demonstrate content filtering, and `pause(page, 2000)` to show the filtered results

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the gallery opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to filter controls and category labels
- `spotlightElement(page, selector, label)` -- Dim everything except a featured gallery item or lightbox image
- `smoothScroll(page)` -- Scroll through the full gallery grid to show all content
- `slowClick(page, selector)` -- Click gallery items, lightbox navigation arrows, and filter buttons
- `slowHover(page, selector)` -- Hover over thumbnails to reveal hover states and overlays
- `pause(page, ms)` -- Pause in lightbox view so the viewer can see full-size images
- `waitForVisualStability(page)` -- Wait for lazy-loaded images, lightbox transitions, and filter animations

## Timing
- Allow 3 seconds on the initial grid view for the viewer to absorb the layout
- Pause 2-3 seconds on each lightbox image
- Allow 1-2 seconds between lightbox navigation clicks
- Total part duration should match the estimated duration from the shot list
