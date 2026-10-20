// Handwriting: the client half of gamma/ink.py (docs/dev/handwriting.md).
// Pure — no React, no network — so it runs under node --test.
//
// An ink group is one `gamma-ink` version 2 file: strokes on one PDF page,
// in the page's scale-1 frame (pdf.js viewport at scale 1: points, origin
// top-left, rotation applied — the same frame highlight rects normalise
// to), or on a notebook sheet. Samples are stored per stroke as one flat
// integer array, `ch` naming the channels ("xy" + any of p/t/a/z/w/o, and
// letters this build does not know, carried by stride); x, y (1/100 pt),
// t (ms) and w/o (× 1000) are deltas after the first sample, p is 0..1000
// absolute. The drawn width is `size × w` (strokeWidth): the only width
// rule, baked once by ink/finishStroke.js. Keep the codec here in step
// with the backend's; tests/shared/inkv2.json, inkwidth.json and
// inkmerge.json pin both.
import { getStroke } from "perfect-freehand";
import { INK_COLORS, INK_HIGHLIGHT_ALPHA, INK_HIGHLIGHT_COLORS } from "../shared/model/palette.js";

export const FORMAT = "gamma-ink";
export const VERSION = 2;
export const COORD_UNIT = 100;
export const PRESSURE_UNIT = 1000;
export const WIDTH_UNIT = 1000;   // w and o: a multiplier × 1000
export const MAX_STROKES = 5000;
// The server's cap on one file (gamma/ink.py MAX_BYTES); applyInk refuses
// an edit past it, and a stroke that would push a group past
// GROUP_SPLIT_BYTES starts a new group instead.
export const MAX_BYTES = 6 * 1024 * 1024;
export const GROUP_SPLIT_BYTES = 3 * 1024 * 1024;

export function newInk(page, width, height) {
  return { format: FORMAT, version: VERSION,
    space: { kind: "pdf-page", page, width, height }, strokes: [] };
}

// A notebook sheet's ink (notebook/notebook.js): the sheet is the canvas,
// points from its top-left corner, y down, like a PDF page's frame.
export function newCanvasInk(width, height) {
  return { format: FORMAT, version: VERSION, space: { kind: "canvas", width, height }, strokes: [] };
}

// The upload bytes of an ink file: keys sorted at every level, no
// whitespace, null and undefined keys dropped (whole-number floats are
// integers in JavaScript already), so the same strokes are the same file
// on every client (the server stores an upload as it came and names it by
// its hash; gamma/ink.py dumps writes the same bytes).
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

// The bytes a group would upload — exactly `serializeInk(ink).length`,
// summed per stroke (each stroke's canonical bytes plus its comma) over
// the empty file's, and cached per object: strokes are shared between a
// group's versions, so an edit costs its changed strokes only. The byte
// budget's gauge.
const strokeSizes = new WeakMap(), inkSizes = new WeakMap();
export function strokeBytes(stroke) {
  let n = strokeSizes.get(stroke);
  if (n === undefined) { n = serializeInk(stroke).length + 1; strokeSizes.set(stroke, n); }
  return n;
}
export function inkBytes(ink) {
  if (!ink) return 0;
  let n = inkSizes.get(ink);
  if (n === undefined) {
    n = serializeInk({ ...ink, strokes: [] }).length;
    for (const s of ink.strokes || []) n += strokeBytes(s);
    if (ink.strokes?.length) n -= 1;  // one comma fewer than strokes
    inkSizes.set(ink, n);
  }
  return n;
}

