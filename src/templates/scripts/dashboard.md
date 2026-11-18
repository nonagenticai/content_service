# Dashboard Demo Patterns

When recording a dashboard or app page, focus on data visualization, key metrics, and interactive controls:

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
1. **Navigate** -- Go to the dashboard URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the dashboard

## Content Exploration
4. **Key metrics** -- Use `highlightElement(page, selector, label)` on each stat card or KPI widget to walk through the top-level numbers
5. **Charts and graphs** -- Use `slowHover(page, selector)` over chart elements to reveal tooltips and data points
6. **Sidebar navigation** -- Use `highlightElement(page, selector, 'Navigation')` on the sidebar to show the app structure
7. **Data tables** -- Use `smoothScroll(page)` to scroll through any data tables, then `pause(page, 2000)` on key rows
8. **Summary panels** -- Use `spotlightElement(page, selector, label)` on summary or status panels to highlight important information

## Interactive Elements
9. **Filters and dropdowns** -- Use `slowClick(page, selector)` on filter dropdowns and date pickers to demonstrate data refinement
10. **Tab navigation** -- Use `slowClick(page, selector)` on tab controls to switch between dashboard views
11. **Action buttons** -- Use `highlightElement(page, selector, label)` on action buttons like export, refresh, or create
12. **Chart interactions** -- Use `slowHover(page, selector)` over chart segments to trigger hover states and tooltip data

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the dashboard opens a new chapter in the demo
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to KPIs, navigation items, and action buttons
- `spotlightElement(page, selector, label)` -- Dim everything except a key chart or metric panel
- `smoothScroll(page)` -- Scroll through data tables and below-fold dashboard sections
- `slowClick(page, selector)` -- Click filters, tabs, and dropdowns with visible cursor movement
- `slowHover(page, selector)` -- Hover over charts to reveal tooltips and data detail
- `pause(page, ms)` -- Pause on key metrics and chart tooltips for viewer comprehension
- `waitForVisualStability(page)` -- Wait for chart animations, data loading, and dynamic content to settle

## Timing
- Allow 2-3 seconds per metric card or KPI highlight
- Pause 2 seconds after hovering on chart elements so tooltips are visible
- Allow 1-2 seconds between filter interactions for the UI to update
- Total part duration should match the estimated duration from the shot list
