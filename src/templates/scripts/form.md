> **SAFETY: Always use demo-safe placeholder data (e.g., "Alex Johnson", "alex@example.com").
> Never enter real credentials, personal information, or production data during recording.**

# Form Page Demo Patterns

When recording a form or signup page, focus on the form layout, field walkthrough, and demo-safe data entry. Never submit the form or enter real credentials:

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
1. **Navigate** -- Go to the form page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the form purpose

## Content Exploration
4. **Form overview** -- Use `highlightElement(page, selector, 'Form')` on the overall form container to show the layout and available fields
5. **Form heading** -- Use `highlightElement(page, selector, label)` on any heading or description text above the form to explain its purpose
6. **Required indicators** -- Use `highlightElement(page, selector, 'Required fields')` on required field markers or helper text if visible
7. **Terms & privacy** -- If terms of service or privacy policy links are present, use `highlightElement(page, selector, label)` to show them

## Interactive Elements
8. **Field walkthrough** -- For each important form field:
   - Use `highlightElement(page, selector, label)` to draw attention to the field
   - Click into the field
   - Use `safeType(page, selector, text)` to enter demo-safe placeholder data
   - Call `pause(page, 1500)` between fields so the viewer can follow
9. **Dropdowns & selects** -- Use `safeClick(page, selector)` on any dropdown or select fields to reveal options, then select one
10. **Checkboxes & radios** -- Use `safeClick(page, selector)` on relevant checkboxes or radio buttons to demonstrate selection
11. **Validation states** -- If visible after interaction, use `highlightElement(page, selector, 'Validation')` on validation messages (success or error states)
12. **Submit button** -- Use `highlightElement(page, selector, 'Submit')` on the submit button to annotate it, but do NOT click it -- never submit forms during demos

## Demo Data Suggestions
Use these placeholder values when filling form fields:
- Name: "Alex Johnson"
- Email: "alex@example.com"
- Company: "Acme Corp"
- Phone: "(555) 123-4567"
- Address: "123 Demo Street, San Francisco, CA 94105"
- Message: "I would like to learn more about your product."

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the form page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to form fields, labels, validation states, and the submit button
- `spotlightElement(page, selector, label)` -- Dim everything except the form container or a specific field group
- `smoothScroll(page)` -- Scroll to reveal form fields below the fold
- `safeType(page, selector, text)` -- **Preferred** — fill in form fields with demo-safe placeholder data (handles `.first()` internally, skips if missing)
- `slowType(page, locator, text)` -- Low-level typing; use `safeType` instead for form fields
- `safeClick(page, selector)` -- **Preferred** — click dropdowns, checkboxes, and radio buttons (waits for visibility, skips if missing)
- `slowClick(page, selector)` -- Low-level click; use `safeClick` instead for elements that may not be present
- `pause(page, ms)` -- Pause after each field entry so the viewer can follow along
- `waitForVisualStability(page)` -- Wait for form validation animations, conditional field rendering, and page load

## Timing
- Allow 2-3 seconds per form field (highlight, type, pause)
- Pause 1-2 seconds after revealing dropdown options before selecting
- Allow 2 seconds on validation messages so the viewer can read them
- Total part duration should match the estimated duration from the shot list
