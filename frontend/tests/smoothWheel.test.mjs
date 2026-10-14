import test from "node:test";
import assert from "node:assert/strict";
import { SMOOTH_MS, easeOut, installSmoothWheel, notchDelta } from "../src/shared/lib/smoothWheel.js";

// A scroller 1000 px tall over 5000 px of content, with the frame clock and
// requestAnimationFrame in the test's hands.
function fixture(t, scrollTop = 100) {
  let clock = 0, queued = [];
  const stubs = {
    performance: { now: () => clock },
    requestAnimationFrame: (fn) => { queued.push(fn); return queued.length; },
    cancelAnimationFrame: () => { queued = []; },
    getComputedStyle: () => ({ overflowY: "visible" }),
  };
  const restore = [];
  for (const [key, value] of Object.entries(stubs)) {
    const old = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value, configurable: true, writable: true });
    restore.push(() => old ? Object.defineProperty(globalThis, key, old) : delete globalThis[key]);
  }
  const el = new EventTarget();
  Object.assign(el, { scrollTop, scrollHeight: 5000, clientHeight: 1000, nodeType: 1 });
  const cleanup = installSmoothWheel(el);
  t.after(() => { cleanup(); restore.forEach((fn) => fn()); });
  const wheel = (props) => {
    const e = new Event("wheel", { cancelable: true });
    Object.assign(e, { deltaMode: 0, deltaX: 0, ...props });
    el.dispatchEvent(e);
    return e.defaultPrevented;
  };
  const notch = (dy) => wheel({ deltaY: dy, wheelDeltaY: -120 * Math.sign(dy) * Math.max(1, Math.round(Math.abs(dy) / 100)) });
  // Runs the frames due up to `ms`, one per 16 ms, and the positions they wrote.
  const frames = (ms) => {
    const seen = [];
    for (const end = clock + ms; clock < end && queued.length;) {
      clock += 16;
      const due = queued; queued = [];
      for (const fn of due) fn(clock);
      seen.push(el.scrollTop);
    }
    return seen;
  };
  return { el, wheel, notch, frames, cleanup, pending: () => queued.length };
}

test("only a notch counts: pixel steps of 120, lines and pages, never a touchpad or a modifier", () => {
  assert.equal(notchDelta({ deltaMode: 0, deltaY: 200, wheelDeltaY: -240 }, 1000), 200);
  assert.equal(notchDelta({ deltaMode: 0, deltaY: -100, wheelDeltaY: 120 }, 1000), -100);
  assert.equal(notchDelta({ deltaMode: 1, deltaY: 3 }, 1000), 120, "Firefox: lines");
  assert.equal(notchDelta({ deltaMode: 2, deltaY: 1 }, 1000), 1000, "a page");
  assert.equal(notchDelta({ deltaMode: 0, deltaY: 3.5, wheelDeltaY: -4 }, 1000), 0, "a touchpad");
  assert.equal(notchDelta({ deltaMode: 0, deltaY: 100 }, 1000), 0, "no legacy delta: left alone");
  for (const mod of ["ctrlKey", "metaKey", "shiftKey", "altKey"]) {
    assert.equal(notchDelta({ deltaMode: 0, deltaY: 100, wheelDeltaY: -120, [mod]: true }, 1000), 0, mod);
  }
  assert.equal(notchDelta({ deltaMode: 0, deltaY: 100, deltaX: 5, wheelDeltaY: -120 }, 1000), 0, "sideways");
  assert.equal(easeOut(0), 0);
  assert.equal(easeOut(1), 1);
});

test("a notch eases to its distance over SMOOTH_MS, moving every frame", (t) => {
  const { el, notch, frames, pending } = fixture(t);
  assert.equal(notch(200), true, "the browser's own jump is prevented");
  assert.equal(el.scrollTop, 100, "nothing moves before a frame");
  const seen = frames(SMOOTH_MS + 50);
  assert(seen.length >= SMOOTH_MS / 16, `${seen.length} frames`);
  for (let i = 1; i < seen.length; i++) assert(seen[i] >= seen[i - 1], `monotonic: ${seen}`);
  assert(seen[0] > 100 && seen[0] < 300, "the first frame is part way");
  assert.equal(el.scrollTop, 300);
  assert.equal(pending(), 0, "done: no frame left");
});

test("a notch on the way extends the motion; a touchpad stays native", (t) => {
  const { el, notch, wheel, frames } = fixture(t);
  notch(200);
  frames(48);
  notch(200);
  frames(SMOOTH_MS + 50);
  assert.equal(el.scrollTop, 500);
  assert.equal(wheel({ deltaY: 3.5, wheelDeltaY: -4 }), false);
  assert.equal(wheel({ deltaY: 100, wheelDeltaY: -120, ctrlKey: true }), false, "zoom is viewerZoom's");
});

test("any other scroll ends the motion, and the ends are left to the browser", (t) => {
  const { el, notch, frames, pending } = fixture(t);
  notch(400);
  frames(32);
  el.scrollTop = 2500; // a jump to a highlight, a zoom keeping its anchor, the scrollbar
  frames(SMOOTH_MS);
  assert.equal(el.scrollTop, 2500);
  assert.equal(pending(), 0);
  el.scrollTop = 4000; // the bottom: 5000 - 1000
  assert.equal(notch(200), false, "nothing left to scroll: the browser's overscroll");
  el.scrollTop = 3950;
  notch(200);
  frames(SMOOTH_MS + 50);
  assert.equal(el.scrollTop, 4000, "clamped to the end");
});

test("cleanup stops the motion and the listener", (t) => {
  const { el, notch, frames, cleanup, pending } = fixture(t);
  notch(200);
  frames(32);
  cleanup();
  assert.equal(pending(), 0);
  const at = el.scrollTop;
  assert.equal(notch(200), false);
  frames(SMOOTH_MS);
  assert.equal(el.scrollTop, at);
});
