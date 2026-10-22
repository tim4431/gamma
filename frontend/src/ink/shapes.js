// The draw-and-hold recogniser (docs/dev/handwriting.md "Draw and hold" and
// "Client"): a stroke held still at its end becomes a line, an arrow, a
// triangle, a rectangle, a regular polygon, a circle or an ellipse. Pure,
// over decoded page-pt samples; InkLayer asks it when its hold timer fires
// (or the iPad host's `hold` comes), finishStroke commits its answer through
// `shapeSamples`, and a continued hold refits the snap through `refitShape`
// (under the host, by `followHold`, the rule its shell runs too). Corners
// come from ShortStraw (Wolin, Eoff, Hammond 2008), the round shapes from
// the central moments (Xournal++'s circle recogniser). Fixtures:
// tests/shared/inkshapes.json, and inkhold.json for the hold's own rules.
import { t } from "../shared/i18n/i18n.js";
import { decodeStroke, encodePoints, mod, round2 } from "./ink.js";

// Draw and hold's numbers, one table for the layer and the iPad host, whose
// Swift constants mirror it (docs/dev/ipad.md "The pen"): the tip still for
// HOLD_MS fires the recogniser, the timer starting again whenever the tip
// lands more than HOLD_MOVE css px from where it last started, and only
// once the stroke had lasted HOLD_MIN_MS by then; held on, a tip REFIT_PX
// css px from where it snapped refits the shape, and one more than
// CANCEL_PX from it within CANCEL_MS of the snap drops it, so a stroke that
// merely paused goes on freehand. The same timer, armed at the down while
// the clipboard holds strokes, opens "Paste here" if the tip has not
// travelled PASTE_MOVE css px by then.
export const HOLD_MS = 600, HOLD_MOVE = 3, HOLD_MIN_MS = 250, REFIT_PX = 6, CANCEL_PX = 12, CANCEL_MS = 150, PASTE_MOVE = 2;

// Thresholds. Those in css px divide by k; the rest are points or ratios.
// The size floor is also the one InkLayer's hold puts under a stroke's
// chord before it asks (holdFire, and the iPad claim's hold).
export const MIN_EXTENT_PX = 24; // a stroke smaller than this on screen …
export const MIN_EXTENT_PT = 18; // … or on the page is never a shape
const MIN_SAMPLES = 8;
const RESAMPLE_DIV = 40;         // ShortStraw spacing: bbox diagonal / 40
const STRAW_W = 3;               // ShortStraw window
const STRAW_T = 0.95;            // a straw under 0.95 × the median is a corner
const CORNER_TURN = 30;          // … when the path really turns there (degrees)
const CLOSE_FRAC = 0.1;          // closed: the ends within 10 % of the diagonal
const LINE_RMS = 0.03;           // a line's samples within 3 % of the chord (RMS)
const LINE_PATH = 1.08;          // … and its path at most 1.08 × the chord
const POLY_RMS = 0.03;           // a polygon's samples within 3 % of the diagonal
const AXIS_SNAP = 5;             // degrees to 0°, 45°, 90°
const ARROW_HEAD = 0.35;         // a barb at most 0.35 × the shaft
const ARROW_NEAR = 0.4;          // … ending within 0.4 × the shaft of the tip
const RECT_ANGLE = 15;           // a corner within 90° ± 15°
const SQUARE_SIDES = 0.1;        // sides within 10 % → a square
const POLY_SIDES = 0.15;         // a regular polygon's sides within 15 %
const CIRCLE_EIGEN = 0.95;       // eigenvalue ratio of the central moments
const CIRCLE_DEV = 0.1;          // radial deviation about the centroid
const ELLIPSE_DEV = 0.12;        // radial deviation about the moment ellipse
const SAMPLE_PT = 2;             // a committed shape's sample spacing
const ELLIPSE_MIN = 48;          // … and a round shape's sample floor
export const SHAPE_KINDS = ["line", "arrow", "triangle", "rect", "polygon", "circle", "ellipse"];

const DEG = 180 / Math.PI;
const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);
// Snap an angle (degrees) to the nearest multiple of `step` when within
// `within` of it; else the angle as given.
function snapAngle(deg, step, within) {
  const near = Math.round(deg / step) * step;
  return Math.abs(deg - near) <= within ? near : deg;
}
// The point `to` turned about `from` to the nearest 15°, its distance kept:
// Shift on a line's or an arrow's end (refitShape, dragHandle and
// shapeTool.js toolShape).
export function snapEnd(from, to) {
  const len = dist(from, to), rad = (Math.round(Math.atan2(to.y - from.y, to.x - from.x) * DEG / 15) * 15) / DEG;
  return { x: from.x + Math.cos(rad) * len, y: from.y + Math.sin(rad) * len };
}

