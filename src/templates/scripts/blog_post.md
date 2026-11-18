# Blog Post Demo Patterns

When recording a blog post, focus on the article content, readability, and related content:

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
1. **Navigate** -- Go to the blog post URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the content type

## Content Exploration
4. **Title and metadata** -- Use `highlightElement(page, selector, 'Article title')` on the headline, then `highlightElement(page, selector, 'Author & date')` on the author name, publish date, and reading time
5. **Featured image** -- Use `spotlightElement(page, selector, 'Featured image')` on the hero image if present, and `pause(page, 2000)` to let the viewer see it
6. **Article body** -- Use `smoothScroll(page)` to scroll through the article at a readable pace, pausing on key paragraphs and subheadings
7. **Inline images and media** -- Use `pause(page, 2000)` on embedded images, videos, or infographics within the article body
8. **Related posts** -- Scroll to the related posts section and `highlightElement(page, selector, 'Related articles')` to show content recommendations

## Interactive Elements
9. **Table of contents** -- If present, use `slowClick(page, selector)` on a TOC link to demonstrate anchor navigation
10. **Social share buttons** -- Use `highlightElement(page, selector, 'Share buttons')` on social sharing icons
11. **Comment section** -- Use `smoothScroll(page)` to scroll to comments and `highlightElement(page, selector, 'Comments')` to show engagement
12. **Tags and categories** -- Use `slowHover(page, selector)` over tag links to show content categorization

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if this blog post opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to the headline, metadata, images, and related posts
- `spotlightElement(page, selector, label)` -- Dim everything except the featured image or a key quote
- `smoothScroll(page)` -- Scroll through the article body at a natural reading pace
- `slowClick(page, selector)` -- Click TOC links or related post cards
- `slowHover(page, selector)` -- Hover over tags and share buttons
- `pause(page, ms)` -- Pause on images, key paragraphs, and pull quotes for viewer reading
- `waitForVisualStability(page)` -- Wait for lazy-loaded images and embedded media to render

## Timing
- Allow 3-4 seconds on the article title and metadata
- Pause 2 seconds on each inline image or media embed
- Scroll the article body slowly -- viewers should be able to read subheadings
- Total part duration should match the estimated duration from the shot list
