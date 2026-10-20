// Scribble-to-erase (docs/dev/handwriting.md "Scribble to erase" and
// "Client"): is a finished pen stroke a fast dense back-and-forth, and which
// strokes under it go. Pure; finishStroke's scribble stage calls both.
// Fixture: tests/shared/inkscribble.json.
import { strokeBounds } from "./ink.js";

// Thresholds. Those in css px divide by k; the rest are points, ms or ratios.
const MIN_REVERSALS = 4;
const EXCURSION_PX = 4;      // a reversal's excursion on both sides, along the major axis
const TURN_DEG = 120;        // the turn at a reversal, measured TURN_PX before and after
const TURN_PX = 4;
const RUN_RATIO = 2;         // the inner runs between reversals within 2× of each other
const PATH_DIAG = 2.5;       // the path at least 2.5 × the bounding-box diagonal
const FLAT_RATIO = 1.5;      // height ≤ 1.5 × width …
const FLAT_PX = 40;          // … or ≤ 40 css px
const MAX_MS = 2000;
const MIN_SPEED = 150;       // css px / s
const COVER = 0.7;           // a target's box at least 70 % inside the scribble's
const SIZE_RATIO = 3;        // … and its diagonal at most 3 × the scribble's

const DEG = 180 / Math.PI;
const dist = (a, b) => Math.hypot(b.x - a.x, b.y - a.y);

function bbox(pts) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const p of pts) { x0 = Math.min(x0, p.x); y0 = Math.min(y0, p.y); x1 = Math.max(x1, p.x); y1 = Math.max(y1, p.y); }
  return [x0, y0, x1, y1];
}
const boxDiag = ([x0, y0, x1, y1]) => Math.hypot(x1 - x0, y1 - y0);

// The unit direction of the samples' major axis (PCA of the positions).
function majorAxis(pts) {
  let cx = 0, cy = 0;
  for (const p of pts) { cx += p.x; cy += p.y; }
  cx /= pts.length; cy /= pts.length;
  let sxx = 0, syy = 0, sxy = 0;
  for (const p of pts) { const dx = p.x - cx, dy = p.y - cy; sxx += dx * dx; syy += dy * dy; sxy += dx * dy; }
  const tr = sxx + syy, det = sxx * syy - sxy * sxy;
  const l1 = tr / 2 + Math.sqrt(Math.max(0, tr * tr / 4 - det));
  let ux, uy;
  if (Math.abs(sxy) > 1e-9) { ux = l1 - syy; uy = sxy; } else if (sxx >= syy) { ux = 1; uy = 0; } else { ux = 0; uy = 1; }
  const l = Math.hypot(ux, uy) || 1;
  return { ux: ux / l, uy: uy / l };
}

// The indices of the reversals of the projection `s`: an extremum reached
// after at least `eps` of travel and left by at least `eps`.
function reversals(s, eps) {
  const out = [];
  let dir = 0, ext = 0, last = 0;
  for (let i = 1; i < s.length; i++) {
    if (!dir) {
      if (Math.abs(s[i] - s[0]) >= eps) { dir = Math.sign(s[i] - s[0]); ext = i; }
      continue;
    }
    if ((s[i] - s[ext]) * dir > 0) ext = i;
    else if ((s[ext] - s[i]) * dir >= eps && (s[ext] - s[last]) * dir >= eps) {
      out.push(ext);
      last = ext; dir = -dir; ext = i;
    }
  }
  return out;
}
// The path direction `span` points of travel before (sign −1) or after
// (sign +1) index i, as a unit vector, or null at the stroke's ends.
function direction(pts, cum, i, span, sign) {
  let j = i;
  while (j + sign >= 0 && j + sign < pts.length && Math.abs(cum[j] - cum[i]) < span) j += sign;
  if (j === i) return null;
  const a = sign < 0 ? pts[j] : pts[i], b = sign < 0 ? pts[i] : pts[j];
  const l = dist(a, b);
  return l ? { x: (b.x - a.x) / l, y: (b.y - a.y) / l } : null;
}

// samples: one finished stroke's decoded page-pt samples [{x, y, p?, t}];
// k css px per pt; durationMs the stroke's length in time (default: last t
// − first t). → whether it is a scribble.
export function isScribble(samples, { k = 1, durationMs } = {}) {
  if (!samples || samples.length < 2 * MIN_REVERSALS) return false;
  const first = samples[0], last = samples[samples.length - 1];
  const duration = durationMs ?? (last.t != null && first.t != null ? last.t - first.t : NaN);
  if (!Number.isFinite(duration) || duration <= 0 || duration > MAX_MS) return false;
  const [x0, y0, x1, y1] = bbox(samples);
  const w = x1 - x0, h = y1 - y0, diag = Math.hypot(w, h);
  if (!diag) return false;
  if (h > FLAT_RATIO * w && h > FLAT_PX / k) return false;
  const cum = [0];
  for (let i = 1; i < samples.length; i++) cum[i] = cum[i - 1] + dist(samples[i - 1], samples[i]);
  const path = cum[cum.length - 1];
  if (path < PATH_DIAG * diag) return false;
  if ((path * k * 1000) / duration < MIN_SPEED) return false;
  const { ux, uy } = majorAxis(samples);
  const proj = samples.map((p) => p.x * ux + p.y * uy);
  const span = TURN_PX / k;
  const turns = reversals(proj, EXCURSION_PX / k).filter((i) => {
    const before = direction(samples, cum, i, span, -1), after = direction(samples, cum, i, span, 1);
    if (!before || !after) return false;
    const turn = Math.acos(Math.max(-1, Math.min(1, before.x * after.x + before.y * after.y))) * DEG;
    return turn > TURN_DEG;
  });
  if (turns.length < MIN_REVERSALS) return false;
  let lo = Infinity, hi = 0;
  for (let i = 1; i < turns.length; i++) {
    const run = cum[turns[i]] - cum[turns[i - 1]];
    lo = Math.min(lo, run); hi = Math.max(hi, run);
  }
  return hi <= RUN_RATIO * lo;
}

// scribble: the scribble's samples; groups: the page's ink groups in order,
// [{id, ink}]; keep(stroke) → bool the eraser filter (default: every
// stroke). → [{gid, ids}] in group order, ids in file order, groups with no
// target left out: the strokes whose box lies at least 70 % by area inside
// the scribble's, not a highlighter, at most 3 × the scribble's diagonal.
export function scribbleTargets(scribble, groups, { keep } = {}) {
  if (!scribble?.length || !groups?.length) return [];
  const box = bbox(scribble);
  const limit = SIZE_RATIO * boxDiag(box);
  const out = [];
  for (const group of groups) {
    const ids = [];
    for (const stroke of group?.ink?.strokes || []) {
      if (stroke.tool === "highlighter") continue;
      const b = strokeBounds(stroke);
      if (!b || boxDiag(b) > limit) continue;
      const area = (b[2] - b[0]) * (b[3] - b[1]);
      const ix = Math.max(0, Math.min(b[2], box[2]) - Math.max(b[0], box[0]));
      const iy = Math.max(0, Math.min(b[3], box[3]) - Math.max(b[1], box[1]));
      if (!(area > 0) || (ix * iy) / area < COVER) continue;
      if (keep && !keep(stroke)) continue;
      ids.push(stroke.id);
    }
    if (ids.length) out.push({ gid: group.id, ids });
  }
  return out;
}