function bbox(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of pts) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
  return { x0, y0, x1, y1, w: x1 - x0, h: y1 - y0, diag: Math.hypot(x1 - x0, y1 - y0) };
}
function pathLength(pts) {
  let n = 0;
  for (let i = 1; i < pts.length; i++) n += dist(pts[i - 1], pts[i]);
  return n;
}
// The polyline resampled at `step` along its length (first point kept, the
// last appended when it is not reached exactly).
function resample(pts, step) {
  const out = [{ x: pts[0].x, y: pts[0].y }];
  let carry = 0;
  for (let i = 1; i < pts.length; i++) {
    let a = pts[i - 1];
    const b = pts[i];
    let d = dist(a, b);
    while (carry + d >= step) {
      const f = (step - carry) / d;
      a = { x: a.x + (b.x - a.x) * f, y: a.y + (b.y - a.y) * f };
      out.push(a);
      d = dist(a, b);
      carry = 0;
    }
    carry += d;
  }
  const last = pts[pts.length - 1];
  if (dist(out[out.length - 1], last) > step / 4) out.push({ x: last.x, y: last.y });
  return out;
}
// The turn (degrees, 0..180) between the directions a→b and b→c.
function turnAt(a, b, c) {
  const ux = b.x - a.x, uy = b.y - a.y, vx = c.x - b.x, vy = c.y - b.y;
  const lu = Math.hypot(ux, uy), lv = Math.hypot(vx, vy);
  if (!lu || !lv) return 0;
  return Math.acos(Math.max(-1, Math.min(1, (ux * vx + uy * vy) / (lu * lv)))) * DEG;
}

// ShortStraw over resampled points: the indices of the inner corners (the
// ends are corners by definition and are not listed). A straw is the
// distance between the points W before and W after; a run of straws under
// 0.95 × the median holds a corner at its shortest, kept when the path turns
// at least CORNER_TURN there (so the wobble of a slow circle is no corner).
function shortStraw(r) {
  const n = r.length, W = STRAW_W;
  if (n < 2 * W + 2) return [];
  const straws = [];
  for (let i = W; i < n - W; i++) straws[i] = dist(r[i - W], r[i + W]);
  const sorted = straws.filter((v) => v !== undefined).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const limit = STRAW_T * median;
  const out = [];
  for (let i = W; i < n - W; i++) {
    if (straws[i] >= limit) continue;
    let best = i;
    while (i < n - W && straws[i] < limit) { if (straws[i] < straws[best]) best = i; i++; }
    if (turnAt(r[Math.max(0, best - W)], r[best], r[Math.min(n - 1, best + W)]) >= CORNER_TURN) out.push(best);
  }
  return out;
}

