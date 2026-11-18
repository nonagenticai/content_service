# Login Page Demo Patterns

When recording a login page, focus on the form layout, authentication options, and account recovery paths. Never enter real credentials:

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
1. **Navigate** -- Go to the login page URL and call `waitForVisualStability(page)` after load
2. **Enable cursor trail** -- Call `enableCursorTrail(page)` for visual polish
3. **Pause** -- Use `pause(page, 1500)` to let the viewer orient on the authentication flow

## Content Exploration
4. **Form layout** -- Use `highlightElement(page, selector, 'Login form')` on the overall form to show the authentication interface
5. **Branding** -- Use `highlightElement(page, selector, label)` on any logo, welcome message, or branding element above the form
6. **Social login options** -- Use `highlightElement(page, selector, 'Social login')` on social login buttons (Google, GitHub, SSO) to show alternative authentication methods
7. **Account links** -- Use `highlightElement(page, selector, 'Forgot password')` on the forgot password link and `highlightElement(page, selector, 'Sign up')` on the create account link
8. **Security indicators** -- Use `highlightElement(page, selector, label)` on any security badges, SSL indicators, or trust signals

## Interactive Elements
9. **Email field** -- Use `safeType(page, selector, 'alex@example.com')` to demonstrate the email input -- never use real credentials
10. **Password field** -- Use `safeType(page, selector, '********')` to show the password field with masked input -- never use real passwords
11. **Show/hide password** -- If a password visibility toggle exists, use `safeClick(page, selector)` to demonstrate the toggle
12. **Submit button** -- Use `highlightElement(page, selector, 'Sign in')` on the submit button to annotate it, but do NOT click it -- never submit login forms during demos

## Recommended Demo Helpers
- `showChapterTitle(page, opts)` -- Use at the start if the login page opens a new chapter
- `scrollToElement(page, selector, opts?)` -- Scrolls smoothly to bring the element into view. Returns true if found. Options: { offset?: number, duration?: number }
- `highlightElement(page, selector, label)` -- Draw attention to form fields, social buttons, and the forgot password link
- `spotlightElement(page, selector, label)` -- Dim everything except the login form or social login section
- `smoothScroll(page)` -- Scroll only if the page has content below the fold (e.g., trust badges)
- `safeType(page, selector, text)` -- **Preferred** — type placeholder credentials into form fields (handles `.first()` internally, skips if missing)
- `slowType(page, locator, text)` -- Low-level typing; use `safeType` instead
- `safeClick(page, selector)` -- **Preferred** — click the password visibility toggle (waits for visibility, skips if missing)
- `slowClick(page, selector)` -- Low-level click; use `safeClick` instead
- `pause(page, ms)` -- Pause after typing in each field so the viewer can see the input
- `waitForVisualStability(page)` -- Wait for form validation animations and social button rendering

## Timing
- Allow 2 seconds after typing in each form field
- Pause 1-2 seconds when highlighting social login options
- Allow 2-3 seconds on the overall form layout annotation
- Total part duration should match the estimated duration from the shot list
