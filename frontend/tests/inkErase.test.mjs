// node --test tests/inkErase.test.mjs  (from frontend/) — the eraser's sweep
// (ink.js eraseAlong, hitStrokesAlong; docs/dev/handwriting.md "Client"):
// a path of page points whose consecutive points are capsules of the
// eraser's radius, tested along the segments between a stroke's samples,
// so a fast eraser and a sparse stroke are cut wherever the sweep crosses.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  decodeStroke, encodeStroke, eraseAlong, eraseAt, eraserKeep, hitStrokes, hitStrokesAlong, newInk, strokeWidth,
} from "../src/ink/ink.js";

const page = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
// The distance from (x, y) to the segment a → b.
function segDistance(x, y, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y, l2 = dx * dx + dy * dy;
  const t = l2 ? Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / l2)) : 0;
  return Math.hypot(a.x + t * dx - x, a.y + t * dy - y);
}
// The distance from (x, y) to a path (its capsules' centre lines).
const pathDistance = (x, y, path) => (path.length === 1 ? Math.hypot(x - path[0].x, y - path[0].y)
  : Math.min(...path.slice(1).map((b, i) => segDistance(x, y, path[i], b))));
// Every sample of every stroke lies outside the sweep: at least the radius
// plus its own half-width from the path, less the file's 1/100 pt grid.
function assertOutside(ink, path, radius, slack = 0.02) {
  for (const s of ink.strokes) for (const q of decodeStroke(s)) {
    const d = pathDistance(q.x, q.y, path), need = radius + strokeWidth(s, q) / 2;
    assert.ok(d >= need - slack, `a sample of ${s.id} at (${q.x}, ${q.y}) lies ${(need - d).toFixed(3)} pt inside the sweep`);
  }
}
const ends = (s) => { const q = decodeStroke(s); return [q[0], q[q.length - 1]]; };
const near = (a, b, tol, what) => assert.ok(Math.abs(a - b) <= tol, `${what}: ${a} vs ${b}`);

test("a two-sample line is cut in its middle by a capsule that never comes near either of its samples", () => {
  const line = encodeStroke({ id: "line", ch: "xyt", pen: false, size: 2, t0: 5000,
    samples: [{ x: 0, y: 100, t: 0 }, { x: 300, y: 100, t: 1000 }], shape: { kind: "line", params: { x1: 0, y1: 100, x2: 300, y2: 100 } } });
  const path = [{ x: 140, y: 40 }, { x: 160, y: 160 }];      // crosses the line at x = 150, at a slant
  assert.ok(decodeStroke(line).every((q) => segDistance(q.x, q.y, ...path) > 100), "neither sample is anywhere near the eraser");
  const { ink, changed } = eraseAlong(page(line), path, 3);
  assert.equal(changed, true);
  assert.equal(ink.strokes.length, 2, "two pieces");
  const [[a0, a1], [b0, b1]] = ink.strokes.map(ends);
  assert.deepEqual([a0.x, a0.y, b1.x, b1.y], [0, 100, 300, 100], "the far ends are the line's own");
  // the new ends lie at the eraser's edge: radius + half the width from its centre line
  near(segDistance(a1.x, a1.y, ...path), 3 + 1, 0.02, "the first piece's new end");
  near(segDistance(b0.x, b0.y, ...path), 3 + 1, 0.02, "the second piece's new end");
  assert.ok(a1.x < 150 && b0.x > 150);
  assertOutside(ink, path, 3);
});