// Least-squares line through points: the centroid and the unit direction of
// the major axis of the central moments, with the eigenvalues (major, minor).
function moments(pts) {
  let cx = 0, cy = 0;
  for (const p of pts) { cx += p.x; cy += p.y; }
  cx /= pts.length; cy /= pts.length;
  let sxx = 0, syy = 0, sxy = 0;
  for (const p of pts) { const dx = p.x - cx, dy = p.y - cy; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
  sxx /= pts.length; syy /= pts.length; sxy /= pts.length;
  const tr = sxx + syy, det = sxx * syy - sxy * sxy;
  const disc = Math.sqrt(Math.max(0, tr * tr / 4 - det));
  const l1 = tr / 2 + disc, l2 = tr / 2 - disc;
  // eigenvector of l1
  let ux, uy;
  if (Math.abs(sxy) > 1e-9) { ux = l1 - syy; uy = sxy; } else if (sxx >= syy) { ux = 1; uy = 0; } else { ux = 0; uy = 1; }
  const lu = Math.hypot(ux, uy) || 1;
  return { cx, cy, ux: ux / lu, uy: uy / lu, l1, l2 };
}
// RMS distance of points to the line through (cx, cy) with direction (ux, uy).
function rmsToLine(pts, { cx, cy, ux, uy }) {
  let s = 0;
  for (const p of pts) { const d = (p.x - cx) * uy - (p.y - cy) * ux; s += d * d; }
  return Math.sqrt(s / pts.length);
}
// Distance from a point to the segment a–b.
function distToSegment(p, a, b) {
  const vx = b.x - a.x, vy = b.y - a.y, l2 = vx * vx + vy * vy;
  const f = l2 ? Math.max(0, Math.min(1, ((p.x - a.x) * vx + (p.y - a.y) * vy) / l2)) : 0;
  return Math.hypot(p.x - (a.x + vx * f), p.y - (a.y + vy * f));
}
// The intersection of two lines (point + direction), or null when parallel.
function intersect(a, b) {
  const den = a.ux * b.uy - a.uy * b.ux;
  if (Math.abs(den) < 1e-9) return null;
  const s = ((b.cx - a.cx) * b.uy - (b.cy - a.cy) * b.ux) / den;
  return { x: a.cx + a.ux * s, y: a.cy + a.uy * s };
}

// A line's endpoints from the first and last of `pts` projected on the
// least-squares line, snapped to 0°, 45° or 90° about the midpoint.
function fitLine(pts) {
  const m = moments(pts);
  const first = pts[0], last = pts[pts.length - 1];
  const s0 = (first.x - m.cx) * m.ux + (first.y - m.cy) * m.uy;
  const s1 = (last.x - m.cx) * m.ux + (last.y - m.cy) * m.uy;
  const [a, b] = snapSegment({ x: m.cx + m.ux * s0, y: m.cy + m.uy * s0 }, { x: m.cx + m.ux * s1, y: m.cy + m.uy * s1 }, 45, AXIS_SNAP);
  return { x1: a.x, y1: a.y, x2: b.x, y2: b.y, rms: rmsToLine(pts, m) };
}
// Rotate the segment a–b about its midpoint so its angle sits on the
// nearest multiple of `step` when within `within` degrees of it.
function snapSegment(a, b, step, within) {
  const ang = Math.atan2(b.y - a.y, b.x - a.x) * DEG;
  const snapped = snapAngle(ang, step, within);
  if (snapped === ang) return [a, b];
  const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2, half = dist(a, b) / 2;
  const rad = snapped / DEG, dx = Math.cos(rad) * half, dy = Math.sin(rad) * half;
  return [{ x: mx - dx, y: my - dy }, { x: mx + dx, y: my + dy }];
}

// The corners of a closed polygon as the intersections of its sides'
// least-squares lines (`runs` the resampled points of each side, in order).
// Null when a side has too few points or two sides are parallel.
function polygonCorners(runs) {
  const lines = runs.map((run) => (run.length >= 2 ? moments(run) : null));
  if (lines.some((l) => !l)) return null;
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const p = intersect(lines[(i + lines.length - 1) % lines.length], lines[i]);
    if (!p) return null;
    out.push(p);
  }
  return out;
}
// The points of each side between consecutive corner indices (cyclic when
// `closed`), the W points nearest each corner left out when the side allows.
function sideRuns(r, corners, closed) {
  const runs = [];
  const m = closed ? corners.length : corners.length - 1;
  for (let i = 0; i < m; i++) {
    const a = corners[i], b = corners[(i + 1) % corners.length];
    const pts = [];
    if (closed && b <= a) { for (let j = a; j < r.length; j++) pts.push(r[j]); for (let j = 0; j <= b; j++) pts.push(r[j]); }
    else for (let j = a; j <= b; j++) pts.push(r[j]);
    const trim = pts.length > 4 * STRAW_W ? STRAW_W : pts.length > 2 * STRAW_W ? 1 : 0;
    runs.push(pts.slice(trim, pts.length - trim));
  }
  return runs;
}
// RMS distance of the resampled points to the closed polygon's sides.
function rmsToPolygon(r, corners) {
  let s = 0;
  for (const p of r) {
    let best = Infinity;
    for (let i = 0; i < corners.length; i++) best = Math.min(best, distToSegment(p, corners[i], corners[(i + 1) % corners.length]));
    s += best * best;
  }
  return Math.sqrt(s / r.length);
}
const sideLengths = (c) => c.map((p, i) => dist(p, c[(i + 1) % c.length]));
const centroidOf = (c) => ({ x: c.reduce((s, p) => s + p.x, 0) / c.length, y: c.reduce((s, p) => s + p.y, 0) / c.length });

// A rectangle from four corners in drawing order: centre, the side along
// `angle` (w) and across it (h), angle in [-45, 45) and 0 when within the
// axis snap; a square (sides within 10 %) takes the mean side for both.
function rectFrom(corners) {
  const sides = sideLengths(corners);
  for (let i = 0; i < 4; i++) {
    const turn = turnAt(corners[i], corners[(i + 1) % 4], corners[(i + 2) % 4]);
    if (Math.abs(turn - 90) > RECT_ANGLE) return null;
  }
  // mean side direction, each reduced mod 90 next to the first
  const dirs = corners.map((p, i) => { const q = corners[(i + 1) % 4]; return Math.atan2(q.y - p.y, q.x - p.x) * DEG; });
  const ref = dirs[0];
  let angle = dirs.reduce((s, d) => s + (ref + (mod(d - ref + 45, 90) - 45)), 0) / 4;
  angle = mod(angle + 45, 90) - 45;
  if (snapAngle(angle, 90, AXIS_SNAP) !== angle) angle = 0;
  const along = dirs.map((d) => Math.abs(mod(d - angle + 90, 180) - 90) < 45);
  let w = 0, h = 0, nw = 0, nh = 0;
  sides.forEach((s, i) => { if (along[i]) { w += s; nw++; } else { h += s; nh++; } });
  if (!nw || !nh) return null;
  w /= nw; h /= nh;
  const c = centroidOf(corners);
  if (Math.abs(w - h) / Math.max(w, h) <= SQUARE_SIDES) w = h = (w * nw + h * nh) / 4;
  return normaliseRect({ cx: c.x, cy: c.y, w, h, angle });
}
function normaliseRect({ cx, cy, w, h, angle }) {
  let a = mod(round2(angle) + 45, 90) - 45;
  // the side along the angle stays w: a quarter turn swaps the pair
  const quarter = Math.round((angle - a) / 90);
  if (quarter % 2) [w, h] = [h, w];
  if (Math.abs(a) < 1e-9) a = 0;
  return { cx, cy, w, h, angle: a };
}
function normalisePolygon({ cx, cy, r, n, angle }) {
  return { cx, cy, r, n, angle: mod(round2(angle), 360 / n) };
}
function normaliseEllipse({ cx, cy, rx, ry, angle }) {
  if (rx < ry) { [rx, ry] = [ry, rx]; angle += 90; }
  return { cx, cy, rx, ry, angle: mod(round2(angle), 180) };
}
// A regular polygon of 5–8 sides: convex, sides within 15 %, turns one way.
function polygonFrom(corners) {
  const n = corners.length;
  if (n < 5 || n > 8) return null;
  const sides = sideLengths(corners);
  if (Math.max(...sides) / Math.min(...sides) > 1 + POLY_SIDES) return null;
  let sign = 0;
  for (let i = 0; i < n; i++) {
    const a = corners[i], b = corners[(i + 1) % n], c = corners[(i + 2) % n];
    const cross = (b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x);
    const s = Math.sign(cross);
    if (!s) return null;
    if (sign && s !== sign) return null;
    sign = s;
  }
  const c = centroidOf(corners);
  const r = corners.reduce((s, p) => s + dist(c, p), 0) / n;
  const angle = Math.atan2(corners[0].y - c.y, corners[0].x - c.x) * DEG;
  return normalisePolygon({ cx: c.x, cy: c.y, r, n, angle });
}

