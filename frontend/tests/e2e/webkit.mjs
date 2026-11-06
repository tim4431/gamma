// What the suite does differently under GAMMA_E2E_BROWSER=webkit
// (docs/dev/debugging.md "WebKit and Firefox"). No Playwright here:
// tests/webkitHarness.test.mjs runs this in node.
//
// Playwright's tap is its own patch to WebKit (WebPage::fakeTouchTap), not a
// finger: it sends touchstart and touchend, and the mouse events and the
// click only if neither was handled, a cancelled pointerdown counting as
// handled. iOS sends the click: WebKit's own test
// pointerevents/ios/pointer-events-prevent-default-allows-click-event.html
// requires it. The editing bar and the window grips cancel pointerdown to
// keep the focus and run from the click, so without this no tap on them does
// anything under GAMMA_E2E_BROWSER=webkit.
//
// The harness sends that click, and nothing else: a press whose pointerdown
// was cancelled, whose touchstart and touchend were not. No mouse events
// come with it, as on iOS, where a cancelled pointerdown stops those. It
// goes out from inside touchend, so a test's next line finds it landed. So a
// WebKit run shows what the app does with the click, and says nothing about
// whether an iPad sends it: that is device check 36 in docs/dev/ipad.md.
// touch.mjs's first step fails once Playwright sends the click itself, as
// each tap on the bar would then run twice.

// `addInitScript` sends this as source, so it reads nothing from outside.
export function clickAfterCancelledTap(win = window) {
  let down = null, start = null;
  // Kept from the capture phase: a listener lower down may stop the event,
  // and an event still answers `defaultPrevented` after it has gone by.
  win.addEventListener("pointerdown", (e) => { down = e; }, { capture: true, passive: true });
  win.addEventListener("touchstart", (e) => { start = e; }, { capture: true, passive: true });
  // Last on the way up, after the page's own touchend listeners.
  win.addEventListener("touchend", (e) => {
    const press = down;
    down = null;
    if (!e.isTrusted || !press?.isTrusted || press.pointerType !== "touch") return;
    if (!press.defaultPrevented || start?.defaultPrevented || e.defaultPrevented) return;
    const { clientX, clientY } = e.changedTouches[0];
    win.document.elementFromPoint(clientX, clientY)?.dispatchEvent(new win.MouseEvent("click", {
      bubbles: true, cancelable: true, composed: true, view: win, detail: 1, button: 0, clientX, clientY,
    }));
  }, { passive: true });
}

// An iPad reports five touch points, Playwright's WebKit none, so
// `navigator.maxTouchPoints > 0` code (touchScreen(), CodeMirror's
// browser.ios) would take it for a desktop.
export function touchPoints() {
  Object.defineProperty(Navigator.prototype, "maxTouchPoints", { get: () => 5, configurable: true });
}

// WebKit rejects the fetches a page still has open as it navigates, which
// shows as an unhandled rejection on the page being left, in the words of a
// CORS failure whatever the URL. Chromium drops such a fetch silently. A
// page's own origin cannot fail an access control check, so that message for
// it is a cancelled load, not a problem. Another origin's still counts.
export function cancelledLoad(text, pageUrl) {
  let host = "";
  try { host = new URL(pageUrl).host; } catch { return false; }
  return !!host && /Fetch API cannot load .* due to access control checks/.test(text) && text.includes(host);
}

// What a run under WebKit prints, and what it does and does not show.
export const WEBKIT_TOUCH_NOTE = "(WebKit's touch contexts: Playwright's tap sends no click after a cancelled pointerdown and reports no touch points, so the harness supplies both. A pass shows what the app does with the click, not that an iPad sends it: device check 36 in docs/dev/ipad.md)";
