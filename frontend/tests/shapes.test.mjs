// node --test tests/  (from frontend/) — the draw-and-hold recogniser over
// tests/shared/inkshapes.json, the polyline a shape commits as (its
// `samples`, which the server's copy in gamma/ink.py runs too), the refit
// under a continued hold, and the iPad host's hold rules over
// tests/shared/inkhold.json (the Swift side runs the same cases).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  CANCEL_MS, CANCEL_PX, HOLD_MIN_MS, HOLD_MOVE, HOLD_MS, PASTE_MOVE, REFIT_PX, SHAPE_KINDS, followHold, recognise, refitShape,
  shapeName, shapeSamples, stillSince,
} from "../src/ink/shapes.js";

const FIX = JSON.parse(readFileSync(new URL("../../tests/shared/inkshapes.json", import.meta.url), "utf8"));
const decode = (rows) => rows.map(([x, y, p, t]) => ({ x, y, p, t }));
const SYMMETRY = { rect: 90, polygon: (p) => 360 / p.n, ellipse: 180 };
const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

// An angle's difference modulo the shape's symmetry (and 360 for the rest).
function angleGap(kind, got, want, params) {
  const sym = SYMMETRY[kind];
  const m = typeof sym === "function" ? sym(params) : sym || 360;
  const d = (((got - want) % m) + m) % m;
  return Math.min(d, m - d);
}

for (const c of FIX.cases) {
  test(`recognise: ${c.note}`, () => {
    const got = recognise(decode(c.samples), { k: c.k });
    if (c.expect === null) { assert.equal(got, null); return; }
    assert.ok(got, "recognised nothing");
    assert.equal(got.kind, c.expect.kind);
    const tol = c.expect.tol ?? 2;
    for (const [key, want] of Object.entries(c.expect.params)) {
      const v = got.params[key];
      assert.equal(typeof v, "number", `${key} missing`);
      if (key === "n") assert.equal(v, want);
      else if (key === "angle") assert.ok(angleGap(got.kind, v, want, got.params) <= 5, `angle ${v} vs ${want}`);
      else assert.ok(Math.abs(v - want) <= tol, `${key} ${v} vs ${want} (tol ${tol})`);
    }
    assert.deepEqual(Object.keys(got.params).sort(), Object.keys(c.expect.params).sort(), "the same parameter keys");
  });
}

const caseNamed = (part) => FIX.cases.find((c) => c.note.includes(part));

test("recognise: too few samples, kinds restricted to lines (a highlighter), and the size floor in css px", () => {
  const line = caseNamed("1° off horizontal");
  assert.equal(recognise(decode(line.samples).slice(0, 7), { k: 1 }), null);
  assert.equal(recognise(decode(line.samples), { k: line.k, kinds: ["line"] })?.kind, "line");
  const rect = caseNamed("rectangle drawn 1.5°");
  assert.equal(recognise(decode(rect.samples), { k: rect.k, kinds: ["line"] }), null, "a highlighter never becomes a rectangle");
  // a 20 pt line is a shape at scale 1 (floor 24 px / 1 = 24 pt? no: max(24 / k, 18)); at k = 0.5 the floor is 48 pt
  const short = Array.from({ length: 20 }, (_, i) => ({ x: 100 + i, y: 100, p: 0.5, t: 20 * i }));
  assert.equal(recognise(short, { k: 2 })?.kind, "line", "19 pt at k = 2: the floor is 18 pt");
  assert.equal(recognise(short, { k: 0.5 }), null, "19 pt at k = 0.5 is under 48 px on screen");
  assert.equal(recognise(null, {}), null);
});

