> **SAFETY: Always use demo-safe placeholder data (e.g., "Alex Johnson", "alex@example.com").
> Never enter real credentials, personal information, or production data during recording.**

# Contact Page Demo Patterns

When recording a contact page, focus on the available contact methods, form interaction, and location details:

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
1. **Navigate** -- Go to the contact page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the contact options

## Content Exploration
4. **Contact information** -- Use `highlightElement(page, selector, label)` on the address, phone number, and email to show the primary contact details
5. **Form layout** -- Use `highlightElement(page, selector, 'Contact form')` to show the overall form structure and available fields
6. **Map embed** -- Use `smoothScroll(page)` to scroll to the map embed if present, then `spotlightElement(page, selector, 'Office location')` to highlight the location
7. **Social links** -- Use `highlightElement(page, selector, 'Social channels')` on social media links or alternative contact methods
8. **Business hours** -- If displayed, use `highlightElement(page, selector, 'Business hours')` to show availability information

## Interactive Elements
9. **Form name field** -- Use `slowType(page, locator, 'Alex Johnson')` to demonstrate name input
10. **Form email field** -- Use `slowType(page, locator, 'alex@example.com')` to demonstrate email input
11. **Form message field** -- Use `slowType(page, locator, 'I would like to learn more about your services.')` to show message composition
12. **Submit button** -- Use `highlightElement(page, selector, 'Submit')` on the submit button to annotate it, but do NOT click it -- never submit real forms during demos

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the contact page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to contact details, form fields, and the submit button
- `spotlightElement(page, selector, label)` -- Dim everything except the map embed or form area
- `smoothScroll(page)` -- Scroll to the map and social links sections
- `slowType(page, locator, text)` -- Fill in form fields with placeholder data to show the form in action
- `slowClick(page, selector)` -- Click form field focus but avoid clicking submit
- `pause(page, ms)` -- Pause on contact details and after form field entry
- `waitForVisualStability(page)` -- Wait for map embed, form validation animations, and page load

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## Timing
- Allow 2-3 seconds on contact information (address, phone, email)
- Pause 1-2 seconds between each form field entry
- Allow 2 seconds on the map embed for the viewer to see the location
- Total part duration should match the estimated duration from the shot list
