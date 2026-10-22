// node --test tests/  (from frontend/) — two- and three-finger undo chords:
// the 80 ms start, 10 px slop and 250 ms lift windows, cancel never counts,
// a pen down is a palm, and 'double' needs two chords within 400 ms / 30 px.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CHORD_LIFT_MS, CHORD_SLOP, CHORD_START_MS, CONTACT_STALE_MS, DOUBLE_MS, DOUBLE_SLOP, makeChordDetector } from "../src/ink/fingerChords.js";

const ev = (pointerId, timeStamp, clientX = 100, clientY = 100, pointerType = "touch") => ({ pointerId, pointerType, clientX, clientY, timeStamp });

function detector(mode = "tap") {
  const fired = [];
  const d = makeChordDetector({ onChord: (kind) => fired.push(kind), mode });
  // a chord of n fingers starting at t0 at (x, y), lifted at t0 + lift
  d.chord = (n, t0, { x = 100, y = 100, lift = 100, spread = 20 } = {}) => {
    for (let i = 0; i < n; i++) d.down(ev(i + 1, t0 + 5 * i, x + spread * i, y));
    for (let i = 0; i < n; i++) d.up(ev(i + 1, t0 + lift + 5 * i, x + spread * i, y));
  };
  return [d, fired];
}

test("tap mode: two fingers undo, three redo, one or four do nothing", () => {
  const [d, fired] = detector("tap");
  d.chord(2, 0);
  assert.deepEqual(fired, ["undo"]);
  d.chord(3, 1000);
  assert.deepEqual(fired, ["undo", "redo"]);
  d.chord(1, 2000);
  d.chord(4, 3000);
  assert.deepEqual(fired, ["undo", "redo"]);
  assert.deepEqual([CHORD_START_MS, CHORD_SLOP, CHORD_LIFT_MS, DOUBLE_MS, DOUBLE_SLOP], [80, 10, 250, 400, 30]);
});

test("the windows: downs within 80 ms of the first, no move over 10 px, every lift within 250 ms of the first down", () => {
  let [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 80, 120));
  d.up(ev(1, 100)); d.up(ev(2, 100, 120));
  assert.deepEqual(fired, ["undo"], "a second finger at exactly 80 ms counts");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 81, 120));
  d.up(ev(1, 100)); d.up(ev(2, 100, 120));
  assert.deepEqual(fired, [], "a late second finger voids the chord");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.move(ev(2, 50, 120, 109)); d.move(ev(2, 60, 120, 111));
  d.up(ev(1, 100)); d.up(ev(2, 100, 120, 111));
  assert.deepEqual(fired, [], "a finger that moved 11 px is a scroll");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.move(ev(2, 50, 120, 109));
  d.up(ev(1, 100)); d.up(ev(2, 100, 120, 109));
  assert.deepEqual(fired, ["undo"], "9 px of wander is still a tap");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.up(ev(1, 100)); d.up(ev(2, 251, 120));
  assert.deepEqual(fired, [], "a lift after 250 ms is a hold, not a chord");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.up(ev(1, 100)); d.up(ev(2, 250, 120));
  assert.deepEqual(fired, ["undo"]);
  // the mouse is not a finger
  [d, fired] = detector("tap");
  d.down(ev(1, 0, 100, 100, "mouse")); d.down(ev(2, 10, 120));
  d.up(ev(1, 100, 100, 100, "mouse")); d.up(ev(2, 100, 120));
  assert.deepEqual(fired, []);
});