test("shapeSamples: a rectangle is a closed dense polyline with t spread over the span and p constant", () => {
  const pts = shapeSamples("rect", { cx: 100, cy: 100, w: 40, h: 20, angle: 0 }, { span: 1000, p: 0.7 });
  assert.deepEqual(pts[0], { x: 80, y: 90, p: 0.7, t: 0 });
  assert.deepEqual({ x: pts.at(-1).x, y: pts.at(-1).y }, { x: pts[0].x, y: pts[0].y }, "closed: the first sample again");
  assert.equal(pts.at(-1).t, 1000);
  for (let i = 1; i < pts.length; i++) {
    assert.ok(dist(pts[i - 1], pts[i]) <= 2.0001, `a sample every 2 pt (${dist(pts[i - 1], pts[i])})`);
    assert.ok(pts[i].t >= pts[i - 1].t, "t never runs backwards");
    assert.equal(pts[i].p, 0.7);
  }
  assert.equal(pts.length, 61, "corners plus 2 pt steps: 20 + 10 + 20 + 10 segments, closed");
  // the corners are samples
  for (const [x, y] of [[120, 90], [120, 110], [80, 110]]) assert.ok(pts.some((s) => s.x === x && s.y === y), `corner ${x},${y}`);
});

test("shapeSamples: a line is two samples; an arrow runs tail → tip → barb → tip → barb; round shapes have at least 48", () => {
  assert.deepEqual(shapeSamples("line", { x1: 0, y1: 0, x2: 30, y2: 40 }, { span: 300, p: 0.5 }),
    [{ x: 0, y: 0, p: 0.5, t: 0 }, { x: 30, y: 40, p: 0.5, t: 300 }]);
  const arrow = shapeSamples("arrow", { x1: 100, y1: 600, x2: 300, y2: 600, head: 30 }, { span: 600, p: 0.5 });
  const tips = arrow.map((s, i) => (s.x === 300 && s.y === 600 ? i : -1)).filter((i) => i >= 0);
  assert.equal(tips.length, 2, "the tip is visited twice");
  assert.deepEqual([arrow[0].x, arrow[0].y], [100, 600]);
  const barbs = arrow.filter((s) => Math.abs(Math.hypot(s.x - 300, s.y - 600) - 30) < 0.01 && s.y !== 600);
  assert.equal(barbs.length, 2, "two barbs 30 pt from the tip");
  assert.ok(barbs[0].y < 600 && barbs[1].y > 600, "one on each side");
  const first = arrow.findIndex((s) => s === barbs[0]);
  assert.ok(tips[0] < first && first < tips[1], "tail, tip, barb, tip, barb");
  const circle = shapeSamples("circle", { cx: 0, cy: 0, r: 10 }, { span: 100, p: 0.5 });
  assert.ok(circle.length >= 49, `${circle.length} samples`);
  assert.deepEqual([circle[0].x, circle[0].y], [circle.at(-1).x, circle.at(-1).y]);
  for (const s of circle) assert.ok(Math.abs(Math.hypot(s.x, s.y) - 10) < 0.02);
  const ellipse = shapeSamples("ellipse", { cx: 300, cy: 400, rx: 120, ry: 60, angle: 30 }, { span: 100, p: 0.5 });
  assert.ok(ellipse.length >= 49 && ellipse.length >= Math.ceil(580 / 2), "at least a sample every 2 pt of perimeter");
  const far = ellipse.reduce((m, s) => Math.max(m, Math.hypot(s.x - 300, s.y - 400)), 0);
  assert.ok(Math.abs(far - 120) < 0.05);
  const polygon = shapeSamples("polygon", { cx: 0, cy: 0, r: 50, n: 6, angle: 0 }, { span: 100, p: 0.5 });
  assert.deepEqual([polygon[0].x, polygon[0].y], [50, 0]);
  assert.ok(polygon.some((s) => Math.abs(s.x - 25) < 0.01 && Math.abs(s.y - 43.3) < 0.01), "the second corner at 60°");
  const triangle = shapeSamples("triangle", { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 }, { span: 100, p: 0.5 });
  assert.equal(triangle.filter((s) => s.x === 20 && s.y === 30).length, 1);
  assert.deepEqual(shapeSamples("curve", {}, {}), []);
});

