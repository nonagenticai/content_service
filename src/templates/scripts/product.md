# Product Page Demo Patterns

When recording a product page, focus on the product presentation, key details, and purchase flow:

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
1. **Navigate** -- Go to the product page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient

## Content Exploration
4. **Product hero** -- Use `highlightElement(page, selector, 'Product image')` on the main product image or gallery
5. **Title and price** -- Use `spotlightElement(page, selector, 'Product name & price')` to draw attention to the product title and pricing
6. **Description** -- Use `smoothScroll(page)` to scroll to the product description, then `pause(page, 2000)` so the viewer can read key details
7. **Features and specs** -- Use `highlightElement(page, selector, label)` on each spec row or feature bullet to walk through the technical details
8. **Customer reviews** -- Scroll to the reviews section and `highlightElement(page, selector, 'Customer reviews')` to show social proof and star ratings

## Interactive Elements
9. **Image gallery** -- Use `slowClick(page, selector)` on thumbnail images to cycle through product photos
10. **Variant selectors** -- Use `slowClick(page, selector)` on size, color, or variant selectors to show available options
11. **Quantity controls** -- Use `highlightElement(page, selector, 'Quantity selector')` on the quantity input
12. **Add to cart CTA** -- Use `slowClick(page, selector)` on the "Add to Cart" or "Buy Now" button to demonstrate the purchase action

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if this product page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to price, specs, variant selectors, and reviews
- `spotlightElement(page, selector, label)` -- Dim everything except the product hero or price block
- `smoothScroll(page)` -- Scroll through description, specs, and reviews naturally
- `slowClick(page, selector)` -- Click image thumbnails, variants, and the add-to-cart CTA
- `slowHover(page, selector)` -- Hover over gallery images to reveal zoom or tooltip details
- `pause(page, ms)` -- Pause on price, key features, and review highlights
- `waitForVisualStability(page)` -- Wait for product images and dynamic pricing to load

## Timing
- Allow 2-3 seconds on the product image and pricing area
- Pause 1-2 seconds on each spec or feature highlight
- Allow 2 seconds after clicking variant selectors for the UI to update
- Total part duration should match the estimated duration from the shot list
