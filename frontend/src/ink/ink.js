// Handwriting: the client half of gamma/ink.py (docs/dev/handwriting.md).
// Pure — no React, no network — so it runs under node --test.
//
// An ink group is one `gamma-ink` file: strokes on one PDF page, in the
// page's scale-1 frame (pdf.js viewport at scale 1: points, origin
// top-left, rotation applied — the same frame highlight rects normalise
// to). Samples are stored per stroke as one flat integer array, `ch`
// naming the channels ("xy" + any of p/t/a/z); x, y (1/100 pt) and t (ms)
// are deltas after the first sample, p is 0..1000. Keep the codec here in
// step with the backend's.
import { getStroke } from "perfect-freehand";

export const FORMAT = "gamma-ink";
export const VERSION = 1;
export const COORD_UNIT = 100;
export const PRESSURE_UNIT = 1000;
export const THINNING = 0.5;      // width = size * (1 + THINNING * (p - 0.5)); mirrors ink.py
export const MAX_STROKES = 5000;

export function newInk(page, width, height) {
  return { format: FORMAT, version: VERSION,
    space: { kind: "pdf-page", page, width, height }, strokes: [] };
}

// A notebook sheet's ink (notebook/notebook.js): the sheet is the canvas,
// points from its top-left corner, y down, like a PDF page's frame.
export function newCanvasInk(width, height) {
  return { format: FORMAT, version: VERSION, space: { kind: "canvas", width, height }, strokes: [] };
}

// The upload bytes of an ink file: keys sorted at every level and no
// whitespace, so the same strokes are the same file on every client (the
// server stores an upload as it came and names it by its hash).
export function serializeInk(ink) {
  const canon = (v) => {
    if (Array.isArray(v)) return v.map(canon);
    if (v && typeof v === "object") {
      const out = {};
      for (const k of Object.keys(v).sort()) if (v[k] !== undefined && v[k] !== null) out[k] = canon(v[k]);
      return out;
    }
    return v;
  };
  return JSON.stringify(canon(ink));
}

// What a group's block carries for its file at `url`: the reference, the
// stroke count and, on a PDF page, the page and the box in the highlight
// shape. Every client writes these the same way; null clears a key.
export function inkProps(ink, url) {
  const pdf = ink.space?.kind === "pdf-page";
  return { ink_url: url, ink_strokes: ink.strokes.length,
    pdf_page: pdf ? ink.space.page : null, pdf_position: pdf ? pdfPositionOf(ink) : null };
}

const ID_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
function strokeId() {
  let s = "";
  for (let i = 0; i < 8; i++) s += ID_CHARS[Math.floor(Math.random() * ID_CHARS.length)];
  return s;
}

// --- tool presets ------------------------------------------------------------
// The strip is a row of presets, Notability-style: each pen or highlighter
// keeps its own colour and width ({id, kind: pen|highlighter, color, size}),
// and the user edits, duplicates and removes them. Widths are pt at scale 1
// (the options row shows them as dots); colours are hex — a highlighter
// gets HIGHLIGHTER_OPACITY on top and multiplies onto the page.
export const PEN_SIZES = [0.6, 1, 1.4, 2, 2.8, 4, 5.6, 8];
export const HIGHLIGHTER_SIZES = [5, 7, 10, 14, 18, 24, 32, 40];
export const PEN_COLORS = ["#1f1f1f", "#6b7280", "#1d4ed8", "#0284c7", "#0f766e", "#15803d", "#65a30d",
  "#ca8a04", "#ea580c", "#dc2626", "#db2777", "#7c3aed", "#92400e", "#ffffff"];
