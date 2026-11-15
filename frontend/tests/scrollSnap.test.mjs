import test from "node:test";
import assert from "node:assert/strict";
import { installScrollSnap } from "../src/pdf/scrollSnap.js";

function fixture(t, scrollEnd = true, opts = {}) {
  const doc = new EventTarget(), win = new EventTarget();
  const restore = [];
  for (const [key, value] of Object.entries({ document: doc, window: win, matchMedia: () => ({ matches: false }) })) {
    const old = Object.getOwnPropertyDescriptor(globalThis, key);
    Object.defineProperty(globalThis, key, { value, configurable: true });
    restore.push(() => old ? Object.defineProperty(globalThis, key, old) : delete globalThis[key]);
  }
  const el = new EventTarget(), corrections = [];
  Object.assign(el, { scrollWidth: 2500, clientWidth: 800, scrollHeight: 2000, clientHeight: 700, scrollLeft: 300, scrollTop: 100,
    scrollTo: (opts) => corrections.push(opts), contains: () => true });
  if (scrollEnd) el.onscrollend = null;
  const cleanup = installScrollSnap(el, opts);
  t.after(() => { cleanup(); restore.forEach((fn) => fn()); });
  const send = (name, points = [], target = el) => {
    const e = new Event(name);
    e.touches = points.map(([clientX, clientY]) => ({ clientX, clientY }));
    target.dispatchEvent(e);
  };
  const vertical = () => { send("touchstart", [[100, 300]]); send("touchmove", [[110, 200]]); };
  const sideways = () => { send("touchstart", [[300, 100]]); send("touchmove", [[200, 110]]); };
  return { el, corrections, send, vertical, sideways, cleanup, doc };
}

test("no offset writes during touch or momentum; one correction after scrollend", (t) => {
  const { el, corrections, send, vertical } = fixture(t);
  vertical();
  el.scrollLeft = 315;
  send("scroll"); send("scrollend");
  assert.equal(el.scrollLeft, 315);
  assert.equal(corrections.length, 0);
  send("touchend");
  el.scrollLeft = 330; send("scroll");
  assert.equal(el.scrollLeft, 330);
  assert.equal(corrections.length, 0);
  send("scrollend"); send("scroll"); send("scrollend");
  assert.deepEqual(corrections, [{ left: 300, behavior: "smooth" }]);
});
test("diagonal movement, deliberate turns, pinch, cancellation, and new input release alignment", (t) => {
  const { el, corrections, send, doc } = fixture(t);
  const cancelCases = [
    () => send("touchmove", [[170, 250]]),
    () => { send("touchmove", [[110, 200]]); send("touchmove", [[220, 190]]); },
    () => send("touchstart", [[110, 200], [200, 200]]),
    () => send("touchcancel"),
    () => send("wheel"),
    () => send("keydown", [], doc),
  ];
  for (const cancel of cancelCases) {
    el.scrollLeft = 300;
    send("touchstart", [[100, 300]]);
    cancel(); el.scrollLeft = 330;
    send("touchend"); send("scrollend");
  }
  assert.equal(corrections.length, 0);
});
test("older Safari fallback waits for quiet after finger lift and cancels on teardown", async (t) => {
  const { el, corrections, send, vertical, cleanup } = fixture(t, false);
  vertical(); el.scrollLeft = 325; send("scroll");
  await new Promise((r) => setTimeout(r, 280));
  assert.equal(corrections.length, 0, "holding a finger never times out into snapping");
  send("touchend"); send("scroll");
  await new Promise((r) => setTimeout(r, 280));
  assert.equal(corrections.length, 1);
  vertical(); el.scrollLeft = 350; send("touchend"); cleanup();
  await new Promise((r) => setTimeout(r, 280));
  assert.equal(corrections.length, 1);
});
test("pages running across: a sideways swipe has its vertical drift corrected once; a downward one is left alone", (t) => {
  const { el, corrections, send, vertical, sideways } = fixture(t, true, { across: true });
  sideways();
  el.scrollTop = 130; el.scrollLeft = 900;
  send("touchend"); send("scroll"); send("scrollend");
  assert.deepEqual(corrections, [{ top: 100, behavior: "smooth" }]);
  assert.equal(el.scrollLeft, 900, "the run's own offset is never written");
  el.scrollTop = 100;
  vertical(); el.scrollTop = 160; send("touchend"); send("scrollend");
  assert.equal(corrections.length, 1, "a swipe against the run is not pulled back");
});
