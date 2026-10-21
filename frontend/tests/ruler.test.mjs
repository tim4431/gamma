// node --test tests/  (from frontend/) — the ruler (src/ink/ruler.js): its
// edges, the rail a stroke snaps to, the projection and the line it
// commits, the angle steps, the two-finger turn, the ends and the ticks.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  RULER_MIN, RULER_PX, RULER_SNAP_PX, defaultRuler, hostSnap, moveRuler, onBand, onRail, pinchRuler, railLine, rulerDir,
  rulerEdges, rulerEnds, rulerTicks, snapAngle, snapRail, stampLine, stepAngle, stretchRuler, turnRuler,
} from "../src/ink/ruler.js";
import { decodeStroke } from "../src/ink/ink.js";

const near = (a, b, tol = 1e-6, what = "") => assert.ok(Math.abs(a - b) <= tol, `${what} ${a} ≉ ${b}`);
const nearPoint = (p, q, tol = 1e-6, what = "") => { near(p.x, q.x, tol, `${what} x`); near(p.y, q.y, tol, `${what} y`); };
const level = { surface: 1, x: 300, y: 400, angle: 0, length: 400, snap: false };
const k = 2;                          // css px per pt: the band is 18 pt wide, the reach 6 pt
const half = RULER_PX / 2 / k, reach = RULER_SNAP_PX / k;

test("the constants the docs name", () => {
  assert.equal(RULER_PX, 36);
  assert.equal(RULER_SNAP_PX, 12);
  assert.equal(RULER_MIN, 36);
});

test("a ruler's direction runs counterclockwise as seen, page y down; its ends and edges follow", () => {
  nearPoint(rulerDir(level), { x: 1, y: 0 });
  nearPoint(rulerDir({ ...level, angle: 90 }), { x: 0, y: -1 }, 1e-9, "90° points up the page");
  nearPoint(rulerDir({ ...level, angle: 30 }), { x: Math.cos(Math.PI / 6), y: -0.5 }, 1e-9, "30° rises to the right");
  const [a, b] = rulerEnds(level);
  nearPoint(a, { x: 100, y: 400 }, 1e-9, "the start end");
  nearPoint(b, { x: 500, y: 400 }, 1e-9, "the other end");
  const [top, bottom] = rulerEdges(level, k);
  nearPoint(top.origin, { x: 300, y: 400 - half }, 1e-9, "the counterclockwise side's edge is the top of a level ruler");
  nearPoint(bottom.origin, { x: 300, y: 400 + half }, 1e-9);
  nearPoint(top.dir, { x: 1, y: 0 });
  // at 90° the counterclockwise side is the left
  const [left] = rulerEdges({ ...level, angle: 90 }, k);
  nearPoint(left.origin, { x: 300 - half, y: 400 }, 1e-9);
  // the band keeps its width on the screen: twice the zoom, half the points
  near(rulerEdges(level, 4)[0].origin.y, 400 - RULER_PX / 2 / 4);
});

test("a new ruler lies level across the middle of the part of the page in view, four fifths of its width", () => {
  assert.deepEqual(defaultRuler(3, { x: 50, y: 100, width: 500, height: 300 }), { surface: 3, x: 300, y: 250, angle: 0, length: 400, snap: false });
  assert.equal(defaultRuler("sheet", { x: 0, y: 0, width: 20, height: 20 }).length, RULER_MIN, "never shorter than RULER_MIN");
  assert.equal(defaultRuler(1, { x: 0.004, y: 0, width: 100.333, height: 10 }).x, 50.17, "on the 1/100 grid");
});