// inkshapes.json `samples`: the samples a shape record is drawn as, which
// the server's copy (gamma/ink.py shape_samples) gives for the same params.
for (const c of FIX.samples) {
  test(`shapeSamples, as the server's shape_samples: ${c.note}`, () => {
    assert.deepEqual(shapeSamples(c.kind, c.params, {}).map((s) => [s.x + 0, s.y + 0]), c.pts);
  });
}

test("refitShape: a line or arrow takes the pointer as its end, Shift snapping the direction to 15°", () => {
  assert.deepEqual(refitShape("line", { x1: 0, y1: 0, x2: 100, y2: 0 }, { pointer: { x: 50, y: 50 }, from: { x: 100, y: 0 } }),
    { x1: 0, y1: 0, x2: 50, y2: 50 });
  const snapped = refitShape("line", { x1: 0, y1: 0, x2: 100, y2: 0 }, { pointer: { x: 100, y: 10 }, from: { x: 100, y: 0 }, shift: true });
  assert.deepEqual(snapped, { x1: 0, y1: 0, x2: 100.5, y2: 0 }, "5.7° snaps to 0°, the length kept");
  const arrow = refitShape("arrow", { x1: 0, y1: 0, x2: 100, y2: 0, head: 20 }, { pointer: { x: 0, y: 80 }, from: { x: 100, y: 0 } });
  assert.deepEqual(arrow, { x1: 0, y1: 0, x2: 0, y2: 80, head: 20 });
});

test("refitShape: closed shapes scale about their centre and rotate with the pointer, from the snap's params", () => {
  const snap = { cx: 100, cy: 100, r: 50 };
  assert.deepEqual(refitShape("circle", snap, { pointer: { x: 200, y: 100 }, from: { x: 150, y: 100 } }), { cx: 100, cy: 100, r: 100 });
  // the same call again gives the same answer: never from an already-refit set
  assert.deepEqual(refitShape("circle", snap, { pointer: { x: 200, y: 100 }, from: { x: 150, y: 100 } }), { cx: 100, cy: 100, r: 100 });
  const rect = { cx: 0, cy: 0, w: 40, h: 20, angle: 0 };
  assert.deepEqual(refitShape("rect", rect, { pointer: { x: 0, y: 20 }, from: { x: 20, y: 0 } }),
    { cx: 0, cy: 0, w: 20, h: 40, angle: 0 }, "a quarter turn is the same rectangle with w and h swapped");
  assert.deepEqual(refitShape("rect", rect, { pointer: { x: 60, y: 0 }, from: { x: 20, y: 0 } }), { cx: 0, cy: 0, w: 120, h: 60, angle: 0 }, "proportions kept");
  const turned = refitShape("rect", rect, { pointer: { x: 20 * Math.cos(0.3), y: 20 * Math.sin(0.3) }, from: { x: 20, y: 0 } });
  assert.ok(Math.abs(turned.angle - 17.19) < 0.02 && Math.abs(turned.w - 40) < 0.01, JSON.stringify(turned));
  const shifted = refitShape("rect", rect, { pointer: { x: 20 * Math.cos(0.3), y: 20 * Math.sin(0.3) }, from: { x: 20, y: 0 }, shift: true });
  assert.equal(shifted.angle, 15, "Shift snaps the rotation to 15°");
  const poly = refitShape("polygon", { cx: 0, cy: 0, r: 10, n: 6, angle: 10 }, { pointer: { x: 0, y: 20 }, from: { x: 20, y: 0 } });
  assert.deepEqual(poly, { cx: 0, cy: 0, r: 10, n: 6, angle: 40 }, "100° mod 60 → 40");
  const ell = refitShape("ellipse", { cx: 0, cy: 0, rx: 20, ry: 10, angle: 170 }, { pointer: { x: 0, y: 10 }, from: { x: 10, y: 0 } });
  assert.deepEqual(ell, { cx: 0, cy: 0, rx: 20, ry: 10, angle: 80 }, "260 mod 180 → 80, rx ≥ ry kept");
  const tri = { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 };
  const big = refitShape("triangle", tri, { pointer: { x: 50, y: 10 }, from: { x: 40, y: 10 } });   // centre (20, 10): 30 / 20
  const side = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  const ratio = side([tri.x1, tri.y1], [tri.x2, tri.y2]) / side([tri.x2, tri.y2], [tri.x3, tri.y3]);
  assert.ok(Math.abs(side([big.x1, big.y1], [big.x2, big.y2]) / side([big.x2, big.y2], [big.x3, big.y3]) - ratio) < 1e-6, "proportions kept");
  assert.ok(Math.abs(side([big.x1, big.y1], [big.x2, big.y2]) - 40 * 1.5) < 0.05, "scaled by |pointer − centre| / |from − centre|");
  assert.deepEqual(refitShape("circle", snap, { pointer: { x: 100, y: 100 }, from: { x: 100, y: 100 } }), snap, "a pointer on the centre changes nothing");
});