export const HIGHLIGHTER_COLORS = ["#fde047", "#86efac", "#7dd3fc", "#f9a8d4", "#fdba74", "#c4b5fd", "#67e8f9", "#d4d4d8"];
export const HIGHLIGHTER_OPACITY = 0.6;
// The palette colour nearest to `hex` (#rgb or #rrggbb) by the "redmean"
// weighted RGB distance — a palette colour maps to itself; null for what is
// not a hex colour. The tool strip names a custom colour after it.
export function nearestInkColor(hex) {
  const rgb = (h) => {
    const m = /^#?([0-9a-f]{3}|[0-9a-f]{6})$/i.exec(String(h || "").trim());
    if (!m) return null;
    const v = m[1].length === 3 ? m[1].replace(/./g, "$&$&") : m[1];
    return [0, 2, 4].map((i) => parseInt(v.slice(i, i + 2), 16));
  };
  const c = rgb(hex);
  if (!c) return null;
  let best = null, bestD = Infinity;
  for (const p of [...PEN_COLORS, ...HIGHLIGHTER_COLORS]) {
    const q = rgb(p), rm = (c[0] + q[0]) / 2;
    const d = (2 + rm / 256) * (c[0] - q[0]) ** 2 + 4 * (c[1] - q[1]) ** 2 + (2 + (255 - rm) / 256) * (c[2] - q[2]) ** 2;
    if (d < bestD) { best = p; bestD = d; }
  }
  return best;
}
// Cap on a stroke's nominal width (pt); scaling a selection stops here.
export const MAX_STROKE_SIZE = 100;
export const DEFAULT_TOOLS = [
  { id: "pen1", kind: "pen", color: "#1f1f1f", size: 2 },
  { id: "pen2", kind: "pen", color: "#1d4ed8", size: 2 },
  { id: "pen3", kind: "pen", color: "#dc2626", size: 2 },
  { id: "pen4", kind: "pen", color: "#15803d", size: 1.4 },
  { id: "hl1", kind: "highlighter", color: "#fde047", size: 14 },
  { id: "hl2", kind: "highlighter", color: "#86efac", size: 14 },
  { id: "hl3", kind: "highlighter", color: "#f9a8d4", size: 14 },
];
export const MAX_TOOLS = 12;
const HEX_RE = /^#[0-9a-f]{6}$/i;

export function toolId() {
  return "t" + strokeId();
}
export function sizesFor(kind) {
  return kind === "highlighter" ? HIGHLIGHTER_SIZES : PEN_SIZES;
}
// A stored list (localStorage, possibly hand-edited or from an older
// build) → a valid preset list, or the defaults when nothing survives.
export function normalizeTools(list) {
  const out = [];
  const seen = new Set();
  for (const t of Array.isArray(list) ? list : []) {
    if (!t || typeof t !== "object") continue;
    const kind = t.kind === "highlighter" ? "highlighter" : t.kind === "pen" ? "pen" : null;
    if (!kind || typeof t.color !== "string" || !HEX_RE.test(t.color)) continue;
    const sizes = sizesFor(kind);
    const size = Number(t.size);
    if (!Number.isFinite(size) || size < sizes[0] || size > sizes[sizes.length - 1]) continue;
    let id = typeof t.id === "string" && /^[A-Za-z0-9_-]{1,24}$/.test(t.id) ? t.id : toolId();
    while (seen.has(id)) id = toolId();
    seen.add(id);
    out.push({ id, kind, color: t.color.toLowerCase(), size: Math.round(size * 100) / 100,
      ...(kind === "pen" && t.brush === "monoline" ? { brush: "monoline" } : {}) });
    if (out.length >= MAX_TOOLS) break;
  }
  return out.length ? out : DEFAULT_TOOLS.map((t) => ({ ...t }));
}
// What the layer draws with for a preset: {tool, color, size, opacity}.
export function toolStyle(preset) {
  return preset.kind === "highlighter"
    ? { tool: "highlighter", color: preset.color, size: preset.size, opacity: HIGHLIGHTER_OPACITY }
    : { tool: "pen", color: preset.color, size: preset.size, opacity: 1,
      ...(preset.brush === "monoline" ? { brush: "monoline" } : {}) };
}


// --- codec -----------------------------------------------------------------