test("a pointercancel never counts: a pinch cancels both touches, so it never undoes; a cancelled finger voids the chord", () => {
  let [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.cancel(ev(1, 50)); d.cancel(ev(2, 50, 120));
  assert.deepEqual(fired, []);
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.cancel(ev(1, 50)); d.up(ev(2, 60, 120));
  assert.deepEqual(fired, [], "one cancelled, one lifted: no chord");
  // and the detector is clean afterwards
  d.chord(2, 1000);
  assert.deepEqual(fired, ["undo"]);
});

test("touches while a pen is down are a palm; a pen landing mid-chord makes it one too", () => {
  let [d, fired] = detector("tap");
  d.down(ev(9, 0, 300, 300, "pen"));
  d.chord(2, 10);
  assert.deepEqual(fired, []);
  d.up(ev(9, 500, 300, 300, "pen"));
  d.chord(2, 600);
  assert.deepEqual(fired, ["undo"], "the pen lifted, chords work again");
  [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.down(ev(9, 20, 300, 300, "pen"));
  d.up(ev(1, 100)); d.up(ev(2, 100, 120));
  assert.deepEqual(fired, []);
  d.cancel(ev(9, 200, 300, 300, "pen"));
  d.chord(3, 1000);
  assert.deepEqual(fired, ["redo"], "a cancelled pen is up too");
});

test("double mode: a second chord of the same size within 400 ms and 30 px fires once; off fires nothing but keeps tracking", () => {
  let [d, fired] = detector("double");
  d.chord(2, 0);
  assert.deepEqual(fired, [], "the first chord waits");
  d.chord(2, 300);                              // the first's last lift was at 105; 300 + 105 lift = 405 … within 400 of 105? the lift times are 400 and 405
  assert.deepEqual(fired, ["undo"], "the second chord's lift within 400 ms of the first's");
  d.chord(2, 1000);
  assert.deepEqual(fired, ["undo"], "a third starts over");
  d.chord(2, 1000 + 100 + 401);                 // its lift lands 401 ms after the previous last lift
  assert.deepEqual(fired, ["undo"], "too late: it becomes the new first");
  d.chord(2, 1600);
  assert.deepEqual(fired, ["undo", "undo"]);
  d.chord(2, 3000); d.chord(2, 3200, { x: 140 });
  assert.deepEqual(fired, ["undo", "undo"], "31 px away (centroids 110 → 150 … 40 px): not a double");
  d.chord(2, 4000); d.chord(2, 4200, { x: 125 });
  assert.deepEqual(fired, ["undo", "undo", "undo"], "25 px away is one");
  d.chord(3, 6000); d.chord(2, 6200);
  assert.deepEqual(fired, ["undo", "undo", "undo"], "a chord of another size is not the second of the first");
  d.chord(3, 6400);
  assert.deepEqual(fired, ["undo", "undo", "undo"], "… and the two-finger one between them reset the pair");
  d.chord(3, 7000); d.chord(3, 7200);
  assert.deepEqual(fired, ["undo", "undo", "undo", "redo"]);
  // off: tracking goes on, so a mode change mid-gesture is safe
  [d, fired] = detector("off");
  d.chord(2, 0);
  assert.deepEqual(fired, []);
  d.down(ev(1, 1000)); d.down(ev(2, 1010, 120));
  d.setMode("tap");
  d.up(ev(1, 1100)); d.up(ev(2, 1100, 120));
  assert.deepEqual(fired, ["undo"]);
  d.setMode("nonsense");
  d.chord(2, 2000);
  assert.deepEqual(fired, ["undo"], "an unknown mode is off");
  // reset forgets a chord in progress and the pens
  d.setMode("tap");
  d.down(ev(9, 3000, 0, 0, "pen"));
  d.down(ev(1, 3010));
  d.reset();
  d.chord(2, 4000);
  assert.deepEqual(fired, ["undo", "undo"]);
});

test("a contact whose lift never comes is forgotten after 1 s, so later chords still fire; a recent one still voids the next chord", () => {
  let [d, fired] = detector("tap");
  d.down(ev(7, 0, 50, 50));                     // lands, and no pointerup ever arrives
  d.chord(2, 5000);
  d.chord(2, 15000);
  assert.deepEqual(fired, ["undo", "undo"]);
  assert.equal(CONTACT_STALE_MS, 1000);
  [d, fired] = detector("tap");
  d.down(ev(7, 0, 50, 50));
  d.chord(2, 500);                              // the held finger is recent: the chord is late, a palm
  assert.deepEqual(fired, []);
  d.chord(2, 1200);
  assert.deepEqual(fired, ["undo"], "once it is stale, the next chord is clean");
});

test("double mode: a chord that fails between two good ones breaks the pair", () => {
  let [d, fired] = detector("double");
  d.chord(2, 0);
  d.down(ev(1, 150)); d.down(ev(2, 160, 120));
  d.move(ev(2, 200, 120, 130));                 // moved 20 px: voided
  d.up(ev(1, 250)); d.up(ev(2, 250, 120, 130));
  d.chord(2, 300);
  assert.deepEqual(fired, [], "the voided chord broke the pair; this one is a new first");
  d.chord(2, 500);
  assert.deepEqual(fired, ["undo"]);
  [d, fired] = detector("double");
  d.chord(2, 0);
  d.down(ev(1, 150)); d.down(ev(2, 160, 120));
  d.cancel(ev(1, 200)); d.cancel(ev(2, 200, 120)); // a pinch
  d.chord(2, 300);
  assert.deepEqual(fired, [], "a cancelled chord breaks it too");
});

test("palm(): the touches down are a palm (a pen the detector never saw is writing)", () => {
  const [d, fired] = detector("tap");
  d.down(ev(1, 0)); d.down(ev(2, 10, 120));
  d.palm();
  d.up(ev(1, 100)); d.up(ev(2, 100, 120));
  assert.deepEqual(fired, []);
  d.chord(2, 1000);
  assert.deepEqual(fired, ["undo"], "the next chord is clean");
});

test("events without a timeStamp use the clock given", () => {
  let clock = 0;
  const fired = [];
  const d = makeChordDetector({ onChord: (k) => fired.push(k), mode: "tap", now: () => clock });
  const bare = (id, x) => ({ pointerId: id, pointerType: "touch", clientX: x, clientY: 0 });
  d.down(bare(1, 0)); clock = 20; d.down(bare(2, 30));
  clock = 100; d.up(bare(1, 0)); d.up(bare(2, 30));
  assert.deepEqual(fired, ["undo"]);
});