test("a fast eraser's one long segment cuts the five strokes it crosses, though none lies near its points", () => {
  const xs = [50, 100, 150, 200, 250];
  const strokes = xs.map((x) => encodeStroke({ id: `v${x}`, ch: "xy", pen: false, size: 1, samples: [{ x, y: -40 }, { x, y: 40 }] }));
  const path = [{ x: 0, y: 0 }, { x: 300, y: 0 }];
  const { ink } = eraseAlong(page(...strokes), path, 3);
  assert.equal(ink.strokes.length, 10, "each stroke in two");
  xs.forEach((x, i) => {
    const [top, bottom] = [ink.strokes[2 * i], ink.strokes[2 * i + 1]].map(ends);
    assert.deepEqual([top[0].x, top[0].y, top[1].x, top[1].y], [x, -40, x, -3.5], `the upper piece of v${x} ends at the edge`);
    assert.deepEqual([bottom[0].x, bottom[0].y, bottom[1].x, bottom[1].y], [x, 3.5, x, 40], `the lower piece of v${x} starts at it`);
    assert.ok([ink.strokes[2 * i], ink.strokes[2 * i + 1]].every((s) => s.from === `v${x}`));
  });
  assertOutside(ink, path, 3);
  // where the old eraser looked, at the path's points alone, it found nothing
  assert.equal(path.reduce((acc, p) => eraseAt(acc, p.x, p.y, 3).ink, page(...strokes)).strokes.length, 5);
  assert.deepEqual(hitStrokesAlong(page(...strokes), path, 3), xs.map((x) => `v${x}`), "the whole-stroke eraser takes all five");
  assert.deepEqual(hitStrokesAlong(page(...strokes), [{ x: 0, y: 0 }, { x: 120, y: 0 }], 3), ["v50", "v100"]);
  assert.deepEqual(hitStrokesAlong(page(...strokes), [{ x: 0, y: 44 }, { x: 300, y: 44 }], 3), [], "4 pt past the strokes' ends: out of reach");
  assert.deepEqual(hitStrokesAlong(page(...strokes), [{ x: 0, y: 43 }, { x: 300, y: 43 }], 3), xs.map((x) => `v${x}`),
    "3 pt past them: the edge reaches");
  assert.deepEqual(hitStrokes(page(...strokes), 150, 0, 3), ["v150"], "one point is a disk");
});

test("a sparse freehand stroke crossed between its samples is cut where the eraser's edge meets its drawn width", () => {
  // a pressure pen with samples 30 pt apart, as a fast mouse or pen leaves them
  const samples = [0, 30, 60, 90, 120].map((x, i) => ({ x, y: i % 2 ? 2 : 0, p: 0.3 + 0.15 * i, t: 8 * i, w: 0.8 + 0.15 * i }));
  const pen = encodeStroke({ id: "sparse", ch: "xyptw", size: 2, t0: 100, samples });
  const path = [{ x: 45, y: -30 }, { x: 45, y: 30 }];           // between the samples at 30 and 60
  const { ink } = eraseAlong(page(pen), path, 2);
  assert.equal(ink.strokes.length, 2);
  const [left, right] = ink.strokes;
  const [, l1] = ends(left), [r0] = ends(right);
  // each new end at the radius plus its own half-width (the width lerped there)
  near(segDistance(l1.x, l1.y, ...path), 2 + strokeWidth(left, l1) / 2, 0.02, "the left piece's new end");
  near(segDistance(r0.x, r0.y, ...path), 2 + strokeWidth(right, r0) / 2, 0.02, "the right piece's new end");
  assert.ok(l1.x > 30 && l1.x < 45 && r0.x > 45 && r0.x < 60, "both between the samples the eraser passed between");
  assert.deepEqual(decodeStroke(left).slice(0, 2).map((q) => q.x), [0, 30], "the samples before the cut stand");
  assert.deepEqual(decodeStroke(right).slice(1).map((q) => q.x), [60, 90, 120], "and those after it");
  assertOutside(ink, path, 2);
});

test("a curved path laid along a stroke takes away the stretch it covers and nothing else", () => {
  const long = encodeStroke({ id: "long", ch: "xyp", size: 1.4, samples: Array.from({ length: 31 }, (_, i) => ({ x: 10 * i, y: 100, p: 0.5 })) });
  const path = Array.from({ length: 40 }, (_, i) => ({ x: 60 + (i * 180) / 39, y: 100 + 1.5 * Math.sin(i / 4) }));
  const { ink } = eraseAlong(page(long), path, 3);
  assert.equal(ink.strokes.length, 2);
  const [[a0, a1], [b0, b1]] = ink.strokes.map(ends);
  assert.deepEqual([a0.x, b1.x], [0, 300], "the stroke's own ends stand");
  near(a1.x, 60 - (3 + 0.7), 0.02, "the cut before the path's first point, at the radius plus the half-width from it");
  assert.ok(b0.x > 240 && b0.x < 244.5, `the cut after its last (${b0.x})`);
  assertOutside(ink, path, 3);
});

