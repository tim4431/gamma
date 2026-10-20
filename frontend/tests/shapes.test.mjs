// node --test tests/  (from frontend/) — the draw-and-hold recogniser over
// tests/shared/inkshapes.json, the polyline a shape commits as, and the
// refit under a continued hold.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { SHAPE_KINDS, recognise, refitShape, shapeName, shapeSamples } from "../src/ink/shapes.js";

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