// samples: [{x, y, p?, t?}] with x/y in points and t in ms since t0.
export function encodeStroke({ id, tool = "pen", brush, color = PEN_COLORS[0], size = 2, opacity = 1,
  pen = true, t0 = null, samples, ch = "xyp" }) {
  const pts = [];
  let px = 0, py = 0, pt = 0;
  for (const s of samples) {
    for (const c of ch) {
      if (c === "x") { const v = Math.round(s.x * COORD_UNIT); pts.push(v - px); px = v; }
      else if (c === "y") { const v = Math.round(s.y * COORD_UNIT); pts.push(v - py); py = v; }
      else if (c === "p") pts.push(Math.round(Math.max(0, Math.min(1, s.p ?? 0.5)) * PRESSURE_UNIT));
      else if (c === "t") { const v = Math.round(s.t || 0); pts.push(v - pt); pt = v; }
      else if (c === "a") pts.push(Math.round(s.a || 0));
      else if (c === "z") pts.push(Math.round(s.z || 0));
    }
  }
  const out = { id: id || strokeId(), tool, color, size, opacity, pen, ch, pts };
  if (tool === "pen" && brush === "monoline") out.brush = "monoline";
  if (t0 != null) out.t0 = t0;
  return out;
}

const decoded = new WeakMap();
// → [{x, y, p, t}] in points; p defaults to 0.5, t to null. Cached per stroke object.
export function decodeStroke(stroke) {
  const hit = decoded.get(stroke);
  if (hit) return hit;
  const { ch = "xy", pts = [] } = stroke;
  const n = ch.length, out = [];
  let x = 0, y = 0, t = 0;
  for (let i = 0; i + n <= pts.length; i += n) {
    const s = { x: 0, y: 0, p: 0.5, t: null };
    for (let k = 0; k < n; k++) {
      const c = ch[k], v = pts[i + k];
      if (c === "x") { x += v; s.x = x / COORD_UNIT; }
      else if (c === "y") { y += v; s.y = y / COORD_UNIT; }
      else if (c === "p") s.p = Math.max(0, Math.min(1, v / PRESSURE_UNIT));
      else if (c === "t") { t += v; s.t = t; }
      else if (c === "a") s.a = v;
      else if (c === "z") s.z = v;
    }
    out.push(s);
  }
  decoded.set(stroke, out);
  return out;
}

export function strokeWidth(stroke, p) {
  if (stroke.tool !== "pen" || stroke.pen === false || stroke.brush === "monoline") return stroke.size;
  return stroke.size * (1 + THINNING * (p - 0.5));
}

// --- geometry --------------------------------------------------------------

const bounds = new WeakMap();
// [x0, y0, x1, y1] around the samples, width included.
export function strokeBounds(stroke) {
  const hit = bounds.get(stroke);
  if (hit) return hit;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const s of decodeStroke(stroke)) {
    const r = strokeWidth(stroke, s.p) / 2;
    x0 = Math.min(x0, s.x - r); y0 = Math.min(y0, s.y - r);
    x1 = Math.max(x1, s.x + r); y1 = Math.max(y1, s.y + r);
  }
  const out = x0 === Infinity ? null : [x0, y0, x1, y1];
  bounds.set(stroke, out);
  return out;
}

// The box around two boxes (either may be null).
export function unionBox(a, b) {
  if (!a) return b ? [...b] : null;
  if (!b) return [...a];
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

export function inkBounds(ink) {
  let out = null;
  for (const s of ink?.strokes || []) out = unionBox(out, strokeBounds(s));
  return out;
}

// The group's box in the highlight `pdf_position` shape (what jump-to-
// position, the sidebar marker and the exporters read).
export function pdfPositionOf(ink) {
  const b = inkBounds(ink);
  if (!b || ink.space?.kind !== "pdf-page") return null;
  const r2 = (v) => Math.round(v * 100) / 100;
  const rect = { x1: r2(b[0]), y1: r2(b[1]), x2: r2(b[2]), y2: r2(b[3]),
    width: ink.space.width, height: ink.space.height, pageNumber: ink.space.page };
  return { pageNumber: ink.space.page, boundingRect: rect, rects: [{ ...rect }] };
}

function segDist2(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  let t = len2 ? ((px - ax) * dx + (py - ay) * dy) / len2 : 0;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx - px, qy = ay + t * dy - py;
  return qx * qx + qy * qy;
}

// Distance from (x, y) to the stroke's painted edge (0 inside the ink), or
// Infinity when the point lies more than `radius` outside its bounding box.
function strokeEdgeDistance(s, x, y, radius) {
  const b = strokeBounds(s);
  if (!b || x < b[0] - radius || x > b[2] + radius || y < b[1] - radius || y > b[3] + radius) return Infinity;
  const pts = decodeStroke(s);
  let best = Infinity;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i], c = pts[Math.min(i + 1, pts.length - 1)];
    const edge = Math.sqrt(segDist2(x, y, a.x, a.y, c.x, c.y)) - Math.max(strokeWidth(s, a.p), strokeWidth(s, c.p)) / 2;
    if (edge < best) best = edge;
  }
  return Math.max(0, best);
}