// The round shapes from the resampled points' central moments: a circle when
// the moments are isotropic and the radii about the centroid even, else an
// ellipse (axes from a least-squares fit in the moment frame) when the
// radii about it are even enough; else null.
function roundFrom(r) {
  const m = moments(r);
  if (m.l1 <= 0) return null;
  const radii = r.map((p) => Math.hypot(p.x - m.cx, p.y - m.cy));
  const mean = radii.reduce((s, v) => s + v, 0) / radii.length;
  const dev = Math.sqrt(radii.reduce((s, v) => s + (v - mean) ** 2, 0) / radii.length) / mean;
  if (m.l2 / m.l1 >= CIRCLE_EIGEN && dev <= CIRCLE_DEV) return { kind: "circle", params: { cx: m.cx, cy: m.cy, r: mean } };
  // axis frame: u along the major axis, v across; fit a u² + b v² = 1
  let suu = 0, svv = 0, suv = 0, su = 0, sv = 0;
  const uv = r.map((p) => {
    const dx = p.x - m.cx, dy = p.y - m.cy;
    const u = dx * m.ux + dy * m.uy, v = -dx * m.uy + dy * m.ux;
    const u2 = u * u, v2 = v * v;
    suu += u2 * u2; svv += v2 * v2; suv += u2 * v2; su += u2; sv += v2;
    return [u2, v2];
  });
  const den = suu * svv - suv * suv;
  if (Math.abs(den) < 1e-12) return null;
  const a = (su * svv - sv * suv) / den, b = (sv * suu - su * suv) / den;
  if (a <= 0 || b <= 0) return null;
  let s = 0;
  for (const [u2, v2] of uv) s += (Math.sqrt(a * u2 + b * v2) - 1) ** 2;
  if (Math.sqrt(s / uv.length) > ELLIPSE_DEV) return null;
  const angle = Math.atan2(m.uy, m.ux) * DEG;
  return { kind: "ellipse", params: normaliseEllipse({ cx: m.cx, cy: m.cy, rx: 1 / Math.sqrt(a), ry: 1 / Math.sqrt(b), angle }) };
}

// An arrow from an open stroke: the tip is the resampled point farthest from
// the tail, the shaft (tail to tip) must fit a line, and the head — the rest
// of the path — stays near the tip, within 0.4 × the shaft, with its points
// on both sides of the shaft line; a barb (the farthest head point on each
// side) is at most 0.35 × the shaft and the head's path two to four barb
// lengths (tip → barb → tip → barb, or a V), so a hook or a loop is none.
// ShortStraw cannot place corners along a head a few samples long, which is
// why the head is read from distances rather than from its corners.
function arrowFrom(samples) {
  // the raw samples: the resampled ones can sit a step short of the tip
  const r = samples, tail = r[0];
  let tipIdx = 0, far = 0;
  for (let i = 1; i < r.length; i++) { const d = dist(tail, r[i]); if (d > far) { far = d; tipIdx = i; } }
  if (tipIdx < 2 || tipIdx >= r.length - 2) return null;
  const tip = r[tipIdx], shaft = far;
  const sx = (tip.x - tail.x) / shaft, sy = (tip.y - tail.y) / shaft;
  const shaftPts = r.slice(0, tipIdx + 1);
  if (pathLength(shaftPts) > LINE_PATH * shaft) return null;
  const line = fitLine(shaftPts);
  if (line.rms > LINE_RMS * shaft) return null;
  let left = null, right = null, leftD = 0, rightD = 0;
  for (let i = tipIdx + 1; i < r.length; i++) {
    const q = r[i], d = dist(q, tip);
    if (d > ARROW_NEAR * shaft) return null;
    const cross = (q.x - tip.x) * sy - (q.y - tip.y) * sx;
    if (Math.abs(cross) < 0.05 * shaft) continue;
    if (cross > 0) { if (d > leftD) { leftD = d; left = q; } } else if (d > rightD) { rightD = d; right = q; }
  }
  if (!left || !right) return null;
  const head = (leftD + rightD) / 2;
  if (Math.max(leftD, rightD) > ARROW_HEAD * shaft || head < Math.max(6, 0.08 * shaft)) return null;
  const headPath = pathLength(r.slice(tipIdx));
  if (headPath < 1.8 * head || headPath > 4.5 * head) return null;
  return { kind: "arrow", params: { x1: line.x1, y1: line.y1, x2: line.x2, y2: line.y2, head } };
}

