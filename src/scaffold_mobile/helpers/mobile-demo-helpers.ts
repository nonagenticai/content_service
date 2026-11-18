/**
 * Mobile demo helper functions for WebdriverIO-based app recordings.
 *
 * These provide gesture-based interactions with visual feedback for
 * demo recording. Unlike web demo-helpers, native apps cannot inject
 * DOM overlays — visual annotations are added via ffmpeg post-processing.
 */

/** Pause execution for the given number of milliseconds. */
export async function pause(ms: number): Promise<void> {
  await browser.pause(ms);
}

/** Tap an element identified by accessibility ID. */
export async function tapElement(
  selector: string,
  description?: string,
): Promise<void> {
  const el = await $(`~${selector}`);
  await el.waitForDisplayed({ timeout: 10000 });
  await el.click();
  if (description) {
    console.log(`[demo] Tapped: ${description}`);
  }
  await pause(500);
}

/** Tap an element identified by a custom selector strategy. */
export async function tapElementBy(
  strategy: 'xpath' | 'id' | 'accessibility id',
  value: string,
  description?: string,
): Promise<void> {
  const el = await $(
    strategy === 'accessibility id'
      ? `~${value}`
      : strategy === 'id'
        ? `id=${value}`
        : value, // xpath: passed as-is
  );
  await el.waitForDisplayed({ timeout: 10000 });
  await el.click();
  if (description) {
    console.log(`[demo] Tapped: ${description}`);
  }
  await pause(500);
}

/** Type text into an input field character by character. */
export async function typeText(
  selector: string,
  text: string,
  charDelay: number = 50,
): Promise<void> {
  const el = await $(`~${selector}`);
  await el.waitForDisplayed({ timeout: 10000 });
  await el.click();

  for (const char of text) {
    await el.addValue(char);
    await pause(charDelay);
  }
  console.log(`[demo] Typed ${text.length} chars into ${selector}`);
}

/** Swipe in a direction across the screen. */
export async function swipeScreen(
  direction: 'up' | 'down' | 'left' | 'right',
  distance: number = 0.5,
  duration: number = 800,
): Promise<void> {
  const { width, height } = await browser.getWindowSize();
  const centerX = width / 2;
  const centerY = height / 2;

  let startX = centerX;
  let startY = centerY;
  let endX = centerX;
  let endY = centerY;

  const swipeDistance = Math.min(width, height) * distance;

  switch (direction) {
    case 'up':
      startY = centerY + swipeDistance / 2;
      endY = centerY - swipeDistance / 2;
      break;
    case 'down':
      startY = centerY - swipeDistance / 2;
      endY = centerY + swipeDistance / 2;
      break;
    case 'left':
      startX = centerX + swipeDistance / 2;
      endX = centerX - swipeDistance / 2;
      break;
    case 'right':
      startX = centerX - swipeDistance / 2;
      endX = centerX + swipeDistance / 2;
      break;
  }

  await browser.action('pointer', { parameters: { pointerType: 'touch' } })
    .move({ x: Math.round(startX), y: Math.round(startY) })
    .down()
    .pause(50)
    .move({ x: Math.round(endX), y: Math.round(endY), duration })
    .up()
    .perform();

  console.log(`[demo] Swiped ${direction}`);
  await pause(500);
}

/** Scroll until an element is visible. */
export async function scrollToElement(
  selector: string,
  maxScrolls: number = 10,
  direction: 'up' | 'down' = 'down',
): Promise<void> {
  for (let i = 0; i < maxScrolls; i++) {
    const el = await $(`~${selector}`);
    if (await el.isDisplayed()) {
      console.log(`[demo] Found ${selector} after ${i} scrolls`);
      return;
    }
    await swipeScreen(direction, 0.3, 600);
  }
  console.log(`[demo] Element ${selector} not found after ${maxScrolls} scrolls`);
}

/** Wait for the screen to stabilize (no layout changes).
 *  Compares a fingerprint of the XML source (stripping dynamic bounds attributes)
 *  to avoid false negatives from constantly changing element coordinates. */
export async function waitForScreen(timeout: number = 5000): Promise<void> {
  const startTime = Date.now();
  let prevFingerprint = '';

  const fingerprint = (source: string): string =>
    source.replace(/bounds="[^"]*"/g, '').replace(/\s+/g, ' ').trim();

  while (Date.now() - startTime < timeout) {
    const source = await browser.getPageSource();
    const fp = fingerprint(source);
    if (fp === prevFingerprint && prevFingerprint !== '') {
      return;
    }
    prevFingerprint = fp;
    await pause(500);
  }
}

/** Assert an element is visible on screen. */
export async function assertVisible(
  selector: string,
  description?: string,
): Promise<void> {
  const el = await $(`~${selector}`);
  await el.waitForDisplayed({ timeout: 10000 });
  console.log(`[demo] Verified visible: ${description || selector}`);
}

/** Navigate back (press system back button). */
export async function goBack(): Promise<void> {
  await browser.back();
  await pause(500);
}