// Ids of the strokes an eraser at (x, y) with `radius` touches.
export function hitStrokes(ink, x, y, radius) {
  return (ink?.strokes || []).filter((s) => strokeEdgeDistance(s, x, y, radius) <= radius).map((s) => s.id);
}

// Nearest stroke edge, with the last-painted stroke winning ties. The
// caller converts a screen-space touch tolerance into page units.
export function nearestInkStroke(groups, x, y, radius) {
  let best = null, distance = radius;
  for (const g of groups) for (const s of g.ink.strokes) {
    const edge = strokeEdgeDistance(s, x, y, radius);
    if (edge <= distance) { distance = edge; best = { id: g.id, ids: [s.id] }; }
  }
  return best;
}

export function restyleStrokes(ink, ids, patch) {
  const selected = new Set(ids);
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id) || (patch.tool && patch.tool !== s.tool)) return s;
    const color = HEX_RE.test(patch.color || "") ? patch.color.toLowerCase() : s.color;
    const sizes = sizesFor(s.tool);
    const size = Number.isFinite(patch.size)
      ? Math.round(Math.max(sizes[0], Math.min(sizes.at(-1), patch.size)) * 100) / 100 : s.size;
    if (color === s.color && size === s.size) return s;
    changed = true;
    return { ...s, color, size };
  });
  return changed ? { ...ink, strokes } : ink;
}

export function duplicateStrokes(ink, ids, dx, dy) {
  const selected = new Set(ids), used = new Set(ink.strokes.map((s) => s.id));
  const originals = ink.strokes.filter((s) => selected.has(s.id));
  if (!originals.length || ink.strokes.length + originals.length > MAX_STROKES) return { ink, ids: [] };
  const copies = originals.map((s) => {
    let id;
    do { id = strokeId(); } while (used.has(id));
    used.add(id);
    return { ...s, id, pts: [...s.pts] };
  });
  const copyIds = copies.map((s) => s.id);
  return { ink: translateStrokes({ ...ink, strokes: [...ink.strokes, ...copies] }, copyIds, dx, dy), ids: copyIds };
}

// Two strokes are the same when every field and sample is.
export function sameStroke(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const k of keys) {
    const x = a[k] ?? null, y = b[k] ?? null;
    if (k === "pts") {
      if (!Array.isArray(x) || !Array.isArray(y) || x.length !== y.length) return false;
      for (let i = 0; i < x.length; i++) if (x[i] !== y[i]) return false;
    } else if (x !== y) return false;
  }
  return true;
}

const sameStrokes = (a, b) => a.length === b.length && a.every((s, i) => sameStroke(s, b[i]));

