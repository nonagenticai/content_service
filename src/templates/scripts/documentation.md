# Documentation Page Demo Patterns

When recording a documentation page, focus on the information architecture, code examples, and navigation:

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
1. **Navigate** -- Go to the documentation page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the docs section

## Content Exploration
4. **Sidebar navigation** -- Use `highlightElement(page, selector, 'Docs sidebar')` on the sidebar to show the documentation structure and hierarchy
5. **Main content** -- Use `highlightElement(page, selector, label)` on the primary content heading to establish the current topic
6. **Code blocks** -- Use `spotlightElement(page, selector, 'Code example')` on code snippets to draw attention to implementation details, and `pause(page, 3000)` for readability
7. **Breadcrumbs** -- Use `highlightElement(page, selector, 'Breadcrumbs')` to show the navigation trail and doc hierarchy
8. **Prev/next links** -- Use `highlightElement(page, selector, 'Page navigation')` on the previous and next page links at the bottom

## Interactive Elements
9. **Search** -- Use `slowClick(page, selector)` on the search input, then `slowType(page, locator, 'example query')` to demonstrate documentation search functionality
10. **Sidebar links** -- Use `slowClick(page, selector)` on 1-2 sidebar links to demonstrate navigation between doc sections
11. **Expandable sections** -- Use `slowClick(page, selector)` on collapsible sections or accordion items to reveal nested content
12. **Copy buttons** -- Use `highlightElement(page, selector, 'Copy code')` on copy-to-clipboard buttons next to code blocks

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if this docs page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to sidebar navigation, code blocks, and breadcrumbs
- `spotlightElement(page, selector, label)` -- Dim everything except a code example or key explanation
- `smoothScroll(page)` -- Scroll through the documentation content to reveal full page
- `slowClick(page, selector)` -- Click sidebar links, search, and expandable sections
- `slowType(page, locator, text)` -- Type a search query to demonstrate the search feature
- `pause(page, ms)` -- Pause on code blocks and important explanations for comprehension
- `waitForVisualStability(page)` -- Wait for syntax highlighting, sidebar loading, and dynamic content

## Timing
- Allow 3 seconds on code blocks so viewers can read the examples
- Pause 1-2 seconds after navigating between doc sections
- Allow 2-3 seconds on search results to show relevance
- Total part duration should match the estimated duration from the shot list
