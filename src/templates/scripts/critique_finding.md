# Critique Finding Script

This script presents a critique finding as an opportunity for enhancement.

## Pattern

For each finding on this page:

1. **Navigate** to the URL and wait for stability
2. **Scroll** to the element identified by the selector (if available)
3. **Highlight** the element with a positive label describing the opportunity
4. **Pause** to let the viewer absorb the finding

## Finding Card Example

```typescript
await scrollToElement(page, 'img:not([alt])');
await pause(page, 500);
await highlightElement(page, 'img:not([alt])', 'Opportunity: Image descriptions');
await pause(page, 3000);
```

## Grouping Multiple Findings

When multiple findings affect the same page:
- Show them in sequence, highest-impact first
- Use brief transitions between findings (`pause(page, 1000)`)
- End with a pause to let the viewer absorb the findings

## Selector Handling

- If a selector is provided, scroll to it and highlight it
- If the selector matches nothing, use `safeHighlight` (it skips gracefully)
- If no selector is provided, navigate to the page and pause to let the viewer absorb the context

## Tone Reminders

- "This image could benefit from a descriptive alt attribute" (not "missing alt text")
- "Consider adding structured data to enhance search visibility" (not "no structured data found")
- "Strengthening Content-Security-Policy would add an extra layer of protection" (not "CSP header missing")