test("snapRail: a pen on the band takes the nearer edge; any pointer within the reach outside an edge takes that edge", () => {
  const [top, bottom] = rulerEdges(level, k);
  assert.deepEqual(snapRail(level, { x: 250, y: 398 }, k, { band: true }), top, "on the band, above the middle: the top edge");
  assert.deepEqual(snapRail(level, { x: 250, y: 405 }, k, { band: true }), bottom, "below the middle: the bottom edge");
  assert.equal(snapRail(level, { x: 250, y: 398 }, k), null, "on the band, a pointer that may not write there gets none");
  assert.deepEqual(snapRail(level, { x: 250, y: 400 - half - reach + 0.1 }, k), top, "just inside the reach above");
  assert.deepEqual(snapRail(level, { x: 250, y: 400 + half + 1 }, k), bottom, "just outside the bottom edge");
  assert.equal(snapRail(level, { x: 250, y: 400 - half - reach - 0.1 }, k), null, "past the reach: freehand");
  assert.equal(snapRail(level, { x: 99, y: 400 - half - 1 }, k), null, "beyond the start end");
  assert.equal(snapRail(level, { x: 501, y: 400 + half + 1 }, k), null, "beyond the other end");
  assert.deepEqual(snapRail(level, { x: 500.2, y: 400 + half + 1 }, k), bottom, "half a css px of slack at an end");
  // the reach is on the screen: at twice the zoom it is half as many points
  assert.equal(snapRail(level, { x: 250, y: 400 - RULER_PX / 2 / 4 - 4 }, 4), null);
  assert.ok(snapRail(level, { x: 250, y: 400 - RULER_PX / 2 / 4 - 2.9 }, 4));
  // a turned ruler: its own frame
  const turned = { ...level, angle: 90 };
  const [left, right] = rulerEdges(turned, k);
  assert.deepEqual(snapRail(turned, { x: 300 - half - 2, y: 450 }, k), left);
  assert.deepEqual(snapRail(turned, { x: 302, y: 450 }, k, { band: true }), right);
  assert.equal(snapRail(null, { x: 0, y: 0 }, k), null);
  assert.ok(onBand(level, { x: 300, y: 400 + half }, k) && !onBand(level, { x: 300, y: 400 + half + 0.5 }, k), "onBand: the edge is on it");
});

test("onRail projects a sample onto the rail's line and keeps its other keys", () => {
  const [top] = rulerEdges(level, k);
  assert.deepEqual(onRail({ x: 220, y: 380, p: 0.7, t: 12, a: 40, z: 90 }, top), { x: 220, y: 400 - half, p: 0.7, t: 12, a: 40, z: 90 });
  const rail = rulerEdges({ ...level, angle: 45 }, k)[1];
  const q = onRail({ x: 310, y: 390, t: 0 }, rail);
  // on the line: perpendicular to the direction from the origin is zero
  const d = rail.dir;
  near((q.x - rail.origin.x) * d.y - (q.y - rail.origin.y) * d.x, 0, 1e-9, "on the line");
  near(onRail(q, rail).x, q.x, 1e-9, "a projection projects to itself");
});

test("railLine is the extent of the projections, from the end nearer the first sample, on the 1/100 grid", () => {
  const [top] = rulerEdges(level, k);
  const y = 400 - half;
  // a wobbly stroke that goes back and forth along the edge
  const samples = [{ x: 150, y: 391 }, { x: 260, y: 380 }, { x: 330.004, y: 393 }, { x: 290, y: 388 }];
  assert.deepEqual(railLine(samples, top), { x1: 150, y1: y, x2: 330, y2: y });
  // drawn right to left: the line starts at the right
  assert.deepEqual(railLine([{ x: 450, y: 390 }, { x: 300, y: 385 }, { x: 160, y: 395 }], top),
    { x1: 450, y1: y, x2: 160, y2: y }, "from the end nearer the first sample");
  // a stroke that overshoots back past where it began still runs from the end nearer its start
  assert.deepEqual(railLine([{ x: 200, y }, { x: 400, y }, { x: 190, y }], top), { x1: 190, y1: y, x2: 400, y2: y });
  assert.equal(railLine([], top), null);
  const tap = railLine([{ x: 210, y: 395 }], top);
  assert.deepEqual(tap, { x1: 210, y1: y, x2: 210, y2: y }, "a tap is a point of the edge");
  // a turned rail: the line lies along its direction
  const turned = { ...level, angle: 30 }, rail = rulerEdges(turned, k)[0];
  const line = railLine([{ x: 250, y: 420 }, { x: 350, y: 370 }], rail);
  const ang = Math.atan2(-(line.y2 - line.y1), line.x2 - line.x1) * 180 / Math.PI;
  near(ang, 30, 0.02, "the line runs at the ruler's angle");
});

