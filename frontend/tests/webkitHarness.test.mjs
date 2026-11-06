// What the suite does differently under WebKit (tests/e2e/webkit.mjs). The
// click after a tap goes only where Playwright's WebKit drops the one iOS
// sends, never where the page stopped the tap or the engine sends its own. A
// page error is let pass only for a load that navigating away cancelled.
import test from "node:test";
import assert from "node:assert/strict";
import { cancelledLoad, clickAfterCancelledTap } from "./e2e/webkit.mjs";

class FakeEvent {
  constructor(type, init) { Object.assign(this, { type, ...init }); }
}

// A window with a target at every point. `tap` plays the order Playwright's
// WebKit sends; `cancel` lists the events the page cancels.
function page() {
  const clicks = [], listeners = [];
  const target = { dispatchEvent: (e) => clicks.push(e) };
  const win = {
    MouseEvent: FakeEvent,
    addEventListener: (type, fn, opts) => listeners.push({ type, fn, capture: !!opts?.capture }),
    document: { elementFromPoint: () => target },
  };
  clickAfterCancelledTap(win);
  const fire = (type, init) => {
    const e = { type, isTrusted: true, defaultPrevented: false, ...init };
    const run = (capture) => listeners.filter((l) => l.type === type && l.capture === capture).forEach((l) => l.fn(e));
    run(true);
    e.defaultPrevented = init.cancelled ?? false;
    run(false);
  };
  const tap = ({ cancel = [], pointerType = "touch", isTrusted = true } = {}) => {
    const cancelled = (type) => cancel.includes(type);
    fire("pointerdown", { pointerType, isTrusted, cancelled: cancelled("pointerdown") });
    fire("touchstart", { isTrusted, cancelled: cancelled("touchstart") });
    fire("touchend", { isTrusted, cancelled: cancelled("touchend"), changedTouches: [{ clientX: 30, clientY: 40 }] });
  };
  return { clicks, tap, fire };
}

test("a tap whose pointerdown was cancelled gets one click where it landed", () => {
  const { clicks, tap } = page();
  tap({ cancel: ["pointerdown"] });
  assert.equal(clicks.length, 1);
  assert.deepEqual([clicks[0].type, clicks[0].clientX, clicks[0].clientY, clicks[0].bubbles, clicks[0].detail], ["click", 30, 40, true, 1]);
});

test("a pointerdown left alone gets none: the engine sends that click", () => {
  const { clicks, tap } = page();
  tap();
  assert.equal(clicks.length, 0);
});

test("a cancelled touchstart or touchend stops the click on iOS, so none is sent", () => {
  for (const cancel of [["pointerdown", "touchstart"], ["pointerdown", "touchend"]]) {
    const { clicks, tap } = page();
    tap({ cancel });
    assert.equal(clicks.length, 0, cancel.join());
  }
});

test("a mouse's press and a test's own dispatched events get none", () => {
  for (const opts of [{ pointerType: "mouse" }, { pointerType: "pen" }, { isTrusted: false }]) {
    const { clicks, tap } = page();
    tap({ cancel: ["pointerdown"], ...opts });
    assert.equal(clicks.length, 0, JSON.stringify(opts));
  }
});

test("each press is answered once, and a press does not carry over to a later touchend", () => {
  const { clicks, tap, fire } = page();
  tap({ cancel: ["pointerdown"] });
  fire("touchend", { changedTouches: [{ clientX: 1, clientY: 1 }] });
  assert.equal(clicks.length, 1);
  tap({ cancel: ["pointerdown"] });
  assert.equal(clicks.length, 2);
});

test("a load cancelled on the page's own origin is not a problem; anything else still is", () => {
  const page = "http://127.0.0.1:45992/?ws=abc";
  // As Playwright hands it over, its "//" gone.
  assert.ok(cancelledLoad("Fetch API cannot load http: /127.0.0.1:45992/api/chats/home due to access control checks.", page));
  assert.ok(cancelledLoad("TypeError: Fetch API cannot load http://127.0.0.1:45992/api/ai/settings due to access control checks.", page));
  assert.ok(!cancelledLoad("Fetch API cannot load https://example.com/a.pdf due to access control checks.", page), "another origin: a real CORS failure");
  assert.ok(!cancelledLoad("TypeError: x is not a function at http://127.0.0.1:45992/assets/a.js", page), "any other error");
  assert.ok(!cancelledLoad("Fetch API cannot load http://127.0.0.1:45992/x due to access control checks.", "about:blank"), "no origin to compare");
});
