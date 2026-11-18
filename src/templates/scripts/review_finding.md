# Review Finding Script

This script presents review findings — first celebrating strengths, then highlighting growth opportunities.

## Pattern: Strengths First

1. **Navigate** to the page being reviewed
2. **Highlight strengths** — elements that work well (positive labels)
3. **Show opportunities** — areas that could be even better
4. **Pause** to let the viewer absorb the findings

## Strength Highlighting

```typescript
// Celebrate what works well
await scrollToElement(page, '.well-designed-section');
await pause(page, 500);
await highlightElement(page, '.well-designed-section', 'Strong visual hierarchy');
await pause(page, 1500);
```

## Opportunity Highlighting

```typescript
// Then show growth areas positively
await scrollToElement(page, '.improvement-area');
await pause(page, 500);
await highlightElement(page, '.improvement-area', 'Room to enhance user engagement');
await pause(page, 3000);
```

## Feature Suggestions

When showing suggested features:
- Navigate to the page where the feature would live
- Highlight the area where the feature would fit
- Use labels that describe the benefit, not just the feature

```typescript
await highlightElement(page, '.content-area', 'Search feature would help users find content faster');
await pause(page, 3000);
```

## Tone

- Lead with genuine praise: "This section demonstrates strong..."
- Bridge to opportunities: "Building on this strength, consider..."
- Feature suggestions: "Users would benefit from..." (not "The site lacks...")