// What a group's block carries for its file at `url`: the reference, the
// stroke count and, on a PDF page, its place there (pdfPositionOf). Every
// client writes these the same way; null clears a key.
export function inkProps(ink, url) {
  return { ink_url: url, ink_strokes: ink.strokes.length, pdf_position: pdfPositionOf(ink) };
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
// (the options row shows them as dots); colours are hex, picked from the
// account's palette (shared/model/palette.js: the ink set for a pen, the
// wash set for a highlighter) or a custom one — a highlighter gets
// HIGHLIGHTER_OPACITY on top and multiplies onto the page.
export const PEN_SIZES = [0.6, 1, 1.4, 2, 2.8, 4, 5.6, 8];
export const HIGHLIGHTER_SIZES = [5, 7, 10, 14, 18, 24, 32, 40];
export const HIGHLIGHTER_OPACITY = INK_HIGHLIGHT_ALPHA;
// Cap on a stroke's nominal width (pt); scaling a selection stops here.
export const MAX_STROKE_SIZE = 100;
export const DEFAULT_TOOLS = [
  { id: "pen1", kind: "pen", color: INK_COLORS[0], size: 2 },
  { id: "pen2", kind: "pen", color: INK_COLORS[2], size: 2 },
  { id: "pen3", kind: "pen", color: INK_COLORS[9], size: 2 },
  { id: "pen4", kind: "pen", color: INK_COLORS[5], size: 1.4 },
  { id: "hl1", kind: "highlighter", color: INK_HIGHLIGHT_COLORS[0], size: 14 },
  { id: "hl2", kind: "highlighter", color: INK_HIGHLIGHT_COLORS[1], size: 14 },
  { id: "hl3", kind: "highlighter", color: INK_HIGHLIGHT_COLORS[4], size: 14 },
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
    // A preset's own opacity (the swatch's alpha when it was picked); left
    // out, the kind's usual one applies (toolStyle).
    const opacity = Number(t.opacity);
    out.push({ id, kind, color: t.color.toLowerCase(), size: Math.round(size * 100) / 100,
      ...(Number.isFinite(opacity) && opacity >= 0.1 && opacity <= 1 && opacity !== defaultOpacity(kind)
        ? { opacity: Math.round(opacity * 100) / 100 } : {}),
      ...(kind === "pen" && t.brush === "monoline" ? { brush: "monoline" } : {}) });
    if (out.length >= MAX_TOOLS) break;
  }
  return out.length ? out : DEFAULT_TOOLS.map((t) => ({ ...t }));
}
// A kind's opacity when a preset names none.
export const defaultOpacity = (kind) => (kind === "highlighter" ? HIGHLIGHTER_OPACITY : 1);
// What the layer draws with for a preset: {tool, color, size, opacity}.
export function toolStyle(preset) {
  const opacity = preset.opacity ?? defaultOpacity(preset.kind);
  return preset.kind === "highlighter"
    ? { tool: "highlighter", color: preset.color, size: preset.size, opacity }
    : { tool: "pen", color: preset.color, size: preset.size, opacity,
      ...(preset.brush === "monoline" ? { brush: "monoline" } : {}) };
}


// --- codec -----------------------------------------------------------------

// The letters both codecs know, after x and y; DELTA names the channels
// written as a difference from the sample before. A letter not here is an
// unknown channel: decodeStroke carries its integer in the sample's `rest`
// array and encodePoints writes it back in place, so a cut on a copy that
// predates the letter keeps the stride.
export const KNOWN_CHANNELS = "xyptazwo";
const DELTA = new Set(["x", "y", "t", "w", "o"]);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const clamp01 = (v) => clamp(v, 0, 1);
const unknownLetters = (ch) => [...ch].filter((c) => !KNOWN_CHANNELS.includes(c));

// samples: [{x, y, p?, t?, a?, z?, w?, o?, rest?}] with x/y in points, p
// 0..1, t ms since t0, a/z degrees, w/o multipliers → the flat `pts`.
export function encodePoints(samples, ch = "xy") {
  const pts = [], prev = {};
  for (const s of samples) {
    let r = 0;
    for (const c of ch) {
      let v;
      if (c === "x") v = Math.round(s.x * COORD_UNIT);
      else if (c === "y") v = Math.round(s.y * COORD_UNIT);
      else if (c === "p") v = Math.round(clamp01(s.p ?? 0.5) * PRESSURE_UNIT);
      else if (c === "t") v = Math.round(s.t || 0);
      else if (c === "a") v = Math.round(s.a || 0);
      else if (c === "z") v = Math.round(s.z || 0);
      else if (c === "w") v = Math.round(clamp(s.w ?? 1, 0, 2) * WIDTH_UNIT);
      else if (c === "o") v = Math.round(clamp01(s.o ?? 1) * WIDTH_UNIT);
      else v = Math.round(s.rest?.[r++] ?? 0);
      if (DELTA.has(c)) { pts.push(v - (prev[c] || 0)); prev[c] = v; } else pts.push(v);
    }
  }
  return pts;
}

// A stroke from its samples and its keys: the look (tool, brush, color,
// size, opacity, pen), t0, and any other key (band, from, shape, fill,
// dash, nib, extras) as given; null and undefined keys are left out.
export function encodeStroke({ id, samples, ch = "xyp", tool = "pen", brush, color = INK_COLORS[0], size = 2, opacity = 1,
  pen = true, t0 = null, ...rest }) {
  const out = { id: id || strokeId(), tool, color, size, opacity, pen, ch, pts: encodePoints(samples, ch) };
  for (const k of Object.keys(rest)) if (rest[k] != null) out[k] = rest[k];
  if (brush) out.brush = brush;
  if (t0 != null) out.t0 = t0;
  return out;
}

