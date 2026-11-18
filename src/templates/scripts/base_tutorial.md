# Playwright Tutorial Script Generation

You are generating a Playwright tutorial script that will be recorded as an instructional video. The goal is to teach users how to navigate and use this website.

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

    // Step 2: Show where things are
    await test.step('Orient the user', async () => {
      await scrollToElement(page, 'nav.sidebar');
      await pause(page, 500);
      await safeHighlight(page, 'nav.sidebar', 'Navigation Menu');
      await pause(page, 2000);
      await scrollToElement(page, 'button.settings');
      await pause(page, 500);
      await safeHighlight(page, 'button.settings', 'Settings');
      await pause(page, 2000);
    });

    // Step 3: Demonstrate an action
    await test.step('Complete a task', async () => {
      await safeClick(page, 'button.create');
      await waitForVisualStability(page);
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

## Tutorial-Specific Guidelines

- **Slower pacing**: 3-4 seconds between actions so learners can follow along. This is a tutorial, not a highlight reel.
- **Highlight before clicking**: Always `safeHighlight` an element before interacting with it, so viewers know exactly where to look
- **Show cause and effect**: After each click or action, pause to show what changed
- **Complete workflows**: Don't skip steps. If filling a form, fill every required field. If navigating, show the full path.

## Quality Guidelines

- **Pacing**: 3-4 seconds between actions — learners need more time than evaluators
- **Highlights**: Use `safeHighlight` before EVERY interaction to draw attention
- **Navigation**: Always call `waitForVisualStability(page)` after `page.goto()`
- **Cursor**: Call `enableCursorTrail(page)` at the start
- **Scroll**: Use `smoothScroll` instead of instant scrolling
- **Typing**: Use `safeType(page, selector, text)` for form fields
- **Resilience**: ALWAYS use `safeHighlight` over `highlightElement`, `safeClick` over `slowClick`, and `safeType` over `slowType` for any element that might not be present or visible
- **Duration**: Keep each part under 90 seconds

## Duration Budget

Each part has a specific duration target shown in the part details below. Respect it:
- Each `pause(page, ms)` adds real seconds
- Total pause time should be under 40% of the duration budget
- Fewer, well-explained interactions beat many rushed ones
- If the budget is under 30s, limit to 2-3 key interactions

## Instructional Purpose

This tutorial is for users learning to use the product. Every interaction should teach something:

- **Orient first**: Before diving into actions, show where things are with highlights
- **Name everything**: Label UI elements with highlights so users build vocabulary ("the sidebar", "the action bar", "the filter panel")
- **Show the full flow**: Don't skip "obvious" steps — what's obvious to you isn't obvious to a new user
- **Build progressively**: Start with simple actions, then combine them into workflows

## Completion

End your response with: `<promise>COMPLETE</promise>`