test("a cut's new samples lerp x, y, p, t, w and o, take tilt and unknown letters from the nearer sample, and keep t in order", () => {
  const s = encodeStroke({ id: "tilt", ch: "xyptazwoq", size: 2, t0: 100, brush: "pencil",
    samples: [{ x: 0, y: 0, p: 0.2, t: 0, a: 30, z: 10, w: 0.7, o: 0.3, rest: [7] }, { x: 100, y: 0, p: 1, t: 200, a: 60, z: 200, w: 1.5, o: 1, rest: [9] }] });
  const path = [{ x: 50, y: -20 }, { x: 50, y: 20 }];
  const { ink } = eraseAlong(page(s), path, 3);
  assert.equal(ink.strokes.length, 2);
  const [, l] = ends(ink.strokes[0]), [r] = ends(ink.strokes[1]);
  for (const [q, what, side] of [[l, "left", 1], [r, "right", -1]]) {
    const u = q.x / 100;
    near(q.p, 0.2 + 0.8 * u, 0.001, `${what} p`);
    near(q.t, 200 * u, 0.5, `${what} t`);
    near(q.w, 0.7 + 0.8 * u, 0.001, `${what} w`);
    near(q.o, 0.3 + 0.7 * u, 0.001, `${what} o`);
    // at the edge: the radius plus the lerped half-width from the eraser's line
    near(50 - q.x, side * (3 + q.w), 0.02, `${what} edge`);
  }
  assert.deepEqual([l.a, l.z, l.rest], [30, 10, [7]], "the left end is nearer the first sample");
  assert.deepEqual([r.a, r.z, r.rest], [60, 200, [9]], "the right end nearer the second");
  for (const piece of ink.strokes) {
    const t = decodeStroke(piece).map((q) => q.t);
    assert.ok(t.every((v, i) => !i || v >= t[i - 1]), `t in order: ${t}`);
  }
});

test("pieces keep ch, brush, dash, extras and t0, name their root in from, and drop shape and fill", () => {
  const s = encodeStroke({ id: "root", ch: "xytw", size: 2, t0: 42, brush: "brush", dash: "dashed", nib: 30, note: "mine",
    shape: { kind: "line", params: { x1: 0, y1: 0, x2: 200, y2: 0 } }, fill: { color: "#ff0000" },
    samples: Array.from({ length: 11 }, (_, i) => ({ x: 20 * i, y: 0, t: 10 * i, w: 1 })) });
  const { ink } = eraseAlong(page(s), [{ x: 70, y: -10 }, { x: 70, y: 10 }], 2);
  assert.equal(ink.strokes.length, 2);
  for (const piece of ink.strokes) {
    assert.deepEqual([piece.ch, piece.brush, piece.dash, piece.nib, piece.note, piece.t0, piece.from],
      ["xytw", "brush", "dashed", 30, "mine", 42, "root"]);
    assert.ok(!("shape" in piece) && !("fill" in piece), "a cut shape is just ink");
    assert.notEqual(piece.id, "root");
  }
  const again = eraseAlong(ink, [{ x: 150, y: -10 }, { x: 150, y: 10 }], 2).ink;
  assert.equal(again.strokes.length, 3);
  assert.ok(again.strokes.every((p) => p.from === "root"), "a cut of a piece keeps the root");
});

test("a piece too short to draw as a line is dropped as a speck; a longer one stays", () => {
  const line = (size) => encodeStroke({ id: `s${size}`, ch: "xy", pen: false, size, samples: [{ x: 0, y: 0 }, { x: 100, y: 0 }] });
  const pieces = (size, at) => eraseAt(page(line(size)), at, 0, 3).ink.strokes.length;
  // half-width 1: what is left before the eraser must be 1 pt long
  assert.equal(pieces(2, 4.5), 1, "a 0.5 pt stub of a 2 pt line goes");
  assert.equal(pieces(2, 5.5), 2, "a 1.5 pt one stays");
  // a hairline: the floor is 0.2 pt
  assert.equal(pieces(0.2, 3.25), 1, "a 0.15 pt stub of a 0.2 pt line goes");
  assert.equal(pieces(0.2, 3.35), 2, "a 0.25 pt one stays");
  // a speck between two passes of one path goes too: a U whose arms leave 0.5 pt between them
  const path = [{ x: 20, y: -10 }, { x: 20, y: 10 }, { x: 28.5, y: 10 }, { x: 28.5, y: -10 }];
  const u = eraseAlong(page(line(2)), path, 3).ink;
  assert.deepEqual(u.strokes.map((s) => ends(s).map((q) => q.x)), [[0, 16], [32.5, 100]]);
});

