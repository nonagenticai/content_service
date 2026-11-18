# Product Listing Demo Patterns

When recording a product listing or catalog page, focus on browsing, filtering, and the path to product detail:

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
1. **Navigate** -- Go to the product listing URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the catalog header and product count

## Content Exploration
4. **Catalog header** -- Use `highlightElement(page, selector, 'Product count')` on the page header showing the total number of products or the category title
5. **Product cards** -- Use `smoothScroll(page)` to scroll through the product grid, then `highlightElement(page, selector, label)` on 3-4 product cards to show names, prices, and thumbnails
6. **Filter sidebar** -- Use `highlightElement(page, selector, 'Filters')` on the filter sidebar or controls to show the available refinement options
7. **Sorting options** -- Use `highlightElement(page, selector, 'Sort by')` on the sort dropdown to show ordering options
8. **Pagination** -- Use `smoothScroll(page)` to the bottom and `highlightElement(page, selector, 'Pagination')` on pagination controls or load-more buttons

## Interactive Elements
9. **Apply a filter** -- Use `slowClick(page, selector)` on a filter checkbox or category link to demonstrate catalog refinement, then `pause(page, 2000)` to show updated results
10. **Change sorting** -- Use `slowClick(page, selector)` on the sort dropdown and select an option to reorder products
11. **Open product detail** -- Use `slowClick(page, selector)` on one product card to navigate to the detail view
12. **Back to listing** -- Use `pause(page, 1500)` when navigating back to the listing page

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the product listing opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to the filter sidebar, product cards, prices, and pagination
- `spotlightElement(page, selector, label)` -- Dim everything except a featured product card or the filter panel
- `smoothScroll(page)` -- Browse through the product grid naturally so viewers can see names and prices
- `slowClick(page, selector)` -- Click filters, sort options, product cards, and pagination
- `slowHover(page, selector)` -- Hover over product cards to reveal quick-view or hover details
- `pause(page, ms)` -- Pause on product cards and after filter changes to show updated results
- `waitForVisualStability(page)` -- Wait for product images, filter transitions, and sort animations

## Visual Variety (REQUIRED)

Do NOT use only `safeHighlight` for everything. Mix these effects:
- Use `spotlightElement` for the MOST important section (dims the rest of the page)
- Use `safeHighlight` for secondary elements
- Use `smoothScroll` with longer durations (2000ms) to let viewers absorb the design
- Use `pause` to give viewers time to absorb what they are seeing

Each script MUST use at least 2 different visual effects (not just safeHighlight repeatedly).

## Timing
- Allow 2-3 seconds per product card highlight so viewers can read names and prices
- Pause 2 seconds after applying filters to show the updated catalog
- Allow 1-2 seconds after sorting to show the reordered results
- Total part duration should match the estimated duration from the shot list