// Apply the change base → ours to theirs (the group as stored now), stroke
// by stroke: stroke ids survive every edit, so they play the part text
// offsets play in the text merge. → {ink, clean}. Where theirs left a
// stroke as the base had it, ours' change applies in place; a stroke ours
// added goes after the stroke before it that the result keeps, behind
// strokes theirs added there; a stroke both changed keeps theirs' version,
// and a stroke one side changed survives the other's erasure (either makes
// the merge unclean). base null merges as a union by id. The same rule as
// gamma/ink.py merge_ink; tests/shared/inkmerge.json pins both. Undo and
// redo rebase through it too (the action's after → before onto now).
export function mergeInk(base, ours, theirs) {
  const bList = base?.strokes || [];
  if (base && sameStrokes(theirs.strokes, bList)) return { ink: ours, clean: true };
  if (sameStrokes(ours.strokes, bList) || sameStrokes(ours.strokes, theirs.strokes)) return { ink: theirs, clean: true };
  const b = new Map(bList.map((s) => [s.id, s]));
  const o = new Map(ours.strokes.map((s) => [s.id, s]));
  let clean = true;
  const out = [];
  for (const t of theirs.strokes) {
    const was = b.get(t.id), mine = o.get(t.id);
    if (!was) {
      if (mine && !sameStroke(mine, t)) clean = false;
      out.push(t);
    } else if (!mine) {
      if (!sameStroke(t, was)) { clean = false; out.push(t); }
    } else if (sameStroke(mine, was) || sameStroke(t, mine)) out.push(t);
    else if (sameStroke(t, was)) out.push(mine);
    else { clean = false; out.push(t); }
  }
  const present = new Set(out.map((s) => s.id));
  let anchor = null;
  for (const s of ours.strokes) {
    if (present.has(s.id)) { anchor = s.id; continue; }
    const was = b.get(s.id);
    if (was) {
      if (sameStroke(s, was)) continue;
      clean = false;
    }
    let at = anchor ? out.findIndex((x) => x.id === anchor) + 1 : 0;
    while (at < out.length && !b.has(out[at].id) && !o.has(out[at].id)) at++;
    out.splice(at, 0, s);
    present.add(s.id);
    anchor = s.id;
  }
  // a merge that changes nothing is that file itself (a change sent again)
  if (sameStrokes(out, theirs.strokes)) return { ink: theirs, clean };
  if (sameStrokes(out, ours.strokes) && JSON.stringify(ours.space) === JSON.stringify(theirs.space)) return { ink: ours, clean };
  return { ink: { format: FORMAT, version: VERSION, space: theirs.space, strokes: out }, clean };
}

export function appendStroke(ink, stroke) {
  return { ...ink, strokes: [...(ink.strokes || []), stroke].slice(-MAX_STROKES) };
}

export function removeStrokes(ink, ids) {
  const drop = new Set(ids);
  return { ...ink, strokes: (ink.strokes || []).filter((s) => !drop.has(s.id)) };
}

// Move strokes by (dx, dy) points. Only the first sample is absolute in
// the delta encoding, so a translation touches two integers per stroke.
export function translateStrokes(ink, ids, dx, dy) {
  const move = new Set(ids);
  const ex = Math.round(dx * COORD_UNIT), ey = Math.round(dy * COORD_UNIT);
  if (!ex && !ey) return ink;
  return { ...ink, strokes: (ink.strokes || []).map((s) => {
    if (!move.has(s.id) || s.pts.length < 2) return s;
    const pts = s.pts.slice();
    pts[0] += ex;
    pts[1] += ey;
    return { ...s, pts };
  }) };
}

// (x, y) scaled by `scale` and rotated by `angle` (radians) about (cx, cy).
export function transformPoint(x, y, { cx, cy, scale, angle }) {
  const cos = Math.cos(angle), sin = Math.sin(angle), dx = x - cx, dy = y - cy;
  return [cx + scale * (dx * cos - dy * sin), cy + scale * (dx * sin + dy * cos)];
}

// Uniform scaling/rotation around a shared page-space origin. Rewrite only
// XY channels, preserving pressure, timing, tilt, IDs and other metadata.
export function transformStrokes(ink, ids, { cx, cy, scale = 1, angle = 0 }) {
  if (![cx, cy, scale, angle].every(Number.isFinite) || scale <= 0 || (scale === 1 && angle === 0)) return ink;
  const selected = new Set(ids);
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id)) return s;
    changed = true;
    const pts = s.pts.slice(), n = s.ch.length;
    let px = 0, py = 0;
    decodeStroke(s).forEach((p, i) => {
      const [tx, ty] = transformPoint(p.x, p.y, { cx, cy, scale, angle });
      const nx = Math.round(tx * COORD_UNIT);
      const ny = Math.round(ty * COORD_UNIT);
      pts[i * n] = nx - px; pts[i * n + 1] = ny - py;
      px = nx; py = ny;
    });
    return { ...s, pts, size: Math.max(0.01, Math.min(MAX_STROKE_SIZE, s.size * scale)) };
  });
  return changed ? { ...ink, strokes } : ink;
}

// A stroke's samples → a stroke of the same look (fresh id).
function restroke(stroke, samples) {
  return encodeStroke({ tool: stroke.tool, brush: stroke.brush, color: stroke.color, size: stroke.size, opacity: stroke.opacity,
    pen: stroke.pen !== false, t0: stroke.t0 ?? null, samples, ch: stroke.ch });
}

