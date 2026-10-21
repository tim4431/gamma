// The ruler (docs/dev/handwriting.md "What the user sees" and "Client"),
// pure: a straight edge App keeps as session state and the ink layer draws
// over its surface. A ruler is {surface, x, y, angle, length, snap}: its
// middle in page points, its angle in degrees counterclockwise as seen, in
// [0, 180) — its direction the page vector (cos angle, −sin angle), page y
// running down —, its length in points, and whether the knob turns it in
// 15° steps. Its band is RULER_PX css px wide on the screen at any zoom, so
// a length in points stays one while the band's half-width in points is
// RULER_PX / 2 / k (k: css px per pt). A stroke that starts on the band (a
// pen) or within RULER_SNAP_PX outside an edge is drawn along that edge:
// its samples projected onto the edge's line (a rail, {origin, dir}), the
// committed stroke the line between the two farthest projections.
import { finishStroke, strokeChannels } from "./finishStroke.js";
import { encodeStroke } from "./ink.js";

export const RULER_PX = 36;       // the band's width on the screen, css px
export const RULER_SNAP_PX = 12;  // the reach outside each edge, css px
export const RULER_MIN = 36;      // its shortest length, pt
export const RULER_STEP = 15;     // the knob's snap, degrees

const DEG = Math.PI / 180;
const r2 = (v) => Math.round(v * 100) / 100;
const mod180 = (v) => ((v % 180) + 180) % 180;
// an angle on the 1/100 degree grid, in [0, 180) (180 itself wraps to 0)
const angleOf = (deg) => {
  const a = r2(mod180(deg));
  return a >= 180 ? 0 : a;
};

// The ruler's direction, a unit page vector.
export function rulerDir(ruler) {
  const a = (ruler.angle || 0) * DEG;
  return { x: Math.cos(a), y: -Math.sin(a) };
}
// The normal on the ruler's counterclockwise side as seen (up for a level
// ruler): its direction turned a quarter turn counterclockwise.
const normalOf = (dir) => ({ x: dir.y, y: -dir.x });

// Its two ends, the start (back along its direction) first.
export function rulerEnds(ruler) {
  const d = rulerDir(ruler), h = (ruler.length || 0) / 2;
  return [{ x: ruler.x - d.x * h, y: ruler.y - d.y * h }, { x: ruler.x + d.x * h, y: ruler.y + d.y * h }];
}

// The lines of its two edges at `k` css px per pt, RULER_PX / 2 / k pt
// either side of its middle line: [the counterclockwise side's (the top of
// a level ruler), the other], each {origin: the edge's middle, dir}.
export function rulerEdges(ruler, k = 1) {
  const d = rulerDir(ruler), n = normalOf(d), half = RULER_PX / 2 / k;
  return [
    { origin: { x: ruler.x + n.x * half, y: ruler.y + n.y * half }, dir: d },
    { origin: { x: ruler.x - n.x * half, y: ruler.y - n.y * half }, dir: d },
  ];
}

// A new ruler on `surface`: level across the middle of `visible` ({x, y,
// width, height}, the part of the surface on the screen in page points),
// four fifths of its width long (at least RULER_MIN), turning freely.
export function defaultRuler(surface, visible) {
  const v = visible || { x: 0, y: 0, width: 0, height: 0 };
  return { surface, x: r2(v.x + v.width / 2), y: r2(v.y + v.height / 2), angle: 0,
    length: r2(Math.max(RULER_MIN, v.width * 0.8)), snap: false };
}

// The point in the ruler's own frame: `u` along it from its middle, `v`
// across it, positive on the counterclockwise side.
function local(ruler, point) {
  const d = rulerDir(ruler), n = normalOf(d), rx = point.x - ruler.x, ry = point.y - ruler.y;
  return { u: rx * d.x + ry * d.y, v: rx * n.x + ry * n.y };
}

// Whether a point (page points) lies on the band at `k` css px per pt.
export function onBand(ruler, point, k = 1) {
  const { u, v } = local(ruler, point);
  return Math.abs(u) <= ruler.length / 2 + 0.5 / k && Math.abs(v) <= RULER_PX / 2 / k;
}