test("snapAngle and stepAngle: 15° steps in [0, 180)", () => {
  assert.equal(snapAngle(37), 30);
  assert.equal(snapAngle(38), 45);
  assert.equal(snapAngle(172), 165);
  assert.equal(snapAngle(173), 0, "180 wraps to 0");
  assert.equal(snapAngle(-8), 165, "nearer −15° than 0°");
  assert.equal(snapAngle(-7.4), 0);
  assert.equal(snapAngle(-8, 45), 0);
  assert.equal(snapAngle(100, 45), 90);
  assert.equal(stepAngle(30, 1), 45);
  assert.equal(stepAngle(37, 1), 45);
  assert.equal(stepAngle(37, -1), 30);
  assert.equal(stepAngle(30, -1), 15);
  assert.equal(stepAngle(0, -1), 165);
  assert.equal(stepAngle(165, 1), 0);
});

test("turnRuler turns about the middle, freely or in 15° steps; moveRuler moves it", () => {
  assert.deepEqual(turnRuler(level, 37.123), { ...level, angle: 37.12 });
  assert.equal(turnRuler({ ...level, angle: 170 }, 20).angle, 10, "past 180 wraps");
  assert.equal(turnRuler({ ...level, angle: 10 }, -20).angle, 170);
  assert.equal(turnRuler(level, 37, { snap: true }).angle, 30);
  assert.equal(turnRuler(level, 90).x, 300, "the middle stays");
  assert.deepEqual(moveRuler(level, 12.345, -3), { ...level, x: 312.35, y: 397 });
});

test("pinchRuler: two fingers turn it by their angle's change and carry it with their midpoint", () => {
  const from = [{ x: 200, y: 400 }, { x: 400, y: 400 }];
  // the fingers turned 30° counterclockwise as seen about their midpoint (300, 400)
  const a = 30 * Math.PI / 180;
  const to = [{ x: 300 - 100 * Math.cos(a), y: 400 + 100 * Math.sin(a) }, { x: 300 + 100 * Math.cos(a), y: 400 - 100 * Math.sin(a) }];
  const r = pinchRuler(level, from, to);
  near(r.angle, 30, 0.01, "turned 30°");
  near(r.x, 300, 0.01); near(r.y, 400, 0.01, "the middle under the midpoint stays");
  // a middle off the midpoint turns about it
  const off = pinchRuler({ ...level, x: 300, y: 300 }, from, to);
  near(off.x, 300 - 100 * Math.sin(a), 0.01, "turned about the fingers' midpoint");
  near(off.y, 400 - 100 * Math.cos(a), 0.01);
  // moved: the midpoint carries it
  const moved = pinchRuler(level, from, from.map((p) => ({ x: p.x + 20, y: p.y - 10 })));
  assert.deepEqual([moved.angle, moved.x, moved.y], [0, 320, 390]);
  // past 180 the angle wraps but the middle follows the fingers' own turn
  const steep = pinchRuler({ ...level, angle: 170, x: 300, y: 300 }, from, to);
  near(steep.angle, 20, 0.01);
  near(steep.x, off.x, 0.01, "the wrap does not fling the middle");
  near(steep.y, off.y, 0.01);
  // with snap: 15° steps
  const b = 37 * Math.PI / 180;
  const snapped = pinchRuler(level, from, [{ x: 300 - 100 * Math.cos(b), y: 400 + 100 * Math.sin(b) }, { x: 300 + 100 * Math.cos(b), y: 400 - 100 * Math.sin(b) }], { snap: true });
  assert.equal(snapped.angle, 30);
});