// Partial eraser: cut every sample within `radius` (plus half the width)
// of (x, y) out of the strokes it touches; each remaining run of at least
// two samples becomes its own stroke. → {ink, changed}.
export function eraseAt(ink, x, y, radius) {
  let changed = false;
  const strokes = [];
  for (const s of ink?.strokes || []) {
    const b = strokeBounds(s);
    if (!b || x < b[0] - radius || x > b[2] + radius || y < b[1] - radius || y > b[3] + radius) { strokes.push(s); continue; }
    const pts = decodeStroke(s);
    const keep = pts.map((q) => {
      const tol = radius + strokeWidth(s, q.p) / 2;
      return (q.x - x) ** 2 + (q.y - y) ** 2 > tol * tol;
    });
    if (keep.every(Boolean)) { strokes.push(s); continue; }
    changed = true;
    let run = [];
    const flush = () => { if (run.length >= 2) strokes.push(restroke(s, run)); run = []; };
    pts.forEach((q, i) => { if (keep[i]) run.push(q); else flush(); });
    flush();
  }
  return { ink: changed ? { ...ink, strokes } : ink, changed };
}

function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

// Lasso: ids of the strokes with more than half their samples inside the
// polygon ([[x, y], …] in page units).
export function strokesInLasso(ink, polygon) {
  if (!polygon || polygon.length < 3) return [];
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const [px, py] of polygon) { x0 = Math.min(x0, px); y0 = Math.min(y0, py); x1 = Math.max(x1, px); y1 = Math.max(y1, py); }
  const out = [];
  for (const s of ink?.strokes || []) {
    const b = strokeBounds(s);
    if (!b || b[2] < x0 || b[0] > x1 || b[3] < y0 || b[1] > y1) continue;
    const pts = decodeStroke(s);
    let inside = 0;
    for (const q of pts) if (pointInPolygon(q.x, q.y, polygon)) inside++;
    if (inside * 2 > pts.length) out.push(s.id);
  }
  return out;
}

// [x0, y0, x1, y1] around the named strokes, or null.
export function boundsOf(ink, ids) {
  const want = new Set(ids);
  let out = null;
  for (const s of ink?.strokes || []) if (want.has(s.id)) out = unionBox(out, strokeBounds(s));
  return out;
}

// --- replay ----------------------------------------------------------------
// A drawing as it was written. A stroke starts at its wall-clock `t0` plus
// its first sample's `t` (a run the partial eraser cut out of a longer
// stroke keeps the times it had), and its samples follow their `t`. Pauses,
// between strokes and inside one, shrink to `pause`, and a replay longer
// than `max` plays faster, so a page written over an hour replays in
// seconds. Strokes play in the order they were drawn: by that start, the
// file's order breaking ties; a stroke without timing counts as drawn right
// after the stroke before it in the file, its samples spread over UNTIMED_MS.
export const REPLAY_PAUSE = 400;
export const REPLAY_MAX = 15000;
const UNTIMED_MS = 300;

// → {duration, strokes: [{id, index, start, end, times}]} in ms from the
// replay's start, in drawing order: `index` is the stroke's place in the
// file, `times` each sample's time.
export function inkTimeline(ink, { pause = REPLAY_PAUSE, max = REPLAY_MAX } = {}) {
  let before = -Infinity;
  const order = (ink?.strokes || []).map((stroke, index) => {
    const samples = decodeStroke(stroke);
    const timed = samples.length > 0 && samples.every((p) => p.t != null);
    const wallStart = Number.isFinite(stroke.t0) ? stroke.t0 + (timed ? samples[0].t : 0) : null;
    before = wallStart ?? before;
    return { stroke, index, samples, timed, wallStart, key: before };
  }).sort((a, b) => a.key - b.key || a.index - b.index);
  const clamp = (v) => Math.max(0, Math.min(pause, v));
  const strokes = [];
  let clock = 0, lastEnd = null;   // lastEnd: the wall-clock end of the stroke before
  for (const { stroke, index, samples, timed, wallStart } of order) {
    if (!samples.length) continue;
    const offsets = [0];
    for (let i = 1; i < samples.length; i++) {
      offsets.push(offsets[i - 1] + (timed ? clamp(samples[i].t - samples[i - 1].t) : UNTIMED_MS / (samples.length - 1)));
    }
    const gap = !strokes.length ? 0 : wallStart != null && lastEnd != null ? clamp(wallStart - lastEnd) : pause / 2;
    const start = clock + gap;
    const times = offsets.map((o) => start + o);
    strokes.push({ id: stroke.id, index, start, end: times[times.length - 1], times });
    clock = times[times.length - 1];
    lastEnd = wallStart == null ? null : wallStart + (timed ? samples[samples.length - 1].t - samples[0].t : 0);
  }
  if (clock > max) {
    const f = max / clock;
    for (const item of strokes) {
      item.start *= f;
      item.end *= f;
      item.times = item.times.map((v) => v * f);
    }
    clock = max;
  }
  return { duration: clock, strokes };
}