const decoded = new WeakMap();
// → [{x, y, p?, t?, a?, z?, w?, o?, rest?}] in points, each key present
// when its channel is: p 0..1, t ms since t0, a/z degrees, w and o as
// multipliers (so a decoded cache never depends on size), `rest` the
// integers of letters this build does not know. Cached per stroke object.
export function decodeStroke(stroke) {
  const hit = decoded.get(stroke);
  if (hit) return hit;
  const { ch = "xy", pts = [] } = stroke;
  const n = ch.length, out = [], run = {};
  const foreign = unknownLetters(ch).length > 0;
  for (let i = 0; i + n <= pts.length; i += n) {
    const s = { x: 0, y: 0 };
    if (foreign) s.rest = [];
    for (let k = 0; k < n; k++) {
      const c = ch[k];
      let v = pts[i + k];
      if (DELTA.has(c)) v = run[c] = (run[c] || 0) + v;
      if (c === "x") s.x = v / COORD_UNIT;
      else if (c === "y") s.y = v / COORD_UNIT;
      else if (c === "p") s.p = clamp01(v / PRESSURE_UNIT);
      else if (c === "t") s.t = v;
      else if (c === "a") s.a = v;
      else if (c === "z") s.z = v;
      else if (c === "w") s.w = clamp(v / WIDTH_UNIT, 0, 2);
      else if (c === "o") s.o = clamp01(v / WIDTH_UNIT);
      else s.rest.push(v);
    }
    out.push(s);
  }
  decoded.set(stroke, out);
  return out;
}

// A stroke's keys with a default, as the file may omit them (gamma/ink.py
// Stroke); withDefaults fills them on a loaded file so every reader and
// the menus see complete strokes (the same object when nothing is missing).
export const DEFAULT_SIZE = 1.6;
export const STROKE_DEFAULTS = { tool: "pen", color: INK_COLORS[0], size: DEFAULT_SIZE, opacity: 1, pen: true, ch: "xy" };
export function withDefaults(ink) {
  if (!ink?.strokes) return ink;
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (Object.keys(STROKE_DEFAULTS).every((k) => s[k] != null)) return s;
    changed = true;
    const out = { ...s };
    for (const k of Object.keys(STROKE_DEFAULTS)) if (out[k] == null) out[k] = STROKE_DEFAULTS[k];
    return out;
  });
  return changed ? { ...ink, strokes } : ink;
}

// The drawn width at a sample: size × w when the stroke carries w, else
// size. The whole rule (gamma/ink.py stroke_width is the same line).
export const strokeWidth = (stroke, sample) => (sample?.w != null ? (stroke.size ?? DEFAULT_SIZE) * sample.w : stroke.size ?? DEFAULT_SIZE);
// Which layer a stroke draws on: 0 under (a highlighter's default), 1 as ink.
export const strokeBand = (stroke) => stroke.band ?? (stroke.tool === "highlighter" ? 0 : 1);
// Strokes in drawing order: every band-0 stroke, then the band-1 ones,
// each band in file order.
export function byBand(strokes) {
  const under = [], over = [];
  for (const s of strokes) (strokeBand(s) === 0 ? under : over).push(s);
  return under.length ? [...under, ...over] : strokes;
}

// --- version 1 -------------------------------------------------------------

// The one-time upgrade of a version 1 file (docs/dev/handwriting.md
// "Upgrading version 1"; gamma/ink.py upgrade_v1 is the same function,
// tests/shared/inkv2.json pins both byte for byte): version 2, and every
// pressure pen (tool pen or absent, pen not false, no brush, p but no w)
// gains w = 500 + clamp(p, 0, 1000), absolute first and deltas after. On
// the raw parsed object, so no default is added; anything but a version
// 1 file (version 2, a newer one, a malformed one) comes back as it is.
// inkStore.loadInk runs it on a file a not-yet-upgraded copy serves;
// nothing re-uploads version 1.
export function upgradeV1(file) {
  if (!file || file.format !== FORMAT || (file.version ?? 1) !== 1) return file;
  const strokes = (file.strokes || []).map((s) => {
    const ch = s.ch || "xy";
    if ((s.tool ?? "pen") !== "pen" || s.pen === false || s.brush != null || !ch.includes("p") || ch.includes("w")) return s;
    const n = ch.length, at = ch.indexOf("p"), pts = [];
    let prev = 0;
    for (let i = 0; i + n <= (s.pts || []).length; i += n) {
      for (let k = 0; k < n; k++) pts.push(s.pts[i + k]);
      const w = 500 + clamp(s.pts[i + at], 0, 1000);
      pts.push(w - prev);
      prev = w;
    }
    return { ...s, ch: ch + "w", pts };
  });
  return { ...file, version: 2, strokes };
}

// What a drawing carries that this build does not know: tools, brushes,
// stroke keys and channel letters, or a newer file version — null when
// nothing. The layer tells the user once (the strokes still draw plainly).
const KNOWN_TOOLS = new Set(["pen", "highlighter"]);
const KNOWN_BRUSHES = new Set(["monoline"]);
const KNOWN_KEYS = new Set(["id", "tool", "brush", "color", "size", "opacity", "pen", "t0", "band", "from", "shape", "fill",
  "dash", "nib", "ch", "pts"]);