const rounded = (params) => Object.fromEntries(Object.entries(params).map(([k, v]) => [k, k === "n" ? v : round2(v)]));

// samples: decoded page-pt samples [{x, y, p?, t?}] as the layer holds them
// when the hold timer fires; k css px per pt; kinds (optional) the kinds
// allowed, ['line'] for a highlighter. → {kind, params} | null.
export function recognise(samples, { k = 1, kinds } = {}) {
  if (!samples || samples.length < MIN_SAMPLES) return null;
  const box = bbox(samples);
  // the stroke's extent: its bounding-box diagonal (a closed shape has no
  // chord to speak of), under the floor nothing is recognised
  if (box.diag < Math.max(MIN_EXTENT_PX / k, MIN_EXTENT_PT)) return null;
  const r = resample(samples, box.diag / RESAMPLE_DIV);
  if (r.length < 2 * STRAW_W + 2) return null;
  const chord = dist(r[0], r[r.length - 1]);
  const path = pathLength(r);
  const closed = chord <= CLOSE_FRAC * box.diag;
  const only = (kind) => !kinds || kinds.includes(kind);

  if (!closed) {
    // a line: straight enough and no longer than its chord allows
    if (only("line") && path <= LINE_PATH * chord) {
      const line = fitLine(r);
      if (line.rms <= LINE_RMS * chord) return { kind: "line", params: rounded({ x1: line.x1, y1: line.y1, x2: line.x2, y2: line.y2 }) };
    }
    if (!only("arrow")) return null;
    const arrow = arrowFrom(samples);
    return arrow ? { kind: "arrow", params: rounded(arrow.params) } : null;
  }

  // closed: the inner corners, plus the join when the path turns there
  const inner = shortStraw(r);
  const n = r.length, W = STRAW_W;
  const joinTurn = turnAt(r[Math.max(0, n - 1 - W)], { x: (r[0].x + r[n - 1].x) / 2, y: (r[0].y + r[n - 1].y) / 2 }, r[Math.min(n - 1, W)]);
  const corners = joinTurn >= CORNER_TURN && (inner.length === 0 || (inner[0] > W && inner[inner.length - 1] < n - 1 - W)) ? [0, ...inner] : inner;
  if (corners.length >= 3 && corners.length <= 8) {
    const pts = polygonCorners(sideRuns(r, corners, true));
    if (pts && rmsToPolygon(r, pts) <= POLY_RMS * box.diag) {
      let shape = null;
      if (pts.length === 3 && only("triangle")) {
        // in drawing order from the corner nearest the first sample
        let start = 0;
        for (let i = 1; i < 3; i++) if (dist(pts[i], samples[0]) < dist(pts[start], samples[0])) start = i;
        const o = [pts[start], pts[(start + 1) % 3], pts[(start + 2) % 3]];
        shape = { kind: "triangle", params: { x1: o[0].x, y1: o[0].y, x2: o[1].x, y2: o[1].y, x3: o[2].x, y3: o[2].y } };
      } else if (pts.length === 4 && only("rect")) {
        const rect = rectFrom(pts);
        if (rect) shape = { kind: "rect", params: rect };
      } else if (pts.length >= 5 && only("polygon")) {
        const poly = polygonFrom(pts);
        if (poly) shape = { kind: "polygon", params: poly };
      }
      if (shape) return { kind: shape.kind, params: rounded(shape.params) };
    }
  }
  const round = roundFrom(r);
  return round && only(round.kind) ? { kind: round.kind, params: rounded(round.params) } : null;
}

// --- the committed polyline ---------------------------------------------------