test("shapeName names every kind (English under node), and SHAPE_KINDS lists the recogniser's answers", () => {
  assert.deepEqual(SHAPE_KINDS.map(shapeName), ["line", "arrow", "triangle", "rectangle", "polygon", "circle", "ellipse"]);
  assert.equal(shapeName("blob"), "blob");
});

// tests/shared/inkhold.json: the hold under the iPad host, the rules its
// shell runs over the same samples (StrokeRecorder, ShapeHold, HeldShape).
const HOLD = JSON.parse(readFileSync(new URL("../../tests/shared/inkhold.json", import.meta.url), "utf8"));
const point = ([x, y]) => ({ x, y });
// every number within 0.011 (a 1/100 rounding either side), n exactly, the same keys; null exactly
function sameParams(got, want, what) {
  if (want === null) { assert.equal(got, null, what); return; }
  assert.ok(got, `${what}: a shape`);
  assert.deepEqual(Object.keys(got).sort(), Object.keys(want).sort(), `${what}: the same keys`);
  for (const [key, v] of Object.entries(want)) {
    if (key === "n") assert.equal(got[key], v, `${what}: n`);
    else assert.ok(Math.abs(got[key] - v) <= 0.011, `${what}: ${key} ${got[key]} vs ${v}`);
  }
}

test("the hold's numbers are the ones the fixture and the iPad shell are written for", () => {
  assert.deepEqual([HOLD_MS, HOLD_MOVE, HOLD_MIN_MS, REFIT_PX, CANCEL_PX, CANCEL_MS, PASTE_MOVE], [600, 3, 250, 6, 12, 150, 2]);
  assert.ok(HOLD.still.length && HOLD.follow.length && HOLD.refit.length);
});

for (const c of HOLD.still) {
  test(`stillSince: ${c.note}`, () => assert.equal(stillSince(decode(c.samples)), c.stillSince));
}
for (const c of HOLD.follow) {
  test(`followHold: ${c.note}`, () => {
    const hold = { ...c.hold, from: point(c.hold.from) };
    // the hold as the web keeps it: the anchor's t + HOLD_MS, n samples, the last of them `from`
    const carried = decode(c.samples.slice(0, c.hold.n));
    assert.equal(c.hold.tSnap, carried[stillSince(carried)].t + HOLD_MS, "tSnap is the anchor's t + HOLD_MS");
    assert.deepEqual(hold.from, { x: carried.at(-1).x, y: carried.at(-1).y }, "from is the last sample the hold carried");
    sameParams(followHold(hold, decode(c.samples)), c.expect, c.note);
  });
}
for (const c of HOLD.refit) {
  test(`refitShape (the host's refit): ${c.note}`, () => {
    sameParams(refitShape(c.kind, c.params, { pointer: point(c.pointer), from: point(c.from) }), c.expect, c.note);
  });
}

