# Blog Index Demo Patterns

When recording a blog listing page, focus on content breadth, navigation, and the reading experience:

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
1. **Navigate** -- Go to the blog index URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the blog header

## Content Exploration
4. **Featured post** -- Use `spotlightElement(page, selector, 'Featured article')` on the featured or pinned post if present to draw attention to the top content
5. **Recent posts** -- Use `smoothScroll(page)` to scroll through recent posts, then `highlightElement(page, selector, label)` on 2-3 post titles to show content variety
6. **Post cards** -- Use `highlightElement(page, selector, label)` on post thumbnails, excerpts, and metadata to demonstrate the card layout
7. **Categories and tags** -- Use `highlightElement(page, selector, 'Categories')` on the sidebar categories or tag cloud to show content organization
8. **Content volume** -- Use `smoothScroll(page)` to scroll further down the page to show the depth of the content library

## Interactive Elements
9. **Category filter** -- Use `slowClick(page, selector)` on a category or tag link to demonstrate content filtering
10. **Read a post** -- Use `slowClick(page, selector)` on a featured post card to show the transition to the article view
11. **Pagination** -- Use `slowClick(page, selector)` on pagination controls or trigger infinite scroll to show additional content pages
12. **Search** -- If present, use `slowClick(page, selector)` on the search input and `slowType(page, locator, 'topic')` to demonstrate blog search

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the blog index opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to post titles, categories, and pagination
- `spotlightElement(page, selector, label)` -- Dim everything except the featured post card
- `smoothScroll(page)` -- Browse through the post list naturally to show content breadth
- `slowClick(page, selector)` -- Click category filters, post cards, and pagination
- `slowType(page, locator, text)` -- Type a search query if search is available
- `pause(page, ms)` -- Pause on post titles and excerpts so viewers can read them
- `waitForVisualStability(page)` -- Wait for post images, lazy-loaded content, and filter transitions

## Timing
- Allow 2-3 seconds on the featured post highlight
- Pause 1-2 seconds per post title annotation -- keep them brief to show breadth
- Allow 2 seconds after filtering or navigating to show updated results
- Total part duration should match the estimated duration from the shot list