// The corners of a shape as a polygon in order, closed (first again last).
function shapeCorners(kind, p) {
  if (kind === "triangle") return [{ x: p.x1, y: p.y1 }, { x: p.x2, y: p.y2 }, { x: p.x3, y: p.y3 }];
  if (kind === "rect") {
    const rad = (p.angle || 0) / DEG, c = Math.cos(rad), s = Math.sin(rad), hw = p.w / 2, hh = p.h / 2;
    return [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]].map(([u, v]) => ({ x: p.cx + u * c - v * s, y: p.cy + u * s + v * c }));
  }
  if (kind === "polygon") {
    const n = Math.max(3, Math.round(p.n));
    return Array.from({ length: n }, (_, i) => {
      const a = ((p.angle || 0) + (360 / n) * i) / DEG;
      return { x: p.cx + p.r * Math.cos(a), y: p.cy + p.r * Math.sin(a) };
    });
  }
  return null;
}
// Points along the polyline `pts` with a sample at each corner and one every
// `step` along each side (closed when asked: the first corner comes again).
function densify(pts, step, closed) {
  const out = [];
  const m = closed ? pts.length : pts.length - 1;
  for (let i = 0; i < m; i++) {
    const a = pts[i], b = pts[(i + 1) % pts.length];
    const n = Math.max(1, Math.ceil(dist(a, b) / step));
    for (let j = 0; j < n; j++) out.push({ x: a.x + (b.x - a.x) * j / n, y: a.y + (b.y - a.y) * j / n });
  }
  out.push(closed ? { x: pts[0].x, y: pts[0].y } : { x: pts[pts.length - 1].x, y: pts[pts.length - 1].y });
  return out;
}
function arrowPolyline(p) {
  const tail = { x: p.x1, y: p.y1 }, tip = { x: p.x2, y: p.y2 };
  const len = dist(tail, tip) || 1, head = p.head ?? Math.min(len / 4, 24);
  const bx = (tail.x - tip.x) / len, by = (tail.y - tip.y) / len;   // back along the shaft
  const barb = (sign) => {
    const a = (sign * 30) / DEG, c = Math.cos(a), s = Math.sin(a);
    return { x: tip.x + head * (bx * c - by * s), y: tip.y + head * (bx * s + by * c) };
  };
  return [tail, tip, barb(1), tip, barb(-1)];
}

// The dense polyline a shape commits as, t spread evenly 0..span, p
// constant: a line two samples; an arrow tail → tip → barb → tip → barb; a
// triangle, rectangle or polygon its corners with a sample every 2 pt,
// closed; a circle or ellipse at least 48 samples, closed.
export function shapeSamples(kind, params, { span = 0, p = 0.5 } = {}) {
  let pts;
  if (kind === "line") pts = [{ x: params.x1, y: params.y1 }, { x: params.x2, y: params.y2 }];
  else if (kind === "arrow") pts = densify(arrowPolyline(params), SAMPLE_PT, false);
  else if (kind === "circle" || kind === "ellipse") {
    const rx = kind === "circle" ? params.r : params.rx, ry = kind === "circle" ? params.r : params.ry;
    const rad = (kind === "circle" ? 0 : params.angle || 0) / DEG, c = Math.cos(rad), s = Math.sin(rad);
    const h = ((rx - ry) / (rx + ry)) ** 2 || 0;
    const perimeter = Math.PI * (rx + ry) * (1 + (3 * h) / (10 + Math.sqrt(4 - 3 * h)));
    const n = Math.max(ELLIPSE_MIN, Math.ceil(perimeter / SAMPLE_PT));
    pts = Array.from({ length: n + 1 }, (_, i) => {
      const a = (2 * Math.PI * (i % n)) / n, u = rx * Math.cos(a), v = ry * Math.sin(a);
      return { x: params.cx + u * c - v * s, y: params.cy + u * s + v * c };
    });
  } else {
    const corners = shapeCorners(kind, params);
    if (!corners) return [];
    pts = densify(corners, SAMPLE_PT, true);
  }
  const last = pts.length - 1;
  return pts.map((q, i) => ({ x: round2(q.x), y: round2(q.y), p, t: last ? Math.round((span * i) / last) : 0 }));
}

// --- the continued hold -----------------------------------------------------------

const shapeCentre = (kind, p) => {
  if (kind === "triangle") return { x: (p.x1 + p.x2 + p.x3) / 3, y: (p.y1 + p.y2 + p.y3) / 3 };
  return { x: p.cx, y: p.cy };
};
const rotate = (c, q, rad, s = 1) => {
  const dx = (q.x - c.x) * s, dy = (q.y - c.y) * s, co = Math.cos(rad), si = Math.sin(rad);
  return { x: c.x + dx * co - dy * si, y: c.y + dx * si + dy * co };
};

// The shape under a continued hold, from the snap's params (never an
// already-refit set): a line or arrow takes `pointer` as its new end; a
// closed shape scales about its centre by |pointer − centre| / |from −
// centre| and rotates by the angle from `from` to `pointer`; Shift snaps
// the direction / rotation to 15°; a rectangle or triangle keeps its
// proportions. Normalised as `recognise` returns it.
export function refitShape(kind, params, { pointer, from, shift = false }) {
  if (kind === "line" || kind === "arrow") {
    const b = shift ? snapEnd({ x: params.x1, y: params.y1 }, pointer) : pointer;
    const out = { x1: params.x1, y1: params.y1, x2: round2(b.x), y2: round2(b.y) };
    if (kind === "arrow") out.head = params.head;
    return out;
  }
  const c = shapeCentre(kind, params);
  const origin = from || pointer;
  const d0 = dist(c, origin), d1 = dist(c, pointer);
  const s = d0 > 1e-6 ? d1 / d0 : 1;
  let deg = d0 > 1e-6 ? (Math.atan2(pointer.y - c.y, pointer.x - c.x) - Math.atan2(origin.y - c.y, origin.x - c.x)) * DEG : 0;
  if (shift) deg = Math.round(deg / 15) * 15;
  const rad = deg / DEG;
  if (kind === "triangle") {
    const [a, b, e] = [[params.x1, params.y1], [params.x2, params.y2], [params.x3, params.y3]].map(([x, y]) => rotate(c, { x, y }, rad, s));
    return rounded({ x1: a.x, y1: a.y, x2: b.x, y2: b.y, x3: e.x, y3: e.y });
  }
  if (kind === "rect") return rounded(normaliseRect({ cx: params.cx, cy: params.cy, w: params.w * s, h: params.h * s, angle: (params.angle || 0) + deg }));
  if (kind === "polygon") return rounded(normalisePolygon({ cx: params.cx, cy: params.cy, r: params.r * s, n: params.n, angle: (params.angle || 0) + deg }));
  if (kind === "circle") return rounded({ cx: params.cx, cy: params.cy, r: params.r * s });
  if (kind === "ellipse") return rounded(normaliseEllipse({ cx: params.cx, cy: params.cy, rx: params.rx * s, ry: params.ry * s, angle: (params.angle || 0) + deg }));
  return params;
}

