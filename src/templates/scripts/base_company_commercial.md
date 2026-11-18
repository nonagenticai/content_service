# Playwright Company Showcase Script Generation

You are generating a Playwright script for a company showcase video. This is a brand story, not a product walkthrough.

## MANDATORY Scrolling Rules

BEFORE highlighting or spotlighting ANY element:
1. Call `await scrollToElement(page, selector)` to scroll it into view
2. Call `await pause(page, 500)` to let the scroll settle
3. THEN call `safeHighlight()` or `spotlightElement()`

NEVER call safeHighlight/spotlightElement on an element without scrolling to it first. Elements below the fold will be INVISIBLE if you skip this step.

## Script Structure

Only import the functions you actually use:

```typescript
import { test } from '@playwright/test';
import { safeHighlight, spotlightElement, enableCursorTrail, smoothScroll, scrollToElement, pause, waitForVisualStability } from '../helpers/demo-helpers';

test.describe('Part Title', () => {
  test('part-id: Part Title', async ({ page }) => {
    await enableCursorTrail(page);

    await test.step('Navigate to page', async () => {
      await page.goto('https://example.com');
      await waitForVisualStability(page);
      await pause(page, 1500);
    });

    // ... more steps ...
  });
});
```

## GOOD EXAMPLE — 30-second company page script

```typescript
import { test } from '@playwright/test';
import { safeHighlight, spotlightElement, enableCursorTrail, smoothScroll, scrollToElement, pause, waitForVisualStability } from '../helpers/demo-helpers';

test.describe('About Company', () => {
  test('ch01-part1: About Company', async ({ page }) => {
    await enableCursorTrail(page);

    await test.step('Navigate', async () => {
      await page.goto('https://example.com/about');
      await waitForVisualStability(page);
      await pause(page, 1500);
    });

    await test.step('Mission statement', async () => {
      await scrollToElement(page, '.about-hero h1');
      await pause(page, 500);
      await spotlightElement(page, '.about-hero', 'Our Mission', 4000);
    });

    await test.step('Team & proof', async () => {
      await smoothScroll(page, { direction: 'down', distance: 600, duration: 2000 });
      await scrollToElement(page, '.team-section');
      await pause(page, 500);
      await safeHighlight(page, '.team-section h2', 'Expert Team', 3000);
    });
  });
});
```

## Recording Constraints

- Viewport: 1920x1080
- Color scheme: light
- Single browser, sequential execution

## Quality Guidelines — Company Showcase

- **Pacing**: 3-4 seconds between actions — let the viewer absorb the design
- **Scrolling**: Use `smoothScroll` with longer durations (2000-3000ms). The page design IS the content.
- **Highlights**: Use `safeHighlight` on proof points — testimonials, certifications, metrics. Linger 4-5 seconds.
- **Spotlight**: Use `spotlightElement` at least once per script for the MOST important section (dims the rest of the page)
- **Navigation**: Always call `waitForVisualStability(page)` after `page.goto()`
- **Cursor**: Call `enableCursorTrail(page)` at the start
- **Resilience**: ALWAYS use `safeHighlight` over `highlightElement`, `safeClick` over `slowClick`
- **Duration**: Keep each part under 90 seconds

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## What to Skip

- Login forms and authentication flows
- Search functionality
- Cookie banners and privacy popups
- Chatbot widgets

## Completion

End your response with: `<promise>COMPLETE</promise>`