test("stillSince: no samples is the first index; followHold never reads the samples the hold carried", () => {
  assert.equal(stillSince([]), 0);
  const hold = { kind: "line", params: { x1: 0, y1: 0, x2: 100, y2: 0 }, from: { x: 100, y: 0 }, n: 2, tSnap: 900 };
  // the second sample is 100 px from `from` within the window, but it is one the hold carried
  assert.deepEqual(followHold(hold, [{ x: 0, y: 0, t: 0 }, { x: 0, y: 0, t: 850 }]), hold.params);
  assert.equal(followHold(hold, [{ x: 0, y: 0, t: 0 }, { x: 100, y: 0, t: 300 }, { x: 113, y: 0, t: 950 }]), null);
});

// --- editable shapes (docs/dev/handwriting.md "Editable shapes") ---------------------
import { dragHandle, reshapeInk, reshapeStroke, shapeHandles } from "../src/ink/shapes.js";
import { decodeStroke, encodeStroke, newInk, translateStrokes } from "../src/ink/ink.js";

const near2 = (a, b, tol = 0.011) => Math.abs(a - b) <= tol;
const at = (handles, id) => handles.find((h) => h.id === id);
// How far a polyline lies from another: the farthest any sample of either is from the other's path.
const gapTo = (p, a, b) => {
  const dx = b.x - a.x, dy = b.y - a.y, len2 = dx * dx + dy * dy;
  const u = len2 ? Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2)) : 0;
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
};
const polylineGap = (a, b) => {
  const from = (pts, path) => Math.max(...pts.map((p) => Math.min(...path.slice(1).map((q, i) => gapTo(p, path[i], q)))));
  return Math.max(from(a, b), from(b, a));
};

test("shapeHandles: a line's and an arrow's ends, the corners in shapeSamples' order, an ellipse's two axis ends; none for a curve", () => {
  assert.deepEqual(shapeHandles("line", { x1: 1, y1: 2, x2: 30, y2: 40 }), [{ id: "p1", x: 1, y: 2 }, { id: "p2", x: 30, y: 40 }]);
  assert.deepEqual(shapeHandles("arrow", { x1: 1, y1: 2, x2: 30, y2: 40, head: 9 }).map((h) => h.id), ["p1", "p2"]);
  const rect = { cx: 100, cy: 100, w: 40, h: 20, angle: 0 };
  assert.deepEqual(shapeHandles("rect", rect), [{ id: "c0", x: 80, y: 90 }, { id: "c1", x: 120, y: 90 }, { id: "c2", x: 120, y: 110 }, { id: "c3", x: 80, y: 110 }]);
  const samples = shapeSamples("rect", rect, {});
  for (const h of shapeHandles("rect", rect)) assert.ok(samples.some((s) => s.x === h.x && s.y === h.y), `${h.id} is a sample`);
  const tri = { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 };
  assert.deepEqual(shapeHandles("triangle", tri), [{ id: "c0", x: 0, y: 0 }, { id: "c1", x: 40, y: 0 }, { id: "c2", x: 20, y: 30 }]);
  const hex = shapeHandles("polygon", { cx: 0, cy: 0, r: 10, n: 6, angle: 0 });
  assert.equal(hex.length, 6);
  assert.ok(near2(hex[0].x, 10) && near2(hex[0].y, 0) && near2(hex[1].x, 5) && near2(hex[1].y, 8.66));
  const ell = shapeHandles("ellipse", { cx: 50, cy: 50, rx: 30, ry: 10, angle: 90 });
  assert.ok(near2(at(ell, "rx").x, 50) && near2(at(ell, "rx").y, 80), "the long axis's end, turned 90° (down the page)");
  assert.ok(near2(at(ell, "ry").x, 40) && near2(at(ell, "ry").y, 50), "the short axis's end");
  assert.deepEqual(shapeHandles("curve", { x1: 0, y1: 0, x2: 1, y2: 1 }), []);
  assert.deepEqual(shapeHandles("spiral", {}), []);
});