test("stretchRuler moves one end along the ruler, the other staying, never shorter than RULER_MIN", () => {
  const longer = stretchRuler(level, 1, { x: 560, y: 380 });
  assert.deepEqual([longer.x, longer.y, longer.length], [330, 400, 460], "the far end out to 560, the start at 100");
  const shorter = stretchRuler(level, 0, { x: 200, y: 420 });
  assert.deepEqual([shorter.x, shorter.y, shorter.length], [350, 400, 300], "the start in to 200, the end at 500");
  const tiny = stretchRuler(level, 1, { x: 90, y: 400 });
  assert.deepEqual([tiny.x, tiny.length], [100 + RULER_MIN / 2, RULER_MIN], "past the other end: the shortest ruler");
  // at 90° the ruler points up the page: its start end is the bottom one (y 600), its other end the top (y 200)
  const turned = stretchRuler({ ...level, angle: 90 }, 1, { x: 300, y: 100 });
  assert.deepEqual([turned.x, turned.y, turned.length], [300, 350, 500], "along a turned ruler");
});

test("rulerTicks: points every 10, longer every 50, numbered every 100; millimetres, longer every 5, numbered per centimetre", () => {
  const pt = rulerTicks({ ...level, length: 250 }, "pt", 1.5);
  assert.equal(pt.length, 26);
  assert.deepEqual(pt.slice(0, 6).map((t) => t.size), [2, 0, 0, 0, 0, 1]);
  assert.deepEqual(pt.filter((t) => t.label).map((t) => [t.at, t.label]), [[100, "100"], [200, "200"]], "no number at 0");
  const cm = rulerTicks({ ...level, length: 60 }, "cm", 1.5);
  assert.equal(cm.length, 22, "every millimetre of 60 pt");
  assert.deepEqual(cm.filter((t) => t.label).map((t) => t.label), ["1", "2"]);
  near(cm[10].at, 28.35, 0.001, "a centimetre is 28.35 pt");
  assert.equal(cm[5].size, 1);
  // too dense on the screen: the small ticks go first, then the numbers
  assert.ok(rulerTicks({ ...level, length: 60 }, "cm", 1).every((t) => t.size > 0), "millimetres under 3 px are left out");
  assert.ok(rulerTicks({ ...level, length: 400 }, "pt", 0.2).every((t) => t.size > 0 && !t.label));
});

test("stampLine: the ruler's middle line end to end in the preset's look, an even line written xyt with its shape record", () => {
  const look = { tool: "pen", brush: "brush", color: "#2563eb", size: 2.8, opacity: 1, dash: "dotted", stabilize: 3 };
  const stroke = stampLine({ ...level, angle: 90, length: 300 }, look, { t0: 1700000000000 });
  assert.equal(stroke.ch, "xyt", "no pressure, no width law: the hold stage's constant width");
  assert.equal(stroke.pen, false);
  assert.deepEqual(stroke.shape, { kind: "line", params: { x1: 300, y1: 550, x2: 300, y2: 250 } }, "from the start end, up the page");
  assert.equal(stroke.brush, "brush", "the brush key stays for the Style picker");
  assert.equal(stroke.dash, "dotted");
  assert.equal(stroke.t0, 1700000000000);
  assert.ok(!("stabilize" in stroke), "input settings are no stroke keys");
  const samples = decodeStroke(stroke);
  assert.deepEqual(samples.map((s) => [s.x, s.y, s.t]), [[300, 550, 0], [300, 250, 0]], "two samples, a span of 0");
  const hl = stampLine(level, { tool: "highlighter", color: "#fde047", size: 14, opacity: 0.6 });
  assert.equal(hl.tool, "highlighter");
  assert.equal(hl.opacity, 0.6);
  assert.ok(!("t0" in hl), "no wall clock given, none written");
});

test("hostSnap: the rail's origin in viewport css px and the ruler's angle, for the iPad claim", () => {
  const [top] = rulerEdges(level, k);
  assert.deepEqual(hostSnap(top, 0, { left: 10, top: 20, k }), { x0: 610, y0: 20 + (400 - half) * k, angle: 0 });
  assert.deepEqual(hostSnap(top, 30.004, { left: 0.333, top: 0, k: 1 }), { x0: 300.33, y0: 400 - half, angle: 30 });
});