export function inkFeatures(ink) {
  const tools = new Set(), brushes = new Set(), keys = new Set(), letters = new Set();
  for (const s of ink?.strokes || []) {
    if (s.tool && !KNOWN_TOOLS.has(s.tool)) tools.add(s.tool);
    if (s.brush && !KNOWN_BRUSHES.has(s.brush)) brushes.add(s.brush);
    for (const k of Object.keys(s)) if (!KNOWN_KEYS.has(k)) keys.add(k);
    for (const c of unknownLetters(s.ch || "xy")) letters.add(c);
  }
  const newer = (ink?.version ?? 1) > VERSION;
  if (!newer && !tools.size && !brushes.size && !keys.size && !letters.size) return null;
  return { newer, tools: [...tools], brushes: [...brushes], keys: [...keys].sort(), letters: [...letters].sort() };
}

// --- geometry --------------------------------------------------------------

const bounds = new WeakMap();
// [x0, y0, x1, y1] around the samples, width included.
export function strokeBounds(stroke) {
  const hit = bounds.get(stroke);
  if (hit) return hit;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (const s of decodeStroke(stroke)) {
    const r = strokeWidth(stroke, s) / 2;
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

// The group's box as a `pdf_position` in the file's page frame
// (shared/model/blockModel.js; what jump-to-position, the sidebar marker
// and the exporters read): null off a PDF page or with no strokes.
export function pdfPositionOf(ink) {
  const b = inkBounds(ink);
  if (!b || ink.space?.kind !== "pdf-page") return null;
  const r2 = (v) => Math.round(v * 100) / 100;
  const rect = { x1: r2(b[0]), y1: r2(b[1]), x2: r2(b[2]), y2: r2(b[3]) };
  return { pageNumber: ink.space.page, width: ink.space.width, height: ink.space.height,
    boundingRect: rect, rects: [{ ...rect }] };
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
    const edge = Math.sqrt(segDist2(x, y, a.x, a.y, c.x, c.y)) - Math.max(strokeWidth(s, a), strokeWidth(s, c)) / 2;
    if (edge < best) best = edge;
  }
  return Math.max(0, best);
}

// Ids of the strokes an eraser at (x, y) with `radius` touches.
export function hitStrokes(ink, x, y, radius) {
  return (ink?.strokes || []).filter((s) => strokeEdgeDistance(s, x, y, radius) <= radius).map((s) => s.id);
}

// Nearest stroke edge within `radius`: ink (band 1) wins over a
// highlighter (band 0) under it, then the last-painted stroke wins ties.
// The caller converts a screen-space touch tolerance into page units.
export function nearestInkStroke(groups, x, y, radius) {
  let best = null, bestBand = -1, distance = radius;
  for (const g of groups) for (const s of g.ink.strokes) {
    const edge = strokeEdgeDistance(s, x, y, radius);
    if (edge > radius) continue;
    const band = strokeBand(s);
    if (band > bestBand || (band === bestBand && edge <= distance)) {
      bestBand = band; distance = edge; best = { id: g.id, ids: [s.id] };
    }
  }
  return best;
}

// A colour, width or opacity change of the selected strokes: `size` alone
// changes for a width (the baked w multiplies it), never `pts`; every
// other key rides along. The original object for a no-op.
export function restyleStrokes(ink, ids, patch) {
  const selected = new Set(ids);
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id) || (patch.tool && patch.tool !== s.tool)) return s;
    const color = HEX_RE.test(patch.color || "") ? patch.color.toLowerCase() : s.color;
    const sizes = sizesFor(s.tool);
    const size = Number.isFinite(patch.size)
      ? Math.round(Math.max(sizes[0], Math.min(sizes.at(-1), patch.size)) * 100) / 100 : s.size;
    const opacity = Number.isFinite(patch.opacity) ? Math.round(Math.min(1, Math.max(0.1, patch.opacity)) * 100) / 100 : s.opacity;
    if (color === s.color && size === s.size && opacity === s.opacity) return s;
    changed = true;
    return { ...s, color, size, opacity };
  });
  return changed ? { ...ink, strokes } : ink;
}

// Fresh-id copies of strokes (ids outside `used`), every key but `from`
// kept: a copy is not a piece of anything. Paste and Duplicate.
export function cloneStrokes(strokes, used = new Set()) {
  return strokes.map(({ from, ...s }) => {
    let id;
    do { id = strokeId(); } while (used.has(id));
    used.add(id);
    return { ...s, id, pts: [...s.pts] };
  });
}