test("dragHandle: an end moves alone, Shift turning it in 15° steps about the other; a triangle's corner alone", () => {
  const line = { x1: 100, y1: 100, x2: 200, y2: 100 };
  assert.deepEqual(dragHandle("line", line, "p2", { x: 220.004, y: 130 }), { x1: 100, y1: 100, x2: 220, y2: 130 });
  assert.deepEqual(dragHandle("line", line, "p1", { x: 90, y: 80 }), { x1: 90, y1: 80, x2: 200, y2: 100 });
  const snapped = dragHandle("line", line, "p2", { x: 200, y: 127 }, { shift: true });   // 15.1° → 15°
  assert.ok(near2(Math.atan2(snapped.y2 - 100, snapped.x2 - 100) * 180 / Math.PI, 15, 0.01));
  assert.ok(near2(Math.hypot(snapped.x2 - 100, snapped.y2 - 100), Math.hypot(100, 27), 0.02), "its length kept");
  assert.equal(dragHandle("arrow", { ...line, head: 12 }, "p2", { x: 150, y: 150 }).head, 12, "an arrow keeps its head");
  const tri = { x1: 0, y1: 0, x2: 40, y2: 0, x3: 20, y3: 30 };
  assert.deepEqual(dragHandle("triangle", tri, "c1", { x: 50, y: 10 }), { x1: 0, y1: 0, x2: 50, y2: 10, x3: 20, y3: 30 });
  assert.equal(dragHandle("line", line, "c0", { x: 0, y: 0 }), line, "a handle it does not have: as it was");
});

test("dragHandle: a rectangle's corner keeps the opposite one and the angle; a polygon stays regular; an ellipse turns, stretches and widens", () => {
  const rect = { cx: 100, cy: 100, w: 40, h: 20, angle: 0 };
  assert.deepEqual(dragHandle("rect", rect, "c2", { x: 140, y: 130 }), { cx: 110, cy: 110, w: 60, h: 40, angle: 0 });
  assert.deepEqual(dragHandle("rect", rect, "c2", { x: 60, y: 70 }), { cx: 70, cy: 80, w: 20, h: 20, angle: 0 }, "past the fixed corner: the box flips over");
  // a turned rectangle: the opposite corner stays where it was, the angle with it
  const turned = { cx: 100, cy: 100, w: 40, h: 20, angle: 20 };
  const fixed = at(shapeHandles("rect", turned), "c0");
  const out = dragHandle("rect", turned, "c2", { x: 140, y: 140 });
  assert.equal(out.angle, 20);
  const moved = at(shapeHandles("rect", out), "c0");
  assert.ok(near2(moved.x, fixed.x, 0.02) && near2(moved.y, fixed.y, 0.02), "c0 stays");
  // a polygon's corner sets its radius and its turn about the centre
  const hex = { cx: 0, cy: 0, r: 10, n: 6, angle: 0 };
  const grown = dragHandle("polygon", hex, "c1", { x: 0, y: 20 });
  assert.deepEqual([grown.cx, grown.cy, grown.r, grown.n], [0, 0, 20, 6]);
  assert.ok(near2(grown.angle, 30), "corner 1 at 90°: the first at 30°");
  const c1 = at(shapeHandles("polygon", grown), "c1");
  assert.ok(near2(c1.x, 0, 0.02) && near2(c1.y, 20, 0.02), "the dragged corner lands under the pointer");
  // an ellipse: the long axis follows rx, ry sets the short one, and a short axis grown past the long one swaps them
  const ell = { cx: 50, cy: 50, rx: 30, ry: 10, angle: 0 };
  assert.deepEqual(dragHandle("ellipse", ell, "rx", { x: 50, y: 90 }), { cx: 50, cy: 50, rx: 40, ry: 10, angle: 90 });
  assert.deepEqual(dragHandle("ellipse", ell, "ry", { x: 70, y: 65 }), { cx: 50, cy: 50, rx: 30, ry: 15, angle: 0 }, "across the long axis only");
  assert.deepEqual(dragHandle("ellipse", ell, "ry", { x: 50, y: 90 }), { cx: 50, cy: 50, rx: 40, ry: 30, angle: 90 }, "past it: the axes swap");
});

