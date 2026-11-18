# Generic Page Demo Patterns

For pages that don't fit a specific category, follow this comprehensive approach:

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
1. **Navigate** — Go to the page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** — Call `enableCursorTrail(page)` for visual polish
3. **Pause** — Use `pause(page, 1500)` to let the viewer orient

## Content Exploration
4. **Scroll overview** — Use `smoothScroll(page)` to reveal the full page layout
5. **Highlight key sections** — Use `highlightElement(page, selector, label)` on important headings or content blocks
6. **Pause on key content** — Call `pause(page, 2000)` on important sections to let the viewer read

## Interactive Elements
7. **Buttons & links** — Use `slowClick(page, selector)` on primary CTAs or navigation links
8. **Form fields** — If forms exist, use `slowType(page, locator, 'example text')` to demonstrate input
9. **Dropdowns & menus** — Use `slowHover(page, selector)` to reveal hover states and dropdowns

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` — Use at the start if this is the first page in a chapter
- `scrollToElement(page, selector, opts?)` — Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` — Draw attention to specific UI elements
- `spotlightElement(page, selector, label)` — Dim everything except the target element
- `smoothScroll(page)` — Scroll the page smoothly for content reveal
- `slowClick(page, selector)` — Click with visible cursor movement
- `slowHover(page, selector)` — Hover with visible cursor movement
- `pause(page, ms)` — Fixed delay for viewer comprehension
- `waitForVisualStability(page)` — Wait for network idle and animations to settle

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## Timing
- Allow 2-3 seconds between major actions
- Pause 1-2 seconds after annotations appear
- Total part duration should match the estimated duration from the shot list