export function duplicateStrokes(ink, ids, dx, dy) {
  const selected = new Set(ids), used = new Set(ink.strokes.map((s) => s.id));
  const originals = ink.strokes.filter((s) => selected.has(s.id));
  if (!originals.length || ink.strokes.length + originals.length > MAX_STROKES) return { ink, ids: [] };
  const copies = cloneStrokes(originals, used);
  const copyIds = copies.map((s) => s.id);
  return { ink: translateStrokes({ ...ink, strokes: [...ink.strokes, ...copies] }, copyIds, dx, dy), ids: copyIds };
}

// Deep equality over JSON values; a missing key equals a null one.
function sameValue(x, y) {
  if (x === y) return true;
  if (Array.isArray(x) || Array.isArray(y)) {
    if (!Array.isArray(x) || !Array.isArray(y) || x.length !== y.length) return false;
    for (let i = 0; i < x.length; i++) if (!sameValue(x[i], y[i])) return false;
    return true;
  }
  if (x && y && typeof x === "object" && typeof y === "object") {
    for (const k of new Set([...Object.keys(x), ...Object.keys(y)])) if (!sameValue(x[k] ?? null, y[k] ?? null)) return false;
    return true;
  }
  return (x ?? null) === (y ?? null);
}

// Two strokes are the same when every key and sample is.
export function sameStroke(a, b) {
  if (a === b) return true;
  if (!a || !b) return false;
  return sameValue(a, b);
}

const sameStrokes = (a, b) => a.length === b.length && a.every((s, i) => sameStroke(s, b[i]));

// --- merge -----------------------------------------------------------------
// A stroke is compared as two halves (docs/dev/handwriting.md "Two writers,
// one group"): its GEOMETRY (pts, ch, size, shape) and its LOOK (every
// other key but id and from, with the defaults filled in so an omitted key
// equals its default value).
const GEOM_KEYS = ["pts", "ch", "size", "shape"];
const NOT_LOOK = new Set(["id", "from", ...GEOM_KEYS]);
const geomOf = (s) => ({ pts: s.pts, ch: s.ch || "xy", size: s.size ?? DEFAULT_SIZE, shape: s.shape ?? null });
function lookOf(s) {
  const out = { tool: "pen", color: INK_COLORS[0], opacity: 1, pen: true };
  for (const k of Object.keys(s)) if (!NOT_LOOK.has(k) && s[k] != null) out[k] = s[k];
  out.band = strokeBand(s);
  if (out.fill) out.fill = { color: out.fill.color, opacity: out.fill.opacity ?? 0.25 };
  return out;
}
const sameGeom = (a, b) => sameValue(geomOf(a), geomOf(b));
const sameLook = (a, b) => sameValue(lookOf(a), lookOf(b));
// The stroke with `geom`'s geometry and `look`'s look, named as `id` (its id and from).
function compose(id, geom, look) {
  const out = {};
  for (const k of Object.keys(look)) if (!NOT_LOOK.has(k) && look[k] != null) out[k] = look[k];
  for (const k of GEOM_KEYS) if (geom[k] != null) out[k] = geom[k];
  out.id = id.id;
  if (id.from != null) out.from = id.from;
  return out;
}

// The merged file: theirs' space, ours' file-level extras (last writer).
function mergedFile(ours, theirs, strokes) {
  const out = {};
  for (const k of Object.keys(ours)) if (!["format", "version", "space", "strokes"].includes(k) && ours[k] != null) out[k] = ours[k];
  return { ...out, format: FORMAT, version: VERSION, space: theirs.space, strokes };
}

