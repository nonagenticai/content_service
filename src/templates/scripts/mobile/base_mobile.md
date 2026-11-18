# Mobile App Demo Script Generation

You are generating a **WebdriverIO** test script that records a demo of a mobile app screen.

## Target Platform
- **Platform**: {{ platform }} ({{ platform_display }})
- **Automation**: WebdriverIO + Appium ({{ automation_name }})
- **App**: {{ app_identifier }}

## Output Format

Write a `.test.ts` file using WebdriverIO's `describe/it` pattern:

```typescript
import { tapElement, typeText, swipeScreen, scrollToElement, pause, waitForScreen, assertVisible, goBack } from '../helpers/mobile-demo-helpers';

describe('{{ screen_name }} Demo', () => {
  it('should demonstrate {{ screen_name }}', async () => {
    // Your demo steps here
  });
});
```

## Mobile Demo Helpers API

Available functions from `mobile-demo-helpers.ts`:

- `tapElement(selector: string, description?: string)` — Tap by accessibility ID
- `tapElementBy(strategy: 'xpath' | 'id' | 'accessibility id', value: string, description?: string)` — Tap by custom strategy
- `typeText(selector: string, text: string, charDelay?: number)` — Type character by character
- `swipeScreen(direction: 'up'|'down'|'left'|'right', distance?: number, duration?: number)` — Swipe gesture
- `scrollToElement(selector: string, maxScrolls?: number, direction?: 'up' | 'down')` — Scroll until element visible
- `waitForScreen(timeout?: number)` — Wait for screen stability
- `assertVisible(selector: string, description?: string)` — Assert element visible
- `goBack()` — Press system back button
- `pause(ms: number)` — Wait

## Key Differences from Web Scripts

1. **No DOM, no CSS selectors** — Use accessibility IDs (`~selector`) or XPath
2. **Tap, not click** — Mobile uses touch gestures
3. **Swipe, not scroll** — Use `swipeScreen()` for scrolling content
4. **No cursor trail** — Native apps cannot inject DOM elements
5. **No page URLs** — Navigation is screen-based, not URL-based
6. **Slower transitions** — Add `pause()` between actions for recording clarity

## Guidelines

- Start each demo section with `waitForScreen()` to ensure the screen has loaded
- Use `assertVisible()` before interacting with elements to verify they exist
- Add `pause(1000)` between major demo steps for recording clarity
- Prefer accessibility IDs over XPath for reliable selectors
- Keep demos focused: 30-90 seconds of actions per screen
- End with the screen in a clean state (navigate back if needed)