// The drawing at replay time `t` (ms): the strokes begun by then, in the
// order they were drawn, the one being written cut to the samples it had
// (a prefix of its delta-coded `pts` is its first samples). The file itself
// once the replay has ended.
export function inkAtTime(ink, timeline, t) {
  if (!ink || t >= timeline.duration) return ink;
  const strokes = [];
  for (const item of timeline.strokes) {
    if (item.start > t) break;
    const stroke = ink.strokes[item.index];
    if (!stroke) continue;
    if (t >= item.end) { strokes.push(stroke); continue; }
    let k = 1;
    while (k < item.times.length && item.times[k] <= t) k++;
    strokes.push({ ...stroke, pts: stroke.pts.slice(0, k * (stroke.ch || "xy").length) });
  }
  return { ...ink, strokes };
}

// --- rendering -------------------------------------------------------------

const avg = (a, b) => (a + b) / 2;
// perfect-freehand's README recipe: the outline polygon as a closed path of
// quadratic curves through midpoints (smooth, no visible corners).
export function svgPathFromPoints(points) {
  const n = points.length;
  if (!n) return "";
  if (n < 3) {
    const [a] = points;
    return `M${a[0].toFixed(2)},${a[1].toFixed(2)} L${(a[0] + 0.01).toFixed(2)},${a[1].toFixed(2)}`;
  }
  let d = `M${points[0][0].toFixed(2)},${points[0][1].toFixed(2)} Q`;
  for (let i = 0; i < n; i++) {
    const a = points[i], b = points[(i + 1) % n];
    d += `${a[0].toFixed(2)},${a[1].toFixed(2)} ${avg(a[0], b[0]).toFixed(2)},${avg(a[1], b[1]).toFixed(2)} `;
  }
  return d + "Z";
}

// Outline options in page units — thickness is size, so zoom scales ink
// like ink on paper.
export function outlineOptions(stroke) {
  return { size: stroke.size, thinning: stroke.pen === false || stroke.brush === "monoline" ? 0 : THINNING, smoothing: 0.5,
    streamline: 0.4, simulatePressure: false, last: true };
}

// samples → the filled outline path for a pen stroke.
export function penOutline(samples, opts) {
  return svgPathFromPoints(getStroke(samples.map((s) => [s.x, s.y, s.p ?? 0.5]), opts));
}

const paths = new WeakMap();
// The SVG rendering of a stroke: {d, fill} (pen: a filled outline) or
// {d, stroke, width} (highlighter: a stroked polyline). Cached per object.
export function strokePath(stroke) {
  const hit = paths.get(stroke);
  if (hit) return hit;
  const pts = decodeStroke(stroke);
  let out;
  if (stroke.tool === "highlighter") {
    const d = pts.map((s, i) => `${i ? "L" : "M"}${s.x.toFixed(2)},${s.y.toFixed(2)}`).join(" ")
      + (pts.length === 1 ? ` L${(pts[0].x + 0.01).toFixed(2)},${pts[0].y.toFixed(2)}` : "");
    out = { d, stroke: true, width: stroke.size };
  } else {
    out = { d: penOutline(pts, outlineOptions(stroke)), stroke: false };
  }
  paths.set(stroke, out);
  return out;
}