// Apply the change base → ours to theirs (the group as stored now), stroke
// by stroke: stroke ids survive every edit, so they play the part text
// offsets play in the text merge. → {ink, clean, lostOurs, returned}:
// `lostOurs` the ids of ours' changes the result dropped (an edit or
// erasure theirs overrode, pieces that were dropped), `returned` the
// strokes theirs had erased that ours' change brings back. Per id, a half
// changed by one side only comes from that side, a half changed by both
// takes theirs (unclean); an edit beats a delete (unclean); a stroke ours
// added goes after the stroke before it that the result keeps, behind
// strokes theirs added there. Pieces (strokes with `from`) follow their
// parent's fate on the other side: unchanged → they stand, restyled → they
// take the look, erased → they go, moved or also cut → theirs' version
// stands and ours' pieces are dropped. base null merges by `baseIds` (a
// stroke one side lacks whose id the base had is erased) or, without them,
// as a union by id. The same rule as gamma/ink.py merge_ink;
// tests/shared/inkmerge.json pins both. Undo and redo rebase through it
// too (the action's after → before onto now).
export function mergeInk(base, ours, theirs, baseIds = null) {
  const bList = base?.strokes || [];
  if (base && sameStrokes(theirs.strokes, bList)) return { ink: ours, clean: true, lostOurs: [], returned: [] };
  if (sameStrokes(ours.strokes, bList) || sameStrokes(ours.strokes, theirs.strokes)) return { ink: theirs, clean: true, lostOurs: [], returned: [] };
  const o = new Map(ours.strokes.map((s) => [s.id, s]));
  const t = new Map(theirs.strokes.map((s) => [s.id, s]));
  let clean = true;
  const lostOurs = [], returned = [];
  const lose = (id) => { clean = false; if (!lostOurs.includes(id)) lostOurs.push(id); };
  const out = [];
  // What a stroke of ours that is not in `out` becomes: the stroke to
  // insert, or null to drop it (the default: an addition inserts itself).
  const oursAs = new Map();
  if (!base) {
    const ids = new Set(baseIds || []);
    for (const th of theirs.strokes) {
      const mine = o.get(th.id);
      if (mine) { if (!sameStroke(mine, th)) lose(th.id); out.push(th); }
      else if (ids.has(th.id)) clean = false;      // ours erased it: deletions win
      else out.push(th);
    }
    for (const s of ours.strokes) if (!t.has(s.id) && ids.has(s.id)) { lose(s.id); oursAs.set(s.id, null); }
  } else {
    const b = new Map(bList.map((s) => [s.id, s]));
    // A side's pieces: strokes it added whose `from` names a base family
    // (the root, or a piece of it) the side no longer has — parent id →
    // the pieces cut from it.
    const pieces = (side) => {
      const byParent = new Map();
      for (const s of side.values()) {
        if (b.has(s.id) || s.from == null) continue;
        for (const p of bList) {
          if ((p.id === s.from || p.from === s.from) && !side.has(p.id)) {
            if (!byParent.has(p.id)) byParent.set(p.id, []);
            byParent.get(p.id).push(s);
          }
        }
      }
      return byParent;
    };
    const oursPieces = pieces(o), theirsPieces = pieces(t);
    // A base stroke's fate on a side: same, look, geom, cut or erased.
    const fate = (p, side, sidePieces) => {
      const s = side.get(p.id);
      if (!s) return sidePieces.has(p.id) ? "cut" : "erased";
      if (sameStroke(s, p)) return "same";
      return sameGeom(s, p) ? "look" : "geom";
    };
    // The pieces cut from `parent` take `look`'s look; when the cutting side
    // restyled them too, theirs' look wins (unclean).
    const restylePieces = (list, parent, look, cutIsOurs) => list.map((piece) => {
      if (sameLook(piece, look)) return piece;
      if (!sameLook(piece, parent)) {
        if (cutIsOurs) { lose(piece.id); return compose(piece, piece, look); }
        lose(parent.id);
        return piece;
      }
      return compose(piece, piece, look);
    });
    const lostMoves = new Set();
    for (const th of theirs.strokes) {
      const was = b.get(th.id), mine = o.get(th.id);
      if (!was) {
        const parents = bList.filter((p) => theirsPieces.get(p.id)?.includes(th));
        if (!parents.length) {                    // theirs' addition
          if (mine && !sameStroke(mine, th)) lose(th.id);
          out.push(th);
          continue;
        }
        // theirs' piece: its parents' fate on ours decides
        const fates = parents.map((p) => fate(p, o, oursPieces));
        if (fates.includes("erased")) continue;   // an undo of the drawing beats the cut
        if (fates.includes("geom")) {              // theirs' pieces stand, ours' move is lost
          parents.forEach((p, i) => { if (fates[i] === "geom" && !lostMoves.has(p.id)) { lostMoves.add(p.id); lose(p.id); } });
          out.push(th);
          continue;
        }
        const i = fates.indexOf("look");
        out.push(i < 0 ? th : restylePieces([th], parents[i], o.get(parents[i].id), false)[0]);
        continue;
      }
      const fo = fate(was, o, oursPieces), ft = fate(was, t, theirsPieces);
      if (fo === "same") { out.push(th); continue; }
      if (fo === "erased") {                      // an edit beats a delete
        if (ft !== "same") { lose(was.id); out.push(th); }
        continue;
      }
      if (fo === "cut") {
        const list = oursPieces.get(was.id);
        if (ft === "geom") { for (const piece of list) { lose(piece.id); oursAs.set(piece.id, null); } out.push(th); }
        else if (ft === "look") for (const piece of restylePieces(list, was, th, true)) oursAs.set(piece.id, piece);
        continue;                                 // ft same: the pieces stand, the parent goes
      }
      // both still have it: half by half
      const geomO = !sameGeom(mine, was), geomT = !sameGeom(th, was);
      const lookO = !sameLook(mine, was), lookT = !sameLook(th, was);
      let geom = th, look = th;
      if (geomO && !geomT) geom = mine; else if (geomO && geomT && !sameGeom(mine, th)) lose(was.id);
      if (lookO && !lookT) look = mine; else if (lookO && lookT && !sameLook(mine, th)) lose(was.id);
      const merged = compose(th, geom, look);
      out.push(sameStroke(merged, th) ? th : sameStroke(merged, mine) ? mine : merged);
    }
    // Base strokes theirs no longer has: ours' edit returns from theirs'
    // erasure; ours' pieces go with it, or lose to theirs' cut.
    for (const was of bList) {
      if (t.has(was.id)) continue;
      const fo = fate(was, o, oursPieces), ft = fate(was, t, theirsPieces);
      if (fo === "look" || fo === "geom") {
        if (ft === "erased") { clean = false; returned.push(was.id); oursAs.set(was.id, o.get(was.id)); }
      } else if (fo === "cut") {
        for (const piece of oursPieces.get(was.id)) { if (ft === "cut") lose(piece.id); oursAs.set(piece.id, null); }
      }
    }
  }
  const inBase = new Set(bList.map((s) => s.id));
  const present = new Set(out.map((s) => s.id));
  let anchor = null;
  for (const s of ours.strokes) {
    if (present.has(s.id)) { anchor = s.id; continue; }
    const add = oursAs.has(s.id) ? oursAs.get(s.id) : inBase.has(s.id) || t.has(s.id) ? null : s;
    if (!add) continue;
    let at = anchor ? out.findIndex((x) => x.id === anchor) + 1 : 0;
    while (at < out.length && !inBase.has(out[at].id) && !o.has(out[at].id)) at++;
    out.splice(at, 0, add);
    present.add(s.id);
    anchor = s.id;
  }
  // a merge that changes nothing is that file itself (a change sent again)
  if (sameStrokes(out, theirs.strokes)) return { ink: theirs, clean, lostOurs, returned };
  if (sameStrokes(out, ours.strokes) && JSON.stringify(ours.space) === JSON.stringify(theirs.space)) return { ink: ours, clean, lostOurs, returned };
  return { ink: mergedFile(ours, theirs, out), clean, lostOurs, returned };
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
// XY channels, preserving pressure, timing, tilt, widths, IDs and other
// metadata; the size scales with the selection.
export function transformStrokes(ink, ids, { cx, cy, scale = 1, angle = 0 }) {
  if (![cx, cy, scale, angle].every(Number.isFinite) || scale <= 0 || (scale === 1 && angle === 0)) return ink;
  const selected = new Set(ids);
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id)) return s;
    changed = true;
    const pts = s.pts.slice(), n = (s.ch || "xy").length;
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