test("reshapeStroke writes the stroke again from new params: the shape's polyline over its own time and mean pressure, every other key kept", () => {
  const params = { cx: 150, cy: 250, w: 80, h: 40, angle: 0 };
  const stroke = encodeStroke({ id: "r", ch: "xypt", pen: false, brush: "calligraphy", nib: 30, color: "#123456", t0: 99,
    fill: { color: "#fde047" }, note: "mine", shape: { kind: "rect", params },
    samples: shapeSamples("rect", params, { span: 480, p: 0.62 }).map((s) => ({ ...s, t: s.t + 20 })) });
  const next = dragHandle("rect", params, "c2", { x: 200, y: 290 });
  const out = reshapeStroke(stroke, next);
  assert.deepEqual(out.shape, { kind: "rect", params: next });
  const got = decodeStroke(out);
  assert.ok(polylineGap(got, shapeSamples("rect", next, {})) < 0.01, "the params' polyline, on the 1/100 grid");
  assert.deepEqual([got[0].t, got.at(-1).t], [20, 500], "its own span of time");
  assert.ok(got.every((s) => s.p === 0.62), "its mean pressure");
  assert.deepEqual([out.id, out.ch, out.color, out.t0, out.fill, out.note, out.nib, out.brush, out.pen],
    ["r", "xypt", "#123456", 99, stroke.fill, "mine", 30, "calligraphy", false]);
  assert.equal(reshapeStroke(encodeStroke({ id: "f", ch: "xy", samples: [{ x: 0, y: 0 }] }), next).pts.length, 2, "no shape: as it was");
  // a later move keeps the params and the samples together, as after every other edit
  const ink = { ...newInk(1, 612, 792), strokes: [out] };
  const shifted = translateStrokes(ink, ["r"], 7.5, -3).strokes[0];
  assert.ok(polylineGap(decodeStroke(shifted), shapeSamples("rect", shifted.shape.params, {})) < 0.02);
  // reshapeInk: the named stroke alone, the same group for anything else
  const two = { ...newInk(1, 612, 792), strokes: [stroke, encodeStroke({ id: "x", ch: "xy", samples: [{ x: 1, y: 1 }] })] };
  const edited = reshapeInk(two, "r", next);
  assert.equal(edited.strokes[1], two.strokes[1]);
  assert.deepEqual(edited.strokes[0].shape.params, next);
  assert.equal(reshapeInk(two, "x", next), two, "a stroke without a shape");
  assert.equal(reshapeInk(two, "nope", next), two);
});

test("every kind with handles rebuilds from a drag of each of them: the samples lie on the new params' shape", () => {
  const cases = {
    line: { x1: 100, y1: 200, x2: 180.5, y2: 230.25 },
    arrow: { x1: 100, y1: 200, x2: 220, y2: 240, head: 18 },
    triangle: { x1: 100, y1: 300, x2: 160, y2: 220, x3: 210, y3: 310 },
    rect: { cx: 150, cy: 250, w: 80, h: 40, angle: 20 },
    polygon: { cx: 150, cy: 250, r: 40, n: 6, angle: 10 },
    ellipse: { cx: 150, cy: 250, rx: 50, ry: 20, angle: 160 },
  };
  for (const [kind, params] of Object.entries(cases)) {
    const stroke = encodeStroke({ id: kind, ch: "xyt", pen: false, shape: { kind, params }, samples: shapeSamples(kind, params, { span: 300 }) });
    for (const h of shapeHandles(kind, params)) {
      const next = dragHandle(kind, params, h.id, { x: h.x + 13.37, y: h.y - 6.5 });
      const out = reshapeStroke(stroke, next);
      const gap = polylineGap(decodeStroke(out), shapeSamples(kind, out.shape.params, {}));
      assert.ok(gap < 0.01, `${kind} ${h.id}: ${gap}`);
      assert.equal(out.shape.kind, kind);
    }
  }
});
