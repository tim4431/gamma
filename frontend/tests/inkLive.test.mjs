// node --test tests/inkLive.test.mjs (from frontend/) — live co-drawing's
// chunks and the wet strokes they make (ink/inkLive.js; docs/dev/collab.md
// "ink-live").
import assert from "node:assert/strict";
import { mock, test } from "node:test";
import {
  LIVE_SAMPLES, WET_MS, clearWet, keepWetOf, landWet, liveLedger, liveSamples, liveStyle, receiveInkLive, subscribeWet, wetOn,
} from "../src/ink/inkLive.js";

const style = { tool: "pen", brush: "brush", size: 2, color: "#1f1f1f", opacity: 1, pen: true };
const chunk = (over = {}) => ({ t: "ink-live", client: "c1", page: 3, g: null, id: "k7Qm2x", style, k: 1.5, n: 0,
  samples: [[10, 20, 0.5, 0, null, null], [11, 21, 0.6, 8, 71, 203]], ...over });

test("a writer's chunk: samples rounded to the wire's units, at most LIVE_SAMPLES, the look as it will be encoded", () => {
  const samples = Array.from({ length: 70 }, (_, i) => ({ x: 1.23456 + i, y: 2.5, p: 0.61234, t: i * 4.4, ...(i ? {} : { a: 71.6, z: 203.2 }) }));
  const first = liveSamples(samples, 0);
  assert.equal(first.length, LIVE_SAMPLES);
  assert.deepEqual(first[0], [1.23, 2.5, 0.612, 0, 72, 203]);
  assert.deepEqual(first[1], [2.23, 2.5, 0.612, 4, null, null]);
  assert.deepEqual(liveSamples(samples, 64).map((s) => s[0]), [65.23, 66.23, 67.23, 68.23, 69.23, 70.23]);
  assert.equal(liveSamples(samples, 0, false)[0][2], null, "no pressure without a stylus");
  assert.deepEqual(liveStyle({ tool: "pen", color: "#000000", size: 2, opacity: 1, stabilize: 2 }, true),
    { tool: "pen", size: 2, color: "#000000", opacity: 1, pen: true }, "only the keys a stroke is encoded with");
  assert.deepEqual(liveStyle({ tool: "highlighter", color: "#ffe066", size: 14, opacity: 0.6, dash: "dotted" }, false),
    { tool: "highlighter", size: 14, color: "#ffe066", opacity: 0.6, pen: false, dash: "dotted" });
});

test("chunks in order grow a wet stroke on their surface; a gap drops it until the file lands; drop cancels it", () => {
  clearWet();
  const seen = [];
  const off = subscribeWet(3, () => seen.push(wetOn(3).length));
  assert.equal(receiveInkLive(chunk()), true);
  let [w] = wetOn(3);
  assert.deepEqual([w.client, w.id, w.style.brush, w.samples.length, w.done], ["c1", "k7Qm2x", "brush", 2, false]);
  assert.deepEqual(w.samples[1], { x: 11, y: 21, t: 8, p: 0.6, a: 71, z: 203 });
  assert.equal("p" in wetOn(3)[0].samples[0], true);
  receiveInkLive(chunk({ n: 2, samples: [[12, 22, null, 16, null, null]], done: true }));
  [w] = wetOn(3);
  assert.equal(w.samples.length, 3);
  assert.equal("p" in w.samples[2], false, "a null pressure is no pressure");
  assert.equal(w.done, true);
  assert.deepEqual(wetOn(4), [], "another surface has none");
  // a chunk out of step (one was missed): the stroke goes, and so does what follows
  receiveInkLive(chunk({ id: "gap", n: 0 }));
  receiveInkLive(chunk({ id: "gap", n: 5 }));
  receiveInkLive(chunk({ id: "gap", n: 7 }));
  assert.deepEqual(wetOn(3).map((s) => s.id), ["k7Qm2x"]);
  // a first chunk missed: nothing starts
  assert.equal(receiveInkLive(chunk({ id: "late", n: 2 })), false);
  receiveInkLive(chunk({ id: "cancel" }));
  receiveInkLive({ t: "ink-live", client: "c1", page: 3, id: "cancel", drop: true });
  assert.deepEqual(wetOn(3).map((s) => s.id), ["k7Qm2x"]);
  // malformed: no style, no id, no surface
  assert.equal(receiveInkLive(chunk({ id: "x", style: null })), false);
  assert.equal(receiveInkLive({ ...chunk(), id: 5 }), false);
  assert.equal(receiveInkLive(chunk({ id: "y", page: null })), false);
  // a sheet is named by its block id
  receiveInkLive(chunk({ page: "sheet1", id: "s1" }));
  assert.equal(wetOn("sheet1").length, 1);
  assert.ok(seen.length >= 4, "the layer heard of every change");
  off();
  clearWet();
});

test("a wet stroke goes when its file lands on the surface, when its writer leaves, and a minute after its last chunk", () => {
  clearWet();
  receiveInkLive(chunk({ id: "a" }));
  receiveInkLive(chunk({ id: "b", client: "c2" }));
  assert.equal(landWet(3, ["zzz"]), false);
  assert.equal(landWet(3, ["a"]), true);
  assert.deepEqual(wetOn(3).map((s) => s.id), ["b"]);
  keepWetOf(["c1"]);
  assert.deepEqual(wetOn(3), [], "c2 left the page");
  mock.timers.enable({ apis: ["setTimeout"] });
  try {
    receiveInkLive(chunk({ id: "c" }));
    mock.timers.tick(WET_MS - 1);
    receiveInkLive(chunk({ id: "c", n: 2, samples: [[1, 1, null, 9, null, null]], done: true }));
    mock.timers.tick(WET_MS - 1);
    assert.equal(wetOn(3).length, 1, "each chunk restarts the minute");
    mock.timers.tick(1);
    assert.deepEqual(wetOn(3), [], "gone a minute after its last chunk, its file never landed");
  } finally {
    mock.timers.reset();
    clearWet();
  }
});

test("the writer's ledger: a stroke sent live and taken away before a save held it gets one drop; a saved one, a moved one, a dropped one none", () => {
  const ledger = liveLedger();
  const ink = (...ids) => ({ strokes: ids.map((id) => ({ id })) });
  ledger.sent("s1", 3);
  ledger.sent("s2", 3);
  ledger.sent("s3", "sheet-1");
  ledger.sent("s4", 3);
  ledger.sent("s4", 3, true);   // its own drop went already (a cancelled stroke)
  // an undo right after the lift takes s1 away
  assert.deepEqual(ledger.removed([{ before: ink("a", "s1"), after: ink("a") }]), [{ id: "s1", page: 3 }]);
  assert.deepEqual(ledger.removed([{ before: ink("a", "s1"), after: ink("a") }]), [], "once");
  // a new note from the selection moves s2 to another group: no drop
  assert.deepEqual(ledger.removed([{ before: ink("s2"), after: ink() }, { before: ink(), after: ink("s2") }]), []);
  // a save holds s2 and s3: their files land them, so a later erasure needs no drop
  ledger.saved(ink("s2", "s3"));
  assert.deepEqual(ledger.removed([{ before: ink("s2", "s3", "s4"), after: ink() }]), [], "saved, or dropped already");
  ledger.sent("s5", 4);
  ledger.clear();
  assert.deepEqual(ledger.removed([{ before: ink("s5"), after: ink() }]), [], "leaving the page forgets them");
});