// A stroke's samples → a piece of it: every key but shape and fill (a cut
// shape is just ink), a fresh id, and `from` naming the root stroke, so
// every piece of a stroke names the original (the merge reads it).
function restroke(stroke, samples) {
  const { id, pts, shape, fill, from, ...keys } = stroke;
  return encodeStroke({ ...keys, ch: stroke.ch || "xy", samples, from: from ?? id });
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
      const tol = radius + strokeWidth(s, q) / 2;
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
// A drawing as it was written, on one of two clocks (docs/dev/recording.md
// "One clock for every replay"). The strokes' own clock is wall-clock
// time: a stroke starts at its `t0` plus its first sample's `t` (a run the
// partial eraser cut out of a longer stroke keeps the times it had) and
// its samples follow their `t`; `strokeWalls` reads it, and `inkUntil`
// draws the drawing as it stood at an instant on it — what a recording's
// player drives from the audio. The compressed clock of a group's own
// replay button is `inkTimeline`: the same strokes in the order they were
// drawn, pauses between strokes and inside one shrunk to `pause` and the
// whole scaled into `max`, so a page written over an hour replays in
// seconds; a stroke without timing counts as drawn right after the stroke
// before it in the file, its samples spread over UNTIMED_MS. `inkAtTime` is
// `inkUntil` on that clock.
export const REPLAY_PAUSE = 400;
export const REPLAY_MAX = 15000;
const UNTIMED_MS = 300;

const walls = new WeakMap();
// Each sample's wall-clock ms, or null for a stroke no clock places (no
// `t0`, or no `t` channel: imported foreign ink). Cached per stroke object.
export function strokeWalls(stroke) {
  if (walls.has(stroke)) return walls.get(stroke);
  const samples = decodeStroke(stroke);
  const timed = Number.isFinite(stroke.t0) && samples.length > 0 && samples.every((p) => p.t != null);
  const out = timed ? samples.map((p) => stroke.t0 + p.t) : null;
  walls.set(stroke, out);
  return out;
}

const spans = new WeakMap();
// [first start, last end] in wall-clock ms over the timed strokes, or null
// when none is timed. Cached per drawing object.
export function inkWallSpan(ink) {
  if (!ink) return null;
  if (spans.has(ink)) return spans.get(ink);
  let out = null;
  for (const stroke of ink.strokes || []) {
    const ts = strokeWalls(stroke);
    if (!ts) continue;
    out = out ? [Math.min(out[0], ts[0]), Math.max(out[1], ts[ts.length - 1])] : [ts[0], ts[ts.length - 1]];
  }
  spans.set(ink, out);
  return out;
}

// → {duration, strokes: [{id, index, start, end, times}], times} in ms from
// the replay's start, in drawing order: `index` is the stroke's place in
// the file, `times` each sample's time, and the map `times` the same per
// id — inkUntil's table.
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
  return { duration: clock, strokes, times: new Map(strokes.map((s) => [s.id, s.times])) };
}