// --- the iPad host's hold ---------------------------------------------------------
// The shell owns the Pencil, so it keeps the hold's timer itself and sends
// the samples so far when it fires (docs/dev/ipad.md "The pen"); these two
// rules are the ones its Swift side runs over the same samples
// (StrokeRecorder's anchor, ShapeHold), held to tests/shared/inkhold.json.
// Samples are [{x, y, t}] in the host's viewport css px, t in ms from the
// first sample.

// The stillness anchor: the index of the latest sample that landed more
// than HOLD_MOVE from the anchor before it, the first sample to start with.
// The hold fires HOLD_MS after the anchor's sample.
export function stillSince(samples) {
  let anchor = 0;
  for (let i = 1; i < (samples?.length || 0); i++) {
    const a = samples[anchor], s = samples[i];
    if (Math.hypot(s.x - a.x, s.y - a.y) > HOLD_MOVE) anchor = i;
  }
  return anchor;
}

// The shape a hold comes to by the end of `samples` (the whole stroke):
// `hold` is {kind, params, from: {x, y}, n, tSnap} — the snap's params,
// the last sample the hold carried, how many it carried, and the anchor's
// t + HOLD_MS. Of the samples from index n on, one more than CANCEL_PX from
// `from` while t − tSnap ≤ CANCEL_MS drops the shape (null: the stroke is
// freehand); once one has been REFIT_PX from `from`, the shape is the snap
// refit to the last sample (refitShape, no Shift); else it is the snap.
export function followHold(hold, samples) {
  const { kind, params, from, n, tSnap } = hold;
  let refit = false;
  for (let i = n; i < samples.length; i++) {
    const s = samples[i], moved = Math.hypot(s.x - from.x, s.y - from.y);
    if (moved > CANCEL_PX && s.t - tSnap <= CANCEL_MS) return null;
    if (moved >= REFIT_PX) refit = true;
  }
  return refit ? refitShape(kind, params, { pointer: samples[samples.length - 1], from }) : params;
}

// --- editable shapes ---------------------------------------------------------------
// A shape held alone in the selection shows its own handles
// (docs/dev/handwriting.md "Editable shapes"): its params are the
// geometry, and a drag of a handle gives new params, from which the
// stroke's samples are written again. Every edit that moves the samples
// carries the params with them (ink.js mapShape), so they always describe
// the samples drawn.

// The handles of a shape, [{id, x, y}] in page points: `p1` and `p2`, a
// line's or an arrow's ends (x1, y1 and x2, y2); `c0`, `c1`… a rectangle's,
// a triangle's or a polygon's corners, in shapeSamples' order; `rx` and
// `ry`, an ellipse's long- and short-axis ends. None for a curve or a kind
// this build does not know.
export function shapeHandles(kind, params) {
  const p = params || {};
  if (kind === "line" || kind === "arrow") return [{ id: "p1", x: p.x1, y: p.y1 }, { id: "p2", x: p.x2, y: p.y2 }];
  if (kind === "rect" || kind === "triangle" || kind === "polygon") {
    return (shapeCorners(kind, p) || []).map((q, i) => ({ id: `c${i}`, x: q.x, y: q.y }));
  }
  if (kind === "ellipse") {
    const rad = (p.angle || 0) / DEG, c = Math.cos(rad), s = Math.sin(rad);
    return [{ id: "rx", x: p.cx + p.rx * c, y: p.cy + p.rx * s }, { id: "ry", x: p.cx - p.ry * s, y: p.cy + p.ry * c }];
  }
  return [];
}

