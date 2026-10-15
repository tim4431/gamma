import test from "node:test";
import assert from "node:assert/strict";
import { SMOOTH_MS, approach, installSmoothWheel, notchDelta, wholeNotches } from "../src/shared/lib/smoothWheel.js";

// A scroller 1000 px tall over 5000 px of content, with the frame clock and
// requestAnimationFrame in the test's hands.
function fixture(t, scrollTop = 100, devicePixelRatio = 1) {
  let clock = 0, queued = [];
  const stubs = {
    devicePixelRatio,
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
  // The motion's full length, tail included.
  const settle = () => frames(SMOOTH_MS * 4);
  return { el, wheel, notch, frames, settle, cleanup, pending: () => queued.length };
}

test("only a notch counts: whole notches in pixels, lines and pages, never a touchpad or a modifier", () => {
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
  assert.equal(approach(0), 0);
  assert(approach(SMOOTH_MS) > 0.94 && approach(SMOOTH_MS) < 0.96, "95% of the way in SMOOTH_MS");
});

// Chromium reports wheelDeltaY as trunc(-notches × 120 / devicePixelRatio),
// and devicePixelRatio carries the page zoom and the display's scale.
test("a notch is a notch at any zoom and display scale; a fraction of one never", () => {
  // What Chromium sent at these ratios (Playwright, one notch).
  for (const [dpr, deltaY, legacy] of [[1.100000023841858, 90.90908893868948, -109], [1.25, 80, -96], [1.5, 66.66666666666667, -80], [2, 50, -60]]) {
    assert.equal(notchDelta({ deltaMode: 0, deltaY, wheelDeltaY: legacy }, 1000, dpr), deltaY, `dpr ${dpr}`);
  }
  assert.equal(notchDelta({ deltaMode: 0, deltaY: 100, wheelDeltaY: -120 }, 1000, 1.1), 100, "WebKit: 120 at any ratio");
  const zooms = [0.25, 1 / 3, 0.5, 2 / 3, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];
  for (const scale of [1, 1.25, 1.5, 1.75, 2, 2.25, 3]) {
    for (const zoom of zooms) {
      const dpr = Math.fround(zoom * scale);
      for (const n of [1, 2, 3, -1, -4]) {
        assert(wholeNotches(Math.trunc(n * 120 / dpr), dpr), `${n} notches at zoom ${zoom}, scale ${scale}`);
      }
      // A precise wheel's eighths and a touchpad's few pixels of a notch.
      for (const part of [1 / 8, 3 / 8, 0.5, 5 / 6, 7 / 8, 1.5, 0.05]) {
        const legacy = Math.trunc(part * 120 / dpr);
        if (legacy) assert(!wholeNotches(legacy, dpr), `${part} of a notch at zoom ${zoom}, scale ${scale}`);
      }
    }
  }
});

test("a notch glides to its distance, most of it within SMOOTH_MS, moving every frame", (t) => {
  const { el, notch, frames, settle, pending } = fixture(t);
  assert.equal(notch(200), true, "the browser's own jump is prevented");
  assert.equal(el.scrollTop, 100, "nothing moves before a frame");
  const seen = frames(SMOOTH_MS);
  assert(seen.length >= SMOOTH_MS / 16 - 1, `${seen.length} frames`);
  for (let i = 1; i < seen.length; i++) assert(seen[i] > seen[i - 1], `moves every frame: ${seen}`);
  assert(seen[0] > 100 && seen[0] < 300, "the first frame is part way");
  assert(el.scrollTop > 285, `most of the way in SMOOTH_MS: ${el.scrollTop}`);
  settle();
  assert.equal(el.scrollTop, 300);
  assert.equal(pending(), 0, "done: no frame left");
});

// Chromium delivers a wheel event inside the frame it belongs to and stamps
// that frame's animation callbacks with the frame's start, before the event.
// A spin faster than the frames must still move in every frame.
test("a notch in every frame of a spin never stops the motion", (t) => {
  const { el, notch, frames, settle } = fixture(t);
  const seen = [];
  for (let i = 0; i < 12; i++) {
    notch(200);
    seen.push(...frames(16));
  }
  for (let i = 1; i < seen.length; i++) assert(seen[i] > seen[i - 1], `moves every frame: ${seen}`);
  settle();
  assert.equal(el.scrollTop, 100 + 12 * 200);
});

test("at 110% zoom a notch glides like any other; a touchpad stays native", (t) => {
  const { el, wheel, frames, settle } = fixture(t, 100, 1.100000023841858);
  assert.equal(wheel({ deltaY: 181.81817787737896, wheelDeltaY: -109 }), true, "the browser's own jump is prevented");
  assert(frames(SMOOTH_MS).length >= 5, "several frames on the way");
  settle();
  assert.equal(Math.round(el.scrollTop), 282);
  assert.equal(wheel({ deltaY: 3.18, wheelDeltaY: -3 }), false);
});

test("a notch on the way extends the motion; a touchpad stays native", (t) => {
  const { el, notch, wheel, frames, settle } = fixture(t);
  notch(200);
  const before = frames(48);
  notch(200);
  const after = frames(16);
  assert(after[0] > before.at(-1), "the motion carries on");
  settle();
  assert.equal(el.scrollTop, 500);
  assert.equal(wheel({ deltaY: 3.5, wheelDeltaY: -4 }), false);
  assert.equal(wheel({ deltaY: 100, wheelDeltaY: -120, ctrlKey: true }), false, "zoom is viewerZoom's");
});

test("any other scroll ends the motion, and the ends are left to the browser", (t) => {
  const { el, notch, frames, settle, pending } = fixture(t);
  notch(400);
  frames(32);
  el.scrollTop = 2500; // a jump to a highlight, a zoom keeping its anchor, the scrollbar
  settle();
  assert.equal(el.scrollTop, 2500);
  assert.equal(pending(), 0);
  el.scrollTop = 4000; // the bottom: 5000 - 1000
  assert.equal(notch(200), false, "nothing left to scroll: the browser's overscroll");
  el.scrollTop = 3950;
  notch(200);
  settle();
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