// The drawing as it stood at instant `at`: `times` is a clock's table of
// stroke id → each sample's instant on it (a timeline's), else the strokes'
// own wall clock. The strokes begun by `at` are drawn in the order they
// were begun, the one being written cut to the samples it had (a prefix of
// its delta-coded `pts` is its first samples); a stroke the clock does not
// place is drawn as it is, under the rest. → {ink, pending, latest, done}:
// `pending` the ids not yet begun, `latest` the id begun last, `done`
// whether every stroke is complete — `ink` is then the file itself.
export function inkUntil(ink, at, times = null) {
  if (!ink) return { ink, pending: [], latest: null, done: true };
  const drawn = [], pending = [];
  let latest = null, latestAt = -Infinity, done = true;
  for (const stroke of ink.strokes || []) {
    const ts = times ? times.get(stroke.id) : strokeWalls(stroke);
    if (!ts?.length) { drawn.push({ stroke, at: -Infinity }); continue; }
    if (ts[0] > at) { pending.push(stroke.id); done = false; continue; }
    if (ts[0] >= latestAt) { latestAt = ts[0]; latest = stroke.id; }
    if (ts[ts.length - 1] <= at) { drawn.push({ stroke, at: ts[0] }); continue; }
    done = false;
    let k = 1;
    while (k < ts.length && ts[k] <= at) k++;
    drawn.push({ stroke: { ...stroke, pts: stroke.pts.slice(0, k * (stroke.ch || "xy").length) }, at: ts[0] });
  }
  if (done) return { ink, pending, latest, done };
  drawn.sort((a, b) => a.at - b.at);
  return { ink: { ...ink, strokes: drawn.map((d) => d.stroke) }, pending, latest, done };
}

// The drawing at replay time `t` of `timeline` (inkTimeline's).
export function inkAtTime(ink, timeline, t) {
  return inkUntil(ink, t, timeline.times).ink;
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
// like ink on paper. perfect-freehand is driven from the baked width:
// thinning 1 with pressure w / 2 gives the radius size × w / 2, exactly
// the drawn width; a stroke without w draws its size.
export function outlineOptions(stroke) {
  return { size: stroke.size ?? DEFAULT_SIZE, thinning: 1, smoothing: 0.5, streamline: 0.4, simulatePressure: false, last: true };
}
export const outlinePressure = (sample) => (sample.w != null ? sample.w / 2 : 0.5);

// samples → the filled outline path for a pen stroke.
export function penOutline(samples, opts) {
  return svgPathFromPoints(getStroke(samples.map((s) => [s.x, s.y, outlinePressure(s)]), opts));
}

// A stroked path's dash pattern from `dash`, in units of its width (the
// geometry is never stored); undefined for a solid line.
export function dashArray(stroke, width = stroke.size) {
  if (stroke.dash === "dashed") return `${(width * 3).toFixed(2)} ${(width * 2).toFixed(2)}`;
  if (stroke.dash === "dotted") return `0.01 ${(width * 2).toFixed(2)}`;
  return undefined;
}

const paths = new WeakMap();
// The SVG rendering of a stroke: {d, stroke: false} (a pen, any brush: a
// filled outline from w) or {d, stroke: true, width, multiply, dash} (a
// stroked polyline: the highlighter at its size, multiplied onto the page;
// an unknown tool at its mean drawn width, source-over). Cached per object.
export function strokePath(stroke) {
  const hit = paths.get(stroke);
  if (hit) return hit;
  const pts = decodeStroke(stroke);
  const tool = stroke.tool || "pen";
  let out;
  if (tool === "pen") {
    out = { d: penOutline(pts, outlineOptions(stroke)), stroke: false };
  } else {
    const d = pts.map((s, i) => `${i ? "L" : "M"}${s.x.toFixed(2)},${s.y.toFixed(2)}`).join(" ")
      + (pts.length === 1 ? ` L${(pts[0].x + 0.01).toFixed(2)},${pts[0].y.toFixed(2)}` : "");
    const width = tool === "highlighter" || !pts.length ? stroke.size ?? DEFAULT_SIZE
      : pts.reduce((sum, s) => sum + strokeWidth(stroke, s), 0) / pts.length;
    out = { d, stroke: true, width, multiply: tool === "highlighter", dash: dashArray(stroke, width) };
  }
  paths.set(stroke, out);
  return out;
}
