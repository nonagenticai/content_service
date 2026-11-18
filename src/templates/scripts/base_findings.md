# Findings Video Script Generation

You are generating a Playwright script that records a findings video — a positive, constructive walkthrough of opportunities for improvement on a website.

## MANDATORY Scrolling Rules

BEFORE highlighting or spotlighting ANY element:
1. Call `await scrollToElement(page, selector)` to scroll it into view
2. Call `await pause(page, 500)` to let the scroll settle
3. THEN call `highlightElement()` or `spotlightElement()`

NEVER call highlightElement/spotlightElement on an element without scrolling to it first.

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
  slowClick,
  safeClick,
  pause,
  waitForVisualStability,
  setDemoTheme,
} from '../helpers/demo-helpers';

test.describe('Part Title', () => {
  test('part-id: Part Title', async ({ page }) => {
    await enableCursorTrail(page);

    // Step 1: Navigate to the page
    await test.step('Navigate to page', async () => {
      await page.goto('https://example.com');
      await waitForVisualStability(page);
    });

    // Step 2: Highlight the relevant element
    await test.step('Highlight opportunity', async () => {
      await scrollToElement(page, '.target-element');
      await pause(page, 500);
      await highlightElement(page, '.target-element', 'Area of opportunity');
    });

    // Step 3: Pause for viewer to absorb the recommendation
    await test.step('Show recommendation', async () => {
      await pause(page, 3000);
    });
  });
});
```

## Key Visual Pattern for Findings

For each finding, follow this sequence:
1. **Navigate** to the page containing the finding
2. **Scroll** to the specific element using `scrollToElement()`
3. **Highlight** the element using `highlightElement()` or `spotlightElement()`
4. **Pause** using `pause(page, 3000)` to let the viewer absorb the finding

## Quality Guidelines

- **Pacing**: 2-3 seconds between actions so viewers can absorb the recommendation
- **Highlights**: Always scroll to elements before highlighting
- **Navigation**: Always call `waitForVisualStability(page)` after `page.goto()`
- **Cursor**: Call `enableCursorTrail(page)` at the start
- **Resilience**: Use `safeHighlight` for elements that might not be present

## Duration Budget

Each part has a specific duration target shown in the part details below. Respect it:
- Each `pause(page, ms)` adds real seconds
- Fewer, clear findings beat many rushed ones

## Completion

End your response with: `<promise>COMPLETE</promise>`