// The params with handle `id` at `point` (page points): a line's or an
// arrow's end moves alone, Shift turning it in 15° steps about the other
// end, its length kept; a triangle's corner moves alone; a rectangle's
// corner moves with the opposite one fixed and the angle kept; a polygon's
// corner sets its radius and its turn, the polygon staying regular about
// its centre; `rx` sets the ellipse's long axis, its length and its
// direction, and `ry` its short one's length (the pointer's distance from
// the centre across the long axis). Normalised as `recognise` returns it,
// every number on the 1/100 grid; the params as they were for a handle
// the shape does not have.
const MIN_SIDE = 0.5;   // pt: a dragged rectangle or ellipse keeps at least this much of each side
export function dragHandle(kind, params, id, point, { shift = false } = {}) {
  const p = params || {};
  if ((kind === "line" || kind === "arrow") && (id === "p1" || id === "p2")) {
    const { x, y } = shift ? snapEnd(id === "p1" ? { x: p.x2, y: p.y2 } : { x: p.x1, y: p.y1 }, point) : point;
    return rounded(id === "p1" ? { ...p, x1: x, y1: y } : { ...p, x2: x, y2: y });
  }
  const corner = /^c(\d+)$/.exec(id || "");
  if (kind === "triangle" && corner && +corner[1] < 3) {
    const n = +corner[1] + 1;
    return rounded({ ...p, [`x${n}`]: point.x, [`y${n}`]: point.y });
  }
  if (kind === "rect" && corner && +corner[1] < 4) {
    const fixed = shapeCorners("rect", p)[(+corner[1] + 2) % 4];
    const rad = (p.angle || 0) / DEG, c = Math.cos(rad), s = Math.sin(rad);
    const dx = point.x - fixed.x, dy = point.y - fixed.y;
    const w = Math.max(MIN_SIDE, Math.abs(dx * c + dy * s)), h = Math.max(MIN_SIDE, Math.abs(-dx * s + dy * c));
    return rounded(normaliseRect({ cx: fixed.x + dx / 2, cy: fixed.y + dy / 2, w, h, angle: p.angle || 0 }));
  }
  if (kind === "polygon" && corner && +corner[1] < Math.max(3, Math.round(p.n))) {
    const n = Math.max(3, Math.round(p.n));
    const r = Math.max(MIN_SIDE, Math.hypot(point.x - p.cx, point.y - p.cy));
    const angle = Math.atan2(point.y - p.cy, point.x - p.cx) * DEG - (360 / n) * +corner[1];
    return rounded(normalisePolygon({ cx: p.cx, cy: p.cy, r, n, angle }));
  }
  if (kind === "ellipse" && (id === "rx" || id === "ry")) {
    const dx = point.x - p.cx, dy = point.y - p.cy;
    if (id === "rx") {
      const rx = Math.max(MIN_SIDE, Math.hypot(dx, dy));
      return rounded(normaliseEllipse({ cx: p.cx, cy: p.cy, rx, ry: p.ry, angle: rx > MIN_SIDE ? Math.atan2(dy, dx) * DEG : p.angle || 0 }));
    }
    const rad = (p.angle || 0) / DEG;
    const ry = Math.max(MIN_SIDE, Math.abs(-dx * Math.sin(rad) + dy * Math.cos(rad)));
    return rounded(normaliseEllipse({ cx: p.cx, cy: p.cy, rx: p.rx, ry, angle: p.angle || 0 }));
  }
  return params;
}

// `stroke` (one with a shape record) written again from `params`: the
// shape's dense polyline (shapeSamples) over the stroke's own span of
// time — from its first sample's t to its last's — and at its mean
// pressure, encoded with its own channels, its `shape` the new params and
// every other key kept (id, look, t0, fill, nib, extras): the XY-only
// rewrite a handle's drag commits, so the params and the samples agree as
// after every other edit. The same stroke when there is nothing to draw
// (a kind shapeSamples does not know).
export function reshapeStroke(stroke, params) {
  const kind = stroke?.shape?.kind;
  const old = decodeStroke(stroke);
  if (!kind || !old.length) return stroke;
  const t0 = old[0].t ?? 0, span = Math.max(0, (old[old.length - 1].t ?? 0) - t0);
  const p = old.some((s) => s.p != null) ? round2(old.reduce((sum, s) => sum + (s.p ?? 0.5), 0) / old.length) : 0.5;
  const samples = shapeSamples(kind, params, { span, p });
  if (!samples.length) return stroke;
  const ch = stroke.ch || "xy";
  return { ...stroke, shape: { ...stroke.shape, params: { ...params } }, pts: encodePoints(samples.map((s) => ({ ...s, t: s.t + t0 })), ch) };
}
// A group with the stroke `id` reshaped to `params` (reshapeStroke), or
// the same group when it has no such stroke with a shape.
export function reshapeInk(ink, id, params) {
  let changed = false;
  const strokes = (ink?.strokes || []).map((s) => {
    if (s.id !== id || !s.shape) return s;
    const next = reshapeStroke(s, params);
    if (next !== s) changed = true;
    return next;
  });
  return changed ? { ...ink, strokes } : ink;
}

// The kind's name for the "Shape: circle." pill.
export function shapeName(kind) {
  switch (kind) {
    case "line": return t("line");
    case "arrow": return t("arrow");
    case "triangle": return t("triangle");
    case "rect": return t("rectangle");
    case "polygon": return t("polygon");
    case "circle": return t("circle");
    case "ellipse": return t("ellipse");
    default: return kind;
  }
}
