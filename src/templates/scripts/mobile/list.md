<!-- base_instructions is substituted from base_mobile.md by the generator.
     If this placeholder is visible in the rendered prompt, the generator
     failed to inject the base content. -->
# List Screen Demo

{{ base_instructions }}

## Screen: List / Feed

Demonstrate a scrollable list or feed:

1. **Initial View** — Pause to show the first visible items
2. **Scroll** — Swipe up to reveal more content
3. **Item Interaction** — Tap an item to show detail navigation
4. **Return** — Navigate back to the list

## Screen Data

{{ screen_data }}

## Interactive Elements

{{ interactive_elements }}

## Notes

- Use `swipeScreen('up', 0.4)` for smooth scrolling
- Pause 1-2 seconds between scrolls to let content be visible
- Select a representative item to demonstrate the tap-to-detail flow