// The edge a stroke that starts at `point` is drawn along, or null: the
// nearer edge when the point lies on the band and the pointer may write
// there (`band`: a pen), or the edge it lies within RULER_SNAP_PX / k pt
// outside of — both only between the ruler's ends (half a css px of slack
// at either end, the band's own rounding). → {origin, dir}.
export function snapRail(ruler, point, k = 1, { band = false } = {}) {
  if (!ruler || !point) return null;
  const { u, v } = local(ruler, point);
  if (Math.abs(u) > ruler.length / 2 + 0.5 / k) return null;
  const half = RULER_PX / 2 / k, reach = RULER_SNAP_PX / k, av = Math.abs(v);
  if (av > half + reach) return null;
  if (av <= half && !band) return null;
  const [ccw, cw] = rulerEdges(ruler, k);
  return v >= 0 ? ccw : cw;
}

// How far along the rail a point lies, from its origin.
const along = (s, rail) => (s.x - rail.origin.x) * rail.dir.x + (s.y - rail.origin.y) * rail.dir.y;

// A sample's point projected onto the rail's line, every other key (p, t,
// the tilt) kept.
export function onRail(sample, rail) {
  const t = along(sample, rail);
  return { ...sample, x: rail.origin.x + t * rail.dir.x, y: rail.origin.y + t * rail.dir.y };
}

// The line a stroke along the rail commits as: between the two farthest
// projections of `samples`, from the end nearer the first sample's, on
// the 1/100 grid → {x1, y1, x2, y2} (a shape record's line params), or
// null for no samples.
export function railLine(samples, rail) {
  if (!samples?.length || !rail) return null;
  let lo = Infinity, hi = -Infinity;
  for (const s of samples) {
    const t = along(s, rail);
    if (t < lo) lo = t;
    if (t > hi) hi = t;
  }
  const first = along(samples[0], rail);
  const [a, b] = first - lo <= hi - first ? [lo, hi] : [hi, lo];
  const { origin: o, dir: d } = rail;
  return { x1: r2(o.x + a * d.x), y1: r2(o.y + a * d.y), x2: r2(o.x + b * d.x), y2: r2(o.y + b * d.y) };
}

// An angle snapped to the nearest `step` degrees, in [0, 180).
export function snapAngle(deg, step = RULER_STEP) {
  return angleOf(Math.round(deg / step) * step);
}
// The next `step` along from `deg` in the direction `dir` (±1): from 30 up
// is 45, from 37 up 45 and down 30 — the knob's Shift + arrow.
export function stepAngle(deg, dir, step = RULER_STEP) {
  const q = deg / step;
  const n = dir > 0 ? Math.floor(q + 1e-9) + 1 : Math.ceil(q - 1e-9) - 1;
  return angleOf(n * step);
}

// The ruler turned by `deg` degrees (counterclockwise as seen) about its
// middle; with `snap` the result is on the 15° grid.
export function turnRuler(ruler, deg, { snap = false } = {}) {
  const angle = (ruler.angle || 0) + deg;
  return { ...ruler, angle: snap ? snapAngle(angle) : angleOf(angle) };
}

// The ruler moved by (dx, dy) page points.
export function moveRuler(ruler, dx, dy) {
  return { ...ruler, x: r2(ruler.x + dx), y: r2(ruler.y + dy) };
}

// The ruler turned and moved by two fingers: `from` the two points (page
// points) when they first held it, `to` where they are now. It turns by the
// change of the angle between them and its middle moves as their midpoint
// does, turning about it; `snap` holds the angle to the 15° grid.
export function pinchRuler(ruler, from, to, { snap = false } = {}) {
  const a0 = Math.atan2(from[1].y - from[0].y, from[1].x - from[0].x);
  const a1 = Math.atan2(to[1].y - to[0].y, to[1].x - to[0].x);
  // page y runs down: a positive atan2 change turns clockwise as seen
  const raw = -(a1 - a0) / DEG;
  const next = turnRuler(ruler, raw, { snap });
  // the turn the ruler made, the snap's and the wrap at 180° folded back
  // next to the fingers' own (the middle follows the fingers, not the fold)
  const wrap = (x) => x - 180 * Math.round(x / 180);
  const turn = raw + wrap(next.angle - ((ruler.angle || 0) + raw));
  const m0 = { x: (from[0].x + from[1].x) / 2, y: (from[0].y + from[1].y) / 2 };
  const m1 = { x: (to[0].x + to[1].x) / 2, y: (to[0].y + to[1].y) / 2 };
  // the middle, turned about the first midpoint as the ruler turned (a
  // standard rotation in page coordinates turns clockwise as seen), then
  // carried with it
  const rad = -turn * DEG, c = Math.cos(rad), s = Math.sin(rad);
  const rx = ruler.x - m0.x, ry = ruler.y - m0.y;
  return { ...next, x: r2(m1.x + rx * c - ry * s), y: r2(m1.y + rx * s + ry * c) };
}

