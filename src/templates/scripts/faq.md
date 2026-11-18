# FAQ Page Demo Patterns

When recording a FAQ page, focus on the question structure, answer reveal interactions, and support pathways:

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
1. **Navigate** -- Go to the FAQ page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the FAQ structure

## Content Exploration
4. **Page heading** -- Use `highlightElement(page, selector, 'FAQ section')` on the page title to establish context
5. **Question categories** -- If the FAQ is organized by topic, use `smoothScroll(page)` to scroll through category headings and `highlightElement(page, selector, label)` on each category name
6. **Answer content** -- After expanding an item, use `pause(page, 3000)` so the viewer can read the answer text fully
7. **Contact CTA** -- Use `highlightElement(page, selector, 'Still have questions?')` on any "contact support" or "still have questions?" link at the bottom
8. **Full scope** -- Use `smoothScroll(page)` to scroll through the full FAQ list to show the breadth of topics covered

## Interactive Elements
9. **Expand accordion items** -- Use `slowClick(page, selector)` on 2-3 accordion items to reveal their answers, pausing after each to let the viewer read
10. **Collapse items** -- Use `slowClick(page, selector)` to close an expanded item before opening the next, showing the accordion behavior
11. **Search or filter** -- If a search box or filter exists, use `slowClick(page, selector)` on the input and `slowType(page, locator, 'keyword')` to demonstrate filtering
12. **Support link** -- Use `highlightElement(page, selector, 'Contact support')` on the support link but do not navigate away

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the FAQ page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to category headings, the search input, and support links
- `spotlightElement(page, selector, label)` -- Dim everything except an expanded FAQ answer
- `smoothScroll(page)` -- Scroll through category sections to show the full question list
- `slowClick(page, selector)` -- Click accordion items to expand and collapse answers
- `slowType(page, locator, text)` -- Type a search query if FAQ search is available
- `pause(page, ms)` -- Pause after expanding each FAQ item so viewers can read the answer
- `waitForVisualStability(page)` -- Wait for accordion animations and search filter transitions

## Timing
- Allow 3 seconds after expanding each FAQ item for the viewer to read the answer
- Pause 1-2 seconds between closing one item and opening the next
- Allow 2 seconds on the search results if demonstrating search
- Total part duration should match the estimated duration from the shot list