test("a dot under the sweep is erased; one out of its reach stays, the same object", () => {
  const dot = encodeStroke({ id: "dot", ch: "xy", pen: false, size: 2, samples: [{ x: 50, y: 50 }] });
  const path = [{ x: 40, y: 53.9 }, { x: 60, y: 53.9 }];
  assert.deepEqual(eraseAlong(page(dot), path, 3).ink.strokes, [], "3.9 pt away: within the radius and its half-width");
  const far = eraseAlong(page(dot), [{ x: 40, y: 54.1 }, { x: 60, y: 54.1 }], 3);
  assert.equal(far.changed, false);
  assert.equal(far.ink.strokes[0], dot);
  assert.deepEqual(hitStrokesAlong(page(dot), path, 3), ["dot"]);
});

test("the eraser filter is respected: a refused stroke is the same object; a sweep that cuts nothing is the same ink", () => {
  const pen = encodeStroke({ id: "pen", ch: "xy", pen: false, size: 2, samples: [{ x: 0, y: 0 }, { x: 100, y: 0 }] });
  const hl = encodeStroke({ id: "hl", tool: "highlighter", ch: "xy", pen: false, size: 14, opacity: 0.6, samples: [{ x: 0, y: 0 }, { x: 100, y: 0 }] });
  const ink = page(pen, hl);
  const path = [{ x: 50, y: -30 }, { x: 50, y: 30 }];
  const only = eraseAlong(ink, path, 3, eraserKeep("highlighter"));
  assert.equal(only.changed, true);
  assert.equal(only.ink.strokes[0], pen, "the pen is the same object, its caches kept");
  assert.equal(only.ink.strokes.length, 3);
  assert.ok(only.ink.strokes.slice(1).every((s) => s.tool === "highlighter" && s.from === "hl"));
  assert.deepEqual(hitStrokesAlong(ink, path, 3, eraserKeep("pen")), ["pen"]);
  const none = eraseAlong(ink, path, 3, () => false);
  assert.equal(none.changed, false);
  assert.equal(none.ink, ink);
  const miss = eraseAlong(ink, [{ x: 300, y: 300 }, { x: 400, y: 300 }], 3);
  assert.equal(miss.changed, false);
  assert.equal(miss.ink, ink, "nothing cut: the same ink");
  assert.equal(eraseAlong(ink, [], 3).ink, ink, "an empty path cuts nothing");
});

test("a sweep in two flushes, the second starting where the first ended, cuts what one sweep would", () => {
  const strokes = [0, 1, 2, 3].map((k) => encodeStroke({ id: `w${k}`, ch: "xyptw", size: 1.4,
    samples: Array.from({ length: 9 }, (_, i) => ({ x: 20 + 25 * i, y: 40 + 30 * k + (i % 3), p: 0.5, t: 8 * i, w: 0.9 + 0.05 * (i % 4) })) }));
  const path = Array.from({ length: 13 }, (_, i) => ({ x: 10 + 20 * i, y: 30 + 9 * i }));
  const once = eraseAlong(page(...strokes), path, 4).ink;
  const twice = eraseAlong(eraseAlong(page(...strokes), path.slice(0, 7), 4).ink, path.slice(6), 4).ink;
  assert.ok(once.strokes.length > strokes.length, "the path crosses them");
  assert.equal(twice.strokes.length, once.strokes.length);
  twice.strokes.forEach((s, i) => {
    const [a, b] = ends(s), [c, d] = ends(once.strokes[i]);
    for (const [p, q] of [[a, c], [b, d]]) assert.ok(Math.hypot(p.x - q.x, p.y - q.y) <= 0.02, "the same cuts");
  });
  assertOutside(twice, path, 4);
  // a cut end rounded onto the file's grid may lie a hair inside the edge:
  // the same sweep again, or the next flush's first capsule, leaves it be
  assert.equal(eraseAlong(once, path, 4).ink, once, "a second identical sweep cuts nothing");
  assert.equal(eraseAlong(twice, path.slice(6), 4).ink, twice, "nor does the second flush again");
});