// The ruler with one end (0 the start, 1 the other) dragged to `point`:
// that end follows the point's projection onto the ruler's line, the other
// stays, and the length is at least RULER_MIN.
export function stretchRuler(ruler, end, point) {
  const d = rulerDir(ruler), [a, b] = rulerEnds(ruler);
  const fixed = end === 0 ? b : a, sign = end === 0 ? -1 : 1;
  const t = ((point.x - fixed.x) * d.x + (point.y - fixed.y) * d.y) * sign;
  const length = Math.max(RULER_MIN, t);
  return { ...ruler, x: r2(fixed.x + sign * d.x * length / 2), y: r2(fixed.y + sign * d.y * length / 2), length: r2(length) };
}

// What the band draws at `k` css px per pt: ticks measured from the start
// end, [{at (pt along the ruler), size: 0 | 1 | 2, label?}]. Points: a tick
// every 10, longer (1) every 50, numbered (2) every 100. Centimetres: every
// millimetre, longer every 5, numbered every centimetre. A rank of ticks
// closer than 3 css px on the screen is left out, and so are the numbers
// when they would stand closer than 24 px.
const MM = 72 / 25.4;
export function rulerTicks(ruler, units = "pt", k = 1) {
  const cm = units === "cm";
  const unit = cm ? MM : 10, mid = 5, major = 10;
  const length = ruler.length || 0, out = [];
  const minor = unit * k >= 3, labels = unit * major * k >= 24;
  for (let i = 0; i * unit <= length + 1e-6; i++) {
    const size = i % major === 0 ? 2 : i % mid === 0 ? 1 : 0;
    if (size === 0 && !minor) continue;
    const tick = { at: r2(i * unit), size };
    if (size === 2 && labels && i) tick.label = String(cm ? i / major : i * unit);
    out.push(tick);
  }
  return out;
}

// Stamp line: the stroke along the ruler's middle line from its start end
// to the other, in `look` (a preset's style, ink.js toolStyle), through
// finishStroke's hold stage as a held line is — an even line written xyt,
// both samples at t 0 (a span of 0), carrying its `line` shape record —
// with `t0` the wall clock it was stamped at.
export function stampLine(ruler, look, { t0 = null } = {}) {
  const [a, b] = rulerEnds(ruler);
  const params = { x1: r2(a.x), y1: r2(a.y), x2: r2(b.x), y2: r2(b.y) };
  const style = { tool: look.tool || "pen", brush: look.brush, dash: look.dash, color: look.color, size: look.size,
    opacity: look.opacity ?? 1, ...(look.nib != null ? { nib: look.nib } : {}) };
  const { commit, style: shaped } = finishStroke([{ x: a.x, y: a.y, p: 0.5, t: 0 }, { x: b.x, y: b.y, p: 0.5, t: 0 }],
    { style, pen: false, hold: { kind: "line", params } });
  return encodeStroke({ ...shaped, t0, samples: commit, ch: strokeChannels(shaped, commit) });
}

// The claim's `snap` for the iPad shell (docs/dev/ipad.md "The host
// contract"): the rail's origin in viewport css px for the layer whose page
// corner is at (left, top) at k css px per pt, and the ruler's angle, each
// on the 1/100 grid.
export function hostSnap(rail, angle, { left = 0, top = 0, k = 1 } = {}) {
  return { x0: r2(left + rail.origin.x * k), y0: r2(top + rail.origin.y * k), angle: angleOf(angle || 0) };
}
