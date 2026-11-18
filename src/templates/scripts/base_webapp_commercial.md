# Playwright Demo Script Generation

You are generating a Playwright demo script that will be recorded as a video.

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

## Script Structure

```typescript
import { test } from '@playwright/test';
import {
  showChapterTitle,
  highlightElement,
  safeHighlight,
  spotlightElement,
  enableCursorTrail,
  disableCursorTrail,
  smoothScroll,
  scrollToElement,
  slowType,
  safeType,
  slowClick,
  safeClick,
  slowHover,
  pause,
  waitForVisualStability,
  setDemoTheme,
} from '../helpers/demo-helpers';

test.describe('Part Title', () => {
  test('part-id: Part Title', async ({ page }) => {
    await enableCursorTrail(page);

    // Step 1: Navigate
    await test.step('Navigate to page', async () => {
      await page.goto('https://example.com');
      await waitForVisualStability(page);
      await pause(page, 1500);
    });

    // Step 2: Interact
    await test.step('Show key features', async () => {
      await scrollToElement(page, 'selector');
      await pause(page, 500);
      await highlightElement(page, 'selector', 'Label');
      await pause(page, 2000);
    });
  });
});
```

## Available Demo Helpers API

| Function | Description |
|----------|-------------|
| `showChapterTitle(page, { chapter, title, subtitle?, duration? })` | Full-screen gradient title card |
| `highlightElement(page, selector, label?, durationMs?)` | Pulsing border + label on element |
| `spotlightElement(page, selector, label?, durationMs?)` | SVG mask dimming rest of page |
| `enableCursorTrail(page)` | Visible cursor dot with trail |
| `safeType(page, selector, text, { delay?, clear? })` | **Preferred** — safe character-by-character typing (handles `.first()` internally, skips if missing) |
| `slowType(page, locator, text, { delay? })` | Low-level typing — use `safeType` instead for form fields |
| `slowClick(page, selector)` | Deliberate approach → pause → click |
| `slowHover(page, selector)` | Gradual mouse approach |
| `smoothScroll(page, { direction?, distance?, duration? })` | Eased scroll animation |
| `safeHighlight(page, selector, label?, durationMs?)` | Like highlightElement but handles missing/multiple elements gracefully |
| `scrollToElement(page, selector, opts?)` | Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number } |
| `safeClick(page, selector)` | Like slowClick but waits for visibility and skips if element missing |
| `pause(page, ms)` | Fixed delay |
| `waitForVisualStability(page, extraMs?)` | Wait for spinners + network idle |

## Recording Constraints

- Viewport: 1920x1080
- slowMo: as configured in YAML (default 0)
- Color scheme: light
- Single browser, sequential execution

## Hidden & Toggle Elements

When interacting with elements that may be hidden (search bars, dropdowns, mobile menus):
1. First check if the element is visible: `await expect(element).toBeVisible()`
2. If not visible, look for a toggle/trigger button (e.g., search icon, hamburger menu, expand button)
3. Click the trigger first, then wait for the target element to become visible
4. Use `safeType(page, selector, text)` instead of `slowType` for typing — it handles `.first()` internally
5. Never assume form inputs are immediately visible — many sites hide them behind toggles
6. Common patterns: search behind a magnifying glass icon, nav behind a hamburger, filters behind a "Show filters" button
7. **Chatbot widgets** are almost always collapsed by default. Look for a toggle button (chat icon, "?" badge, floating action button) and click it first. Use `safeClick` for the toggle — if it doesn't exist, the chatbot section is gracefully skipped.

## Quality Guidelines

- **Pacing**: 2-3 seconds between actions so viewers can follow
- **Highlights**: Use `highlightElement` before interacting with elements
- **Navigation**: Always call `waitForVisualStability(page)` after `page.goto()`
- **Cursor**: Call `enableCursorTrail(page)` at the start
- **Scroll**: Use `smoothScroll` instead of instant scrolling
- **Typing**: Use `safeType(page, selector, text)` for form fields — it handles `.first()` internally and skips gracefully if the element is missing. Duplicate IDs are common on WordPress/CMS sites, so `safeType` is always safer than `slowType`.
- **Resilience**: ALWAYS use `safeHighlight` over `highlightElement`, `safeClick` over `slowClick`, and `safeType` over `slowType` for any element that might not be present or visible (chatbots, search bars, modals, dropdowns, toggles, cookie banners). Never use raw Playwright `.click()` — always go through `slowClick` or `safeClick`.
- **Duration**: Keep each part under 90 seconds

## Duration Budget

Each part has a specific duration target shown in the part details below. Respect it:
- Each `pause(page, ms)` adds real seconds
- Total pause time should be under 40% of the duration budget
- Fewer, impactful interactions beat many rushed ones
- If the budget is under 30s, limit to 2-3 key interactions

## Narrative Purpose

This demo is for busy professionals evaluating a product or service. Every interaction should serve the story:

- **Lead with value**: Show the most impressive or differentiating element of each page first
- **Linger on proof**: Spend 3-4 seconds highlighting testimonials, metrics, or case study results
- **Rush through chrome**: Cookie banners, footers, empty nav menus — skip or breeze past them
- **Build a sequence**: Each page visit should feel like the next logical step, not a random click
- **End each part with intent**: The last thing the viewer sees should reinforce why this page matters

## Completion

End your response with: `<promise>COMPLETE</promise>`
