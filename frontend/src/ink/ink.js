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
// The w channel's ceiling, decoded: the pencil's slant reaches 3.5 (it was
// 2 before the pencil); the drawn ceiling stays size × max(w) ≤ 100.
export const MAX_W = 4;
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
// A fresh stroke id, 8 characters (the layer mints a stroke's id at the
// pen's down, so the wet stroke peers see and the committed one share it).
export function strokeId() {
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
// The pen styles (docs/dev/handwriting.md "The brush table"): the brush a
// pen preset or a pen stroke names — the plain Pen is the absent key — in
// the order the Style picker lists them and Shift+[ / ] step through
// them. A highlighter has none. Their laws are ink/finishStroke.js BRUSHES.
export const BRUSH_ORDER = [null, "monoline", "brush", "pencil", "calligraphy"];
export const KNOWN_BRUSHES = new Set(["monoline", "brush", "pencil", "calligraphy"]);
// The calligraphy pen's nib (docs/dev/handwriting.md "Calligraphy"): a
// preset's angle is an integer from NIB_MIN to NIB_MAX degrees, stepped by
// NIB_STEP on the strip, DEFAULT_NIB to start, counterclockwise as seen (45
// rises to the right); a stroke's `nib` is that angle as an undirected
// line, 0..179, the nib's edge the page vector (cos nib, −sin nib).
export const DEFAULT_NIB = 45, NIB_MIN = -60, NIB_MAX = 60, NIB_STEP = 15;
// The hairline a nib draws along its own edge, in sizes: the floor of the
// calligraphy law (finishStroke.js BRUSHES), which the screen draws as a
// segment that long across the nib, swept with it (nibOutline); and how
// far the nib's corners reach from a sample, in sizes: the diagonal of the
// nib and that hairline (half of it each side).
export const NIB_HAIR = 0.12;
export const NIB_REACH = Math.hypot(1, NIB_HAIR);
// A calligraphy pen smooths (ink/stabilizer.js) at this level unless its
// preset sets its own, Off (0) included: a default the layer draws with,
// never written into the preset, so a pen stepped through Calligraphy to
// another style keeps the smoothing it had.
export const CALLIGRAPHY_SMOOTHING = 2;
// The smoothing level a preset draws with, 0–4: its own, else its brush's default.
export const presetSmoothing = (preset) => preset?.stabilize
  ?? (preset?.kind === "pen" && preset.brush === "calligraphy" ? CALLIGRAPHY_SMOOTHING : 0);
// A stroke's nib from its preset's angle and the pen's twist at the
// stroke's first sample (PointerEvent.twist, or the iPad host's roll:
// degrees clockwise as seen, so a clockwise roll turns the nib clockwise):
// (nib − twist) mod 180, rounded; the twist left out when it is null or
// `lock` (the preset's Lock angle) is on.
export function strokeNib(nib, twist = null, lock = false) {
  const angle = Number.isFinite(nib) ? nib : DEFAULT_NIB;
  const turn = !lock && Number.isFinite(twist) ? twist : 0;
  return ((Math.round(angle - turn) % 180) + 180) % 180;
}
// A line: dashed or dotted, solid when absent (DASH below).
export const isDash = (v) => v === "dashed" || v === "dotted";

export function toolId() {
  return "t" + strokeId();
}
export function sizesFor(kind) {
  return kind === "highlighter" ? HIGHLIGHTER_SIZES : PEN_SIZES;
}
// The keys a preset is known by here; any other key it carries is kept as
// it is (below), so an older build that edits one preset never strips what
// a newer one wrote — while the preset serialises under TOOL_MAX_BYTES.
const KNOWN_TOOL_KEYS = new Set(["id", "kind", "color", "size", "opacity", "brush", "nib", "nibLock", "dash", "stabilize", "straight", "hidden"]);
const TOOL_MAX_BYTES = 512;   // UTF-8 bytes of the preset's JSON, what the profile stores
const utf8Bytes = (s) => new TextEncoder().encode(s).length;
// A stored list (the account's profile, localStorage, possibly hand-edited
// or from an older or newer build) → a valid preset list, or the defaults
// when nothing survives. At least one preset stays visible: a list that
// hides every one shows its first.
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
    // Smoothing (ink/stabilizer.js) 0–4, left out at 0 — but kept at 0 on a
    // calligraphy pen, whose smoothing left out is CALLIGRAPHY_SMOOTHING; a
    // highlighter's straightening, 'always' | 'off', left out at the default
    // 'hold'; a hidden preset (off the strip, still in the list), left out
    // when shown.
    const stabilize = t.stabilize == null ? NaN : Math.round(Number(t.stabilize));
    // A calligraphy pen's nib angle, an integer from NIB_MIN to NIB_MAX left
    // out at DEFAULT_NIB, and its Lock angle, left out when off: neither
    // stays on any other preset.
    const calligraphy = kind === "pen" && t.brush === "calligraphy";
    const nib = t.nib == null ? NaN : Math.round(Number(t.nib));
    const preset = { id, kind, color: t.color.toLowerCase(), size: Math.round(size * 100) / 100,
      ...(Number.isFinite(opacity) && opacity >= 0.1 && opacity <= 1 && opacity !== defaultOpacity(kind)
        ? { opacity: Math.round(opacity * 100) / 100 } : {}),
      // a pen's brush among the known ones ('pen', an unknown value or any
      // brush on a highlighter is dropped: the plain pen is the absent
      // key); a line, pens and highlighters alike, left out when solid
      ...(kind === "pen" && KNOWN_BRUSHES.has(t.brush) ? { brush: t.brush } : {}),
      ...(calligraphy && Number.isFinite(nib) && nib >= NIB_MIN && nib <= NIB_MAX && nib !== DEFAULT_NIB ? { nib } : {}),
      ...(calligraphy && t.nibLock === true ? { nibLock: true } : {}),
      ...(isDash(t.dash) ? { dash: t.dash } : {}),
      ...(Number.isFinite(stabilize) && stabilize >= (calligraphy ? 0 : 1) && stabilize <= 4 ? { stabilize } : {}),
      ...(kind === "highlighter" && (t.straight === "always" || t.straight === "off") ? { straight: t.straight } : {}),
      ...(t.hidden === true ? { hidden: true } : {}) };
    // The keys this build does not know, verbatim and after the known ones,
    // each only while the preset stays under the byte cap.
    for (const [key, value] of Object.entries(t)) {
      if (KNOWN_TOOL_KEYS.has(key) || value === undefined) continue;
      if (utf8Bytes(JSON.stringify({ ...preset, [key]: value })) > TOOL_MAX_BYTES) continue;
      preset[key] = value;
    }
    out.push(preset);
    if (out.length >= MAX_TOOLS) break;
  }
  if (!out.length) return DEFAULT_TOOLS.map((t) => ({ ...t }));
  if (out.every((t) => t.hidden)) { const { hidden, ...first } = out[0]; out[0] = first; }
  return out;
}
// The presets on the strip: the list without its hidden ones. What the
// row, the key table (ink/stripKeys.js) and the stylus's backTool read.
export function visibleTools(list) {
  return list.some((t) => t.hidden) ? list.filter((t) => !t.hidden) : list;
}
// A kind's opacity when a preset names none.
export const defaultOpacity = (kind) => (kind === "highlighter" ? HIGHLIGHTER_OPACITY : 1);
// What the layer draws with for a preset: {tool, color, size, opacity} and
// a pen's brush, a calligraphy pen's nib (0..179, strokeNib) and the line
// (dash) when it has them — the keys a stroke is encoded with — plus the
// smoothing level it draws with (presetSmoothing: a calligraphy pen's
// default included), a highlighter's straightening and a calligraphy
// pen's Lock angle (`nibLock`: the layer reads them; a stroke's style
// never carries them).
export function toolStyle(preset) {
  const opacity = preset.opacity ?? defaultOpacity(preset.kind);
  const level = presetSmoothing(preset);
  const input = level ? { stabilize: level } : {};
  const line = isDash(preset.dash) ? { dash: preset.dash } : {};
  return preset.kind === "highlighter"
    ? { tool: "highlighter", color: preset.color, size: preset.size, opacity, ...input,
      ...(preset.straight ? { straight: preset.straight } : {}), ...line }
    : { tool: "pen", color: preset.color, size: preset.size, opacity, ...input,
      ...(KNOWN_BRUSHES.has(preset.brush) ? { brush: preset.brush } : {}),
      ...(preset.brush === "calligraphy" ? { nib: strokeNib(preset.nib), ...(preset.nibLock ? { nibLock: true } : {}) } : {}),
      ...line };
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
      // a sample without tilt in a tilted stroke is upright: a 90, z 0
      else if (c === "a") v = Math.round(clamp(s.a ?? 90, 0, 90));
      else if (c === "z") v = Math.round(s.z ?? 0);
      else if (c === "w") v = Math.round(clamp(s.w ?? 1, 0, MAX_W) * WIDTH_UNIT);
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

// --- caches ------------------------------------------------------------------
// What a stroke's samples give — the decoded samples, its bounds, its
// outline — is worked out once per `pts` array, not per stroke object
// (docs/dev/handwriting.md "Client", Caches). An edit that leaves the
// samples alone keeps the array (a colour or line restyle, withDefaults,
// the merge's composed strokes, a reload whose strokes
// inkStore.adoptStrokes matched by id to the arrays already drawn), so
// nothing is built again; one that changes them (a move, a cut, a brush
// re-bake) writes a new array. A value is keyed by what else it was built
// from as well (`keyOf`: the size for the bounds; every key but the
// colour, id, from and t0 for an outline, so an outline must never read
// the colour), and a WeakMap per stroke object sits in front, so a repeat
// costs one lookup.
const byPts = new WeakMap();   // pts array → {ch, values: {cache name → Map(key → value)}}
function strokeCache(name, keyOf) {
  const objects = new WeakMap();
  const slot = (stroke) => {
    const pts = stroke?.pts;
    if (!pts || typeof pts !== "object") return null;
    const ch = stroke.ch || "xy";
    let entry = byPts.get(pts);
    if (!entry || entry.ch !== ch) { entry = { ch, values: {} }; byPts.set(pts, entry); }
    return entry.values[name] || (entry.values[name] = new Map());
  };
  return {
    get(stroke) {
      if (objects.has(stroke)) return objects.get(stroke);
      const map = slot(stroke);
      const key = map ? keyOf(stroke) : null;
      if (!map || !map.has(key)) return undefined;
      const value = map.get(key);
      objects.set(stroke, value);
      return value;
    },
    set(stroke, value) {
      objects.set(stroke, value);
      slot(stroke)?.set(keyOf(stroke), value);
    },
  };
}
// The outline's key: every key it may depend on, so a change of any of
// them (size, tool, brush, dash, opacity, band, an extra) builds it again.
const NOT_OUTLINE = new Set(["id", "pts", "ch", "color", "from", "t0"]);
function outlineKey(stroke) {
  let key = "";
  for (const k of Object.keys(stroke).sort()) {
    const v = stroke[k];
    if (!NOT_OUTLINE.has(k) && v != null) key += `${k}=${typeof v === "object" ? JSON.stringify(v) : v};`;
  }
  return key;
}
// The outline already built for a stroke (strokePath), or undefined —
// never building it: what the layer's idle builder asks before it renders
// a dense group's placeholder.
export const cachedStrokePath = (stroke) => paths.get(stroke);
const lines = strokeCache("line", (s) => `${s.tool}|${s.size}`);
// A stroke's stand-in while its outline is still to be built (a group of
// more than the layer's IDLE_STROKES): the samples as a stroked polyline
// at the stroke's mean drawn width, from the decoded samples alone —
// strokePath's stroked shape, {d, stroke: true, width, multiply}.
export function strokePlaceholder(stroke) {
  const hit = lines.get(stroke);
  if (hit) return hit;
  const pts = decodeStroke(stroke);
  const d = pts.map((s, i) => `${i ? "L" : "M"}${s.x.toFixed(2)},${s.y.toFixed(2)}`).join(" ")
    + (pts.length === 1 ? ` L${(pts[0].x + 0.01).toFixed(2)},${pts[0].y.toFixed(2)}` : "");
  const width = pts.length ? pts.reduce((sum, s) => sum + strokeWidth(stroke, s), 0) / pts.length : stroke.size ?? DEFAULT_SIZE;
  const out = { d, stroke: true, width, multiply: stroke.tool === "highlighter" };
  lines.set(stroke, out);
  return out;
}

const decoded = strokeCache("decoded", () => "");
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
      else if (c === "w") s.w = clamp(v / WIDTH_UNIT, 0, MAX_W);
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
// The drawn opacity at a sample: opacity × o when the stroke carries o (the
// pencil's shading), else opacity. The same one line as the width
// (gamma/ink.py stroke_opacity).
export const strokeOpacity = (stroke, sample) => (sample?.o != null ? (stroke.opacity ?? 1) * sample.o : stroke.opacity ?? 1);
// The widest a stroke draws, in points: size × its largest w (its size
// without w) — what the server caps at MAX_STROKE_SIZE (size × max(w) ≤ 100).
export function strokeMaxWidth(stroke) {
  let max = 0;
  for (const s of decodeStroke(stroke)) max = Math.max(max, strokeWidth(stroke, s));
  return max || (stroke.size ?? DEFAULT_SIZE);
}
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

// A calligraphy stroke drawn with its nib (one without a shape record)
// reaches as far as the nib's corners from every sample, whatever its w:
// half the nib and its hairline's diagonal, in sizes (nibOutline below;
// gamma/ink.py bounding_box pads the same).
const nibDrawn = (stroke) => stroke.brush === "calligraphy" && !stroke.shape;
const bounds = strokeCache("bounds", (s) => `${s.size ?? DEFAULT_SIZE}|${nibDrawn(s) ? "nib" : ""}`);
// [x0, y0, x1, y1] around the samples, width included.
export function strokeBounds(stroke) {
  const hit = bounds.get(stroke);
  if (hit) return hit;
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  const nibReach = nibDrawn(stroke) ? (stroke.size ?? DEFAULT_SIZE) * NIB_REACH / 2 : 0;
  for (const s of decodeStroke(stroke)) {
    const r = Math.max(strokeWidth(stroke, s) / 2, nibReach);
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

// The eraser filter (the account's inkEraserFilter) as one predicate: what
// any erasure — the eraser tool, the pen's eraser end, a scribble — may
// remove. 'all' keeps every stroke, 'pen' the pen tool of any brush,
// 'highlighter' the highlighter; a stroke of an unknown tool goes only
// under 'all'.
export function eraserKeep(filter) {
  if (!filter || filter === "all") return () => true;
  return (s) => (s.tool || "pen") === filter;
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

// The shapes with an inside, the ones a fill is drawn in (gamma/ink.py
// CLOSED_SHAPES): a stroke's `fill` counts only on a shape record of these
// kinds, and FILL_OPACITY is a fill's opacity when it names none.
export const CLOSED_SHAPES = new Set(["rect", "ellipse", "triangle", "polygon"]);
export const FILL_OPACITY = 0.25;
export const isClosedShape = (stroke) => CLOSED_SHAPES.has(stroke?.shape?.kind);
// A fill as a stroke carries it: {color: '#rrggbb', opacity?}, the opacity
// left out at FILL_OPACITY; null for none, undefined for a value that is no
// fill.
function fillOf(value) {
  if (value === null) return null;
  if (!value || !HEX_RE.test(value.color || "")) return undefined;
  const opacity = Number(value.opacity);
  const kept = Number.isFinite(opacity) && opacity > 0 && opacity <= 1 ? Math.round(opacity * 100) / 100 : FILL_OPACITY;
  return { color: value.color.toLowerCase(), ...(kept !== FILL_OPACITY ? { opacity: kept } : {}) };
}
const sameFill = (a, b) => (a?.color ?? null) === (b?.color ?? null) && (a?.opacity ?? FILL_OPACITY) === (b?.opacity ?? FILL_OPACITY);
// A colour, width, opacity, line or fill change of the selected strokes:
// `size` alone changes for a width (the baked w multiplies it), never
// `pts`; `dash` is 'dashed' | 'dotted', or null for solid (the key goes),
// any other value ignored, and applies to every tool; `fill` is {color,
// opacity?} or null for none, and applies to the closed shapes alone
// (isClosedShape: every other stroke stays as it is for it); every other
// key rides along (a brush change re-bakes w, so it is
// ink/finishStroke.js restyleBrush). The original object for a no-op.
export function restyleStrokes(ink, ids, patch) {
  const selected = new Set(ids);
  const line = "dash" in patch && (patch.dash === null || isDash(patch.dash)) ? { dash: patch.dash } : null;
  const fill = "fill" in patch ? fillOf(patch.fill) : undefined;
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id) || (patch.tool && patch.tool !== s.tool)) return s;
    const color = HEX_RE.test(patch.color || "") ? patch.color.toLowerCase() : s.color;
    const sizes = sizesFor(s.tool);
    const size = Number.isFinite(patch.size)
      ? Math.round(Math.max(sizes[0], Math.min(sizes.at(-1), patch.size)) * 100) / 100 : s.size;
    const opacity = Number.isFinite(patch.opacity) ? Math.round(Math.min(1, Math.max(0.1, patch.opacity)) * 100) / 100 : s.opacity;
    const dash = line ? line.dash : s.dash ?? null;
    const filled = fill !== undefined && isClosedShape(s) ? fill : s.fill ?? null;
    if (color === s.color && size === s.size && opacity === s.opacity && dash === (s.dash ?? null) && sameFill(filled, s.fill)) return s;
    changed = true;
    const out = { ...s, color, size, opacity };
    if (dash) out.dash = dash; else delete out.dash;
    if (filled) out.fill = filled; else delete out.fill;
    return out;
  });
  return changed ? { ...ink, strokes } : ink;
}

// Fresh-id copies of strokes (ids outside `used`), moved by (dx, dy)
// points through the first sample's two absolutes, a shape record's params
// with them, every key but `from` kept — t0, p, t, w, shape, extras: a copy
// is not a piece of anything. The copy every paste and Duplicate is made of.
export function cloneStrokes(strokes, dx = 0, dy = 0, used = new Set()) {
  const ex = Math.round((dx || 0) * COORD_UNIT), ey = Math.round((dy || 0) * COORD_UNIT);
  const shift = ex || ey ? movedBy(ex, ey) : null;
  return strokes.map(({ from, ...s }) => {
    let id;
    do { id = strokeId(); } while (used.has(id));
    used.add(id);
    const pts = [...s.pts];
    if (pts.length < 2 || !shift) return { ...s, id, pts };
    pts[0] += ex; pts[1] += ey;
    return carryShape(s, { ...s, id, pts }, shift);
  });
}

export function duplicateStrokes(ink, ids, dx, dy) {
  const selected = new Set(ids), used = new Set(ink.strokes.map((s) => s.id));
  const originals = ink.strokes.filter((s) => selected.has(s.id));
  if (!originals.length || ink.strokes.length + originals.length > MAX_STROKES) return { ink, ids: [] };
  const copies = cloneStrokes(originals, dx, dy, used);
  return { ink: { ...ink, strokes: [...ink.strokes, ...copies] }, ids: copies.map((s) => s.id) };
}

// `strokes` appended to a group as they are (a paste's fresh copies, a
// merge's moved strokes, `from` and all); one whose id the group already
// holds is given a fresh id, so a group never carries an id twice. → {ink,
// ids} with the ids as they landed; the same object with nothing to add.
export function appendStrokes(ink, strokes) {
  if (!strokes?.length) return { ink, ids: [] };
  const used = new Set((ink.strokes || []).map((s) => s.id));
  const added = strokes.map((s) => {
    let id = s.id;
    if (!id || used.has(id)) { do { id = strokeId(); } while (used.has(id)); }
    used.add(id);
    return id === s.id ? s : { ...s, id };
  });
  return { ink: { ...ink, strokes: [...(ink.strokes || []), ...added] }, ids: added.map((s) => s.id) };
}

// The selected strokes mirrored about (cx, cy): axis 'h' rewrites every x
// as 2cx − x (left to right), 'v' every y as 2cy − y (top to bottom), in
// the samples alone — size, p, t, w, ids and channels stay — with a
// `shape` record carried through the same reflection (its angle to 180° −
// angle, or −angle) and a calligraphy `nib` as (180 − nib) mod 180, the
// same on either flip (an undirected angle reflects the same way about both
// axes). The centre is taken on the 1/100 pt grid, so two flips give back
// the very integers. The same object when nothing is selected.
export function flipStrokes(ink, ids, { cx, cy, axis }) {
  const h = axis === "h", v = axis === "v";
  if ((!h && !v) || !Number.isFinite(h ? cx : cy)) return ink;
  const selected = new Set(ids);
  const twice = Math.round(2 * (h ? cx : cy) * COORD_UNIT);   // 2c, in 1/100 pt
  const c2 = twice / COORD_UNIT;
  const mirror = h ? { point: (x, y) => [c2 - x, y], turn: (a) => 180 - a } : { point: (x, y) => [x, c2 - y], turn: (a) => -a };
  let changed = false;
  const strokes = ink.strokes.map((s) => {
    if (!selected.has(s.id) || (s.pts || []).length < 2) return s;
    changed = true;
    const ch = s.ch || "xy", n = ch.length, at = h ? 0 : 1, pts = s.pts.slice();
    // the channel is delta-coded: the first value flips about 2c and every
    // later difference changes sign
    pts[at] = twice - pts[at];
    for (let i = n + at; i < pts.length; i += n) pts[i] = -pts[i] || 0;
    const out = carryShape(s, { ...s, pts }, mirror);
    if (Number.isFinite(s.nib)) out.nib = (180 - s.nib) % 180;
    return out;
  });
  return changed ? { ...ink, strokes } : ink;
}

// A shape record carried through a map of the page that keeps shapes — a
// move, a uniform scale and rotation about a centre, a mirror — so the
// params an editor rebuilds `pts` from (shapes.js shapeSamples) follow the
// samples: its points (`cx, cy` and every `xN, yN` — a line's or an
// arrow's ends, a triangle's corners, a curve's points) through
// `point(x, y) → [x, y]`, its lengths (`w`, `h`, `r`, `rx`, `ry`, `head`)
// times `scale`, and its `angle` (degrees) through `turn`, back into the
// range shapes.js gives it (a rectangle's [−45°, 45°) with its sides
// swapped across a quarter turn, an ellipse's [0°, 180°), a polygon's
// [0°, 360° / n)); every number it changes on the recogniser's 1/100 grid;
// a side count, a flag and a key it does not know as they are. What
// shapes.js draws in place of a key left out is written when the edit
// changes it: an arrow's head (min(length / 4, 24)) on a scale, the angle
// of a rectangle, a polygon or an ellipse (0) on a turn. A record of
// a kind this build does not know goes (undefined): its params may be
// geometry it cannot carry, and stale ones would rebuild the wrong `pts`.
// Also how a held shape crosses between page points and the iPad host's
// viewport css px (hostInk.js shapeToHost, shapeFromHost).
const SHAPE_RECORDS = new Set(["line", "arrow", "triangle", "rect", "polygon", "circle", "ellipse", "curve"]);
const SHAPE_LENGTHS = ["w", "h", "r", "rx", "ry", "head"];
const SHAPE_TURNED = new Set(["rect", "polygon", "ellipse"]);
const mod = (a, m) => ((a % m) + m) % m;
const onGrid = (v) => Math.round(v * 100) / 100 || 0;
export function mapShape(shape, { point, scale = 1, turn = null }) {
  if (!SHAPE_RECORDS.has(shape?.kind)) return undefined;
  const p = shape.params;
  if (!p || typeof p !== "object") return shape;
  const out = { ...p };
  const pairs = [["cx", "cy"], ...Object.keys(p).filter((k) => /^x\d+$/.test(k)).map((k) => [k, `y${k.slice(1)}`])];
  for (const [kx, ky] of pairs) {
    if (!Number.isFinite(p[kx]) || !Number.isFinite(p[ky])) continue;
    const [x, y] = point(p[kx], p[ky]);
    out[kx] = onGrid(x);
    out[ky] = onGrid(y);
  }
  if (scale !== 1) for (const k of SHAPE_LENGTHS) if (Number.isFinite(p[k])) out[k] = onGrid(p[k] * scale);
  if (scale !== 1 && shape.kind === "arrow" && p.head == null && [p.x1, p.y1, p.x2, p.y2].every(Number.isFinite)) {
    out.head = onGrid(Math.min((Math.hypot(p.x2 - p.x1, p.y2 - p.y1) || 1) / 4, 24) * scale);
  }
  const angle = Number.isFinite(p.angle) ? p.angle : SHAPE_TURNED.has(shape.kind) && p.angle == null ? 0 : null;
  if (turn && angle !== null) {
    const raw = onGrid(turn(angle));
    let a = raw;
    if (shape.kind === "rect") {
      a = mod(raw + 45, 90) - 45;
      if (Math.round((raw - a) / 90) % 2) [out.w, out.h] = [out.h, out.w];
    } else if (shape.kind === "polygon" && p.n > 0) a = mod(raw, 360 / p.n);
    else if (shape.kind === "ellipse") a = mod(raw, 180);
    out.angle = onGrid(a);
  }
  return { ...shape, params: out };
}
// `out`, a copy of `s` with its samples moved, with s's shape record moved
// alike (mapShape), or without it when the record cannot follow.
function carryShape(s, out, map) {
  if (!s.shape) return out;
  const shape = mapShape(s.shape, map);
  if (shape) out.shape = shape;
  else delete out.shape;
  return out;
}
// The map of a move by (ex, ey), in 1/100 pt.
const movedBy = (ex, ey) => ({ point: (x, y) => [x + ex / COORD_UNIT, y + ey / COORD_UNIT] });

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
// one group"): its GEOMETRY (pts, ch, size, shape, and brush and nib, since
// a brush or a nib change bakes the w in pts again, and a turn or a flip of
// a calligraphy stroke moves its nib with its samples: the keys travel with
// the widths baked for them) and its LOOK (every other key but id and
// from, with the defaults filled in so an omitted key equals its default
// value).
const GEOM_KEYS = ["pts", "ch", "size", "shape", "brush", "nib"];
const NOT_LOOK = new Set(["id", "from", ...GEOM_KEYS]);
const geomOf = (s) => ({ pts: s.pts, ch: s.ch || "xy", size: s.size ?? DEFAULT_SIZE, shape: s.shape ?? null, brush: s.brush ?? null,
  nib: s.nib ?? null });
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
// the delta encoding, so a translation touches two integers per stroke (a
// shape record's points move with them).
export function translateStrokes(ink, ids, dx, dy) {
  const move = new Set(ids);
  const ex = Math.round(dx * COORD_UNIT), ey = Math.round(dy * COORD_UNIT);
  if (!ex && !ey) return ink;
  const shift = movedBy(ex, ey);
  return { ...ink, strokes: (ink.strokes || []).map((s) => {
    if (!move.has(s.id) || s.pts.length < 2) return s;
    const pts = s.pts.slice();
    pts[0] += ex;
    pts[1] += ey;
    return carryShape(s, { ...s, pts }, shift);
  }) };
}

// (x, y) scaled by `scale` and rotated by `angle` (radians) about (cx, cy).
export function transformPoint(x, y, { cx, cy, scale, angle }) {
  const cos = Math.cos(angle), sin = Math.sin(angle), dx = x - cx, dy = y - cy;
  return [cx + scale * (dx * cos - dy * sin), cy + scale * (dx * sin + dy * cos)];
}

// Uniform scaling/rotation around a shared page-space origin. Rewrite only
// XY channels, preserving pressure, timing, tilt, widths, IDs and other
// metadata; the size scales with the selection, a shape record's params go
// through the same map, and a calligraphy nib turns with the samples, (nib
// − angle°) mod 180 rounded (the angle turns clockwise as seen, a nib
// counts counterclockwise), so its baked widths stay right.
export function transformStrokes(ink, ids, { cx, cy, scale = 1, angle = 0 }) {
  if (![cx, cy, scale, angle].every(Number.isFinite) || scale <= 0 || (scale === 1 && angle === 0)) return ink;
  const selected = new Set(ids);
  const map = { point: (x, y) => transformPoint(x, y, { cx, cy, scale, angle }), scale, turn: (a) => a + (angle * 180) / Math.PI };
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
    const out = carryShape(s, { ...s, pts, size: Math.max(0.01, Math.min(MAX_STROKE_SIZE, s.size * scale)) }, map);
    if (Number.isFinite(s.nib) && angle) out.nib = ((Math.round(s.nib - (angle * 180) / Math.PI) % 180) + 180) % 180;
    return out;
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

// --- the eraser --------------------------------------------------------------
// The eraser sweeps its path (page points): every two consecutive points
// and all within `radius` of the segment between them, a capsule, a path of
// one point being a disk. A stroke lies under the sweep where its centre
// line comes within radius plus its drawn half-width there (strokeWidth /
// 2, linear between two samples). The test runs along the segments between
// samples, not at the samples, so a fast eraser, whose points lie far
// apart, and a sparse stroke (a fast mouse stroke, a held line's two
// samples) are cut wherever the sweep crosses them. Per capsule, the part
// of one such segment under it is one interval: the distance from a point
// moving along a segment to a segment is convex, and less a linear
// half-width it stays convex, so a point inside is found first and the
// interval's ends by bisection from it.

// pt: an overlap shallower than GRAZE is a touch, and cuts nothing. It is
// the file's grid: a cut end, rounded onto it, may lie about that far
// inside the edge, and a sweep over the cut again (the next flush starts
// where this one ended) must leave it as it is.
const GRAZE = 1 / COORD_UNIT;
const EDGE = 1e-3;     // pt: how near the eraser's edge a cut lands (just outside it)
const MIN_PIECE = 0.2; // pt: a piece shorter than this, or than half its drawn width, is a speck
const PHI = (Math.sqrt(5) - 1) / 2;

// A path's capsules, each with its box padded by the radius, and their union.
function sweepOf(path, radius) {
  const pts = (path || []).filter((p) => Number.isFinite(p?.x) && Number.isFinite(p?.y));
  if (!pts.length || !(radius >= 0)) return null;
  const caps = [], box = [Infinity, Infinity, -Infinity, -Infinity];
  for (let i = 0; i < Math.max(1, pts.length - 1); i++) {
    const c = pts[i], d = pts[Math.min(i + 1, pts.length - 1)];
    const cap = { cx: c.x, cy: c.y, dx: d.x, dy: d.y, x0: Math.min(c.x, d.x) - radius, y0: Math.min(c.y, d.y) - radius,
      x1: Math.max(c.x, d.x) + radius, y1: Math.max(c.y, d.y) + radius };
    caps.push(cap);
    box[0] = Math.min(box[0], cap.x0); box[1] = Math.min(box[1], cap.y0);
    box[2] = Math.max(box[2], cap.x1); box[3] = Math.max(box[3], cap.y1);
  }
  return { caps, box, radius };
}

// Twice the signed area of (o, a, b): which side of the line o → a b is on.
const cross = (ox, oy, ax, ay, bx, by) => (ax - ox) * (by - oy) - (ay - oy) * (bx - ox);

// A point of 0..1 where the convex `depth` is inside (below −GRAZE): the
// deepest of the candidates when one is, else a golden-section search (a
// half-width that changes along the segment moves the deepest point off
// them); −1 when there is none.
function insideOf(depth, candidates) {
  let best = -1, low = -GRAZE;
  for (const u of candidates) {
    const v = depth(u);
    if (v < low) { low = v; best = u; }
  }
  if (best >= 0) return best;
  let lo = 0, hi = 1, x1 = 1 - PHI, x2 = PHI, f1 = depth(x1), f2 = depth(x2);
  for (let k = 0; k < 48; k++) {
    if (f1 < -GRAZE) return x1;
    if (f2 < -GRAZE) return x2;
    if (f1 <= f2) { hi = x2; x2 = x1; f2 = f1; x1 = hi - PHI * (hi - lo); f1 = depth(x1); }
    else { lo = x1; x1 = x2; f1 = f2; x2 = lo + PHI * (hi - lo); f2 = depth(x2); }
  }
  return -1;
}

// Where `depth` crosses 0 between `out` (outside) and `inside`, to EDGE pt
// along a segment `len` long: the outside end of the bracket, so a piece's
// new end lies just past the eraser's edge.
function edgeOf(depth, out, inside, len) {
  for (let k = 0; k < 64 && Math.abs(inside - out) * len > EDGE; k++) {
    const m = (out + inside) / 2;
    if (depth(m) < 0) inside = m; else out = m;
  }
  return out;
}

// The part of the segment a → b of a stroke (drawn half-widths ha, hb at
// its ends) under the capsule c of radius r: [u0, u1] in 0..1 along it,
// or null when it stays outside. With `any`, a point inside is enough
// ([u, u], no bisection).
function underCapsule(a, b, ha, hb, c, r, any) {
  const ex = b.x - a.x, ey = b.y - a.y;
  // the segments' distance against the wider half-width rules out most pairs
  const d1 = cross(c.cx, c.cy, c.dx, c.dy, a.x, a.y), d2 = cross(c.cx, c.cy, c.dx, c.dy, b.x, b.y);
  const d3 = cross(a.x, a.y, b.x, b.y, c.cx, c.cy), d4 = cross(a.x, a.y, b.x, b.y, c.dx, c.dy);
  const crossing = ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
  const near = crossing ? 0 : Math.min(segDist2(a.x, a.y, c.cx, c.cy, c.dx, c.dy), segDist2(b.x, b.y, c.cx, c.cy, c.dx, c.dy),
    segDist2(c.cx, c.cy, a.x, a.y, b.x, b.y), segDist2(c.dx, c.dy, a.x, a.y, b.x, b.y));
  if (Math.sqrt(near) - Math.max(ha, hb) - r >= -GRAZE) return null;
  const depth = (u) => Math.sqrt(segDist2(a.x + u * ex, a.y + u * ey, c.cx, c.cy, c.dx, c.dy)) - (ha + u * (hb - ha)) - r;
  // the nearest the two segments come: where they cross, or an end of one
  // against the other (in the plane, the nearest pair has an end in it)
  const l2 = ex * ex + ey * ey;
  const along = (px, py) => (l2 ? clamp(((px - a.x) * ex + (py - a.y) * ey) / l2, 0, 1) : 0);
  const candidates = [0, 1, along(c.cx, c.cy), along(c.dx, c.dy)];
  if (crossing) candidates.push(d1 / (d1 - d2));
  const u = insideOf(depth, candidates);
  if (u < 0) return null;
  if (any) return [u, u];
  const len = Math.sqrt(l2);
  return [depth(0) < 0 ? 0 : edgeOf(depth, 0, u, len), depth(1) < 0 ? 1 : edgeOf(depth, 1, u, len)];
}

// The parts of stroke `s` under a sweep, as sorted and merged [from, to]
// ranges along its samples (sample i at i, a fraction u on to the next at
// i + u), or null when none; a dot (one sample) is under it whole, [[0,
// 0]], or not at all. With `any`, the first part found is the answer.
function sweptRanges(s, sweep, any = false) {
  const b = strokeBounds(s), box = sweep.box;
  if (!b || b[2] < box[0] || b[0] > box[2] || b[3] < box[1] || b[1] > box[3]) return null;
  const caps = sweep.caps.filter((c) => b[2] >= c.x0 && b[0] <= c.x1 && b[3] >= c.y0 && b[1] <= c.y1);
  if (!caps.length) return null;
  const pts = decodeStroke(s), r = sweep.radius;
  if (pts.length === 1) {
    const q = pts[0], h = strokeWidth(s, q) / 2;
    return caps.some((c) => Math.sqrt(segDist2(q.x, q.y, c.cx, c.cy, c.dx, c.dy)) - h - r < -GRAZE) ? [[0, 0]] : null;
  }
  const ranges = [];
  for (let i = 0; i + 1 < pts.length; i++) {
    const a = pts[i], q = pts[i + 1];
    const ha = strokeWidth(s, a) / 2, hb = strokeWidth(s, q) / 2, h = Math.max(ha, hb);
    const x0 = Math.min(a.x, q.x) - h, x1 = Math.max(a.x, q.x) + h, y0 = Math.min(a.y, q.y) - h, y1 = Math.max(a.y, q.y) + h;
    for (const c of caps) {
      if (x1 < c.x0 || x0 > c.x1 || y1 < c.y0 || y0 > c.y1) continue;
      const part = underCapsule(a, q, ha, hb, c, r, any);
      if (!part) continue;
      if (any) return [[i + part[0], i + part[1]]];
      ranges.push([i + part[0], i + part[1]]);
    }
  }
  if (!ranges.length) return null;
  ranges.sort((p, q) => p[0] - q[0]);
  const out = [ranges[0]];
  for (let k = 1; k < ranges.length; k++) {
    const last = out[out.length - 1], [lo, hi] = ranges[k];
    if (lo <= last[1] + 1e-9) last[1] = Math.max(last[1], hi); else out.push([lo, hi]);
  }
  return out;
}

// The sample at v along a stroke's samples (sample i at i): a sample
// itself, or one lerped between two — x, y, p, t, w and o linear, tilt
// (a, z) and letters this build does not know from the nearer of the two.
function sampleAt(pts, v) {
  const i = Math.floor(v), u = v - i;
  if (i >= pts.length - 1) return pts[pts.length - 1];
  if (u <= 1e-9) return pts[i];
  if (u >= 1 - 1e-9) return pts[i + 1];
  return u < 0.5 ? lerpSample(pts[i], pts[i + 1], u) : lerpSample(pts[i + 1], pts[i], 1 - u);
}
// What the swept ranges leave of a stroke's samples: a run per stretch
// between them, each end where a range begins or ends.
function keptRuns(pts, ranges) {
  const runs = [];
  const take = (from, to) => {
    if (!(to > from)) return;
    const run = [sampleAt(pts, from)];
    for (let j = Math.floor(from + 1e-9) + 1; j < to - 1e-9; j++) run.push(pts[j]);
    run.push(sampleAt(pts, to));
    runs.push(run);
  };
  let from = 0;
  for (const [lo, hi] of ranges) { take(from, lo); from = Math.max(from, hi); }
  take(from, pts.length - 1);
  return runs;
}
// A piece that would draw as a blot, not a line: shorter than MIN_PIECE,
// or than half its drawn width at either end.
const speck = (s, run) => arcLengths(run)[run.length - 1]
  < Math.max(MIN_PIECE, strokeWidth(s, run[0]) / 2, strokeWidth(s, run[run.length - 1]) / 2);

// The partial eraser along `path` (page points; one point is a disk) with
// `radius`: every stroke under the sweep is cut where the sweep's edge
// crosses it, a sample lerped there (sampleAt), so each piece ends at the
// eraser's edge however far apart the stroke's samples or the path's
// points lie; a piece that is a speck goes, and so does a dot under the
// sweep. Each piece is a stroke of its own (restroke). → {ink, changed};
// a stroke `allow` (optional) refuses stays as it is, the same object, and
// a sweep that cuts nothing gives the same ink.
export function eraseAlong(ink, path, radius, allow) {
  const sweep = sweepOf(path, radius);
  let changed = false;
  const strokes = [];
  for (const s of ink?.strokes || []) {
    const ranges = sweep && (!allow || allow(s)) ? sweptRanges(s, sweep) : null;
    if (!ranges) { strokes.push(s); continue; }
    changed = true;
    for (const run of keptRuns(decodeStroke(s), ranges)) if (!speck(s, run)) strokes.push(restroke(s, run));
  }
  return { ink: changed ? { ...ink, strokes } : ink, changed };
}
// The partial eraser at one point: a disk of `radius` at (x, y).
export function eraseAt(ink, x, y, radius, allow) {
  return eraseAlong(ink, [{ x, y }], radius, allow);
}

// Ids of the strokes the sweep of `path` with `radius` touches, the
// whole-stroke eraser's test (a stroke the partial eraser would cut);
// `keep(stroke)` (optional) leaves the strokes it refuses out.
export function hitStrokesAlong(ink, path, radius, keep) {
  const sweep = sweepOf(path, radius);
  if (!sweep) return [];
  return (ink?.strokes || []).filter((s) => (!keep || keep(s)) && sweptRanges(s, sweep, true)).map((s) => s.id);
}
// The same at one point (x, y).
export function hitStrokes(ink, x, y, radius, keep) {
  return hitStrokesAlong(ink, [{ x, y }], radius, keep);
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

// A piece of a pressure stroke outlined on its own — a dash, a dotted
// pencil's dab, a pencil's shade: `first` / `last` whether its start / end
// is one of the stroke's own ends (round) or a cut between two pieces
// (flat, so neighbouring shades meet without overlapping; a dash is round at
// both). perfect-freehand has one absolute constant, the 3 units at the end
// of a path whose points it drops, so a piece shorter than 3 pt (a thin
// pen's dash, a short shade) would outline as a disc about its length
// across: a piece is outlined at RUN_SCALE times the page's scale. And a
// path of two points gets its in-between points at pressure 0.5, the
// nominal width, so a two-sample piece gets its midpoint first.
// gamma/ink.py stroke_outline outlines a piece the same way.
const RUN_SCALE = 10;
export function runOutline(samples, opts, first = true, last = true) {
  const run = samples.length === 2 ? [samples[0], lerpSample(samples[0], samples[1], 0.5), samples[1]] : samples;
  const k = RUN_SCALE;
  const pts = getStroke(run.map((s) => [s.x * k, s.y * k, outlinePressure(s)]),
    { ...opts, size: opts.size * k, start: { cap: first, taper: 0 }, end: { cap: last, taper: 0 } });
  return svgPathFromPoints(pts.map(([x, y]) => [x / k, y / k]));
}

// --- dashes and the pencil's shades ----------------------------------------
// A stroke's `dash` ('dashed' | 'dotted', solid when absent) is drawn from
// its nominal size, never stored as geometry: DASH holds the on and off
// lengths in sizes (gamma/ink.py DASH is the same table;
// tests/shared/inkdash.json pins both), with round caps everywhere, so a
// dotted constant-width stroke's dot is a round-capped dash a tenth of a
// size long. The pattern multiplies the size, never the drawn width, and
// its phase is 0 at the first sample of every stroke and every cut piece.
export const DASH = { dashed: { on: 2, off: 2 }, dotted: { on: 0.1, off: 2 } };
// [on, off] in points for a stroke (or a style, {dash, size}); null when solid.
export function dashPattern(stroke) {
  if (!isDash(stroke?.dash)) return null;
  const { on, off } = DASH[stroke.dash], size = stroke.size ?? DEFAULT_SIZE;
  return [on * size, off * size];
}
const num = (v) => String(Math.round(v * 1000) / 1000);
// A stroked line's stroke-dasharray ("4 4"; the live canvas hands
// dashPattern to setLineDash); null when solid.
export function dashArray(stroke) {
  const p = dashPattern(stroke);
  return p ? `${num(p[0])} ${num(p[1])}` : null;
}

// The arc length at each sample.
function arcLengths(samples) {
  const out = new Array(samples.length);
  let run = 0;
  for (let i = 0; i < samples.length; i++) {
    if (i) run += Math.hypot(samples[i].x - samples[i - 1].x, samples[i].y - samples[i - 1].y);
    out[i] = run;
  }
  return out;
}
// The sample a fraction `f` of the way from `a` to `b`: x, y, p, t, w and o
// linear, tilt (a, z) and anything else from `a`, the sample before.
const LERPED = ["x", "y", "p", "t", "w", "o"];
export function lerpSample(a, b, f) {
  const out = { ...a };
  for (const k of LERPED) if (a[k] != null && b[k] != null) out[k] = a[k] + (b[k] - a[k]) * f;
  return out;
}
// The samples between arcs `from` < `to`: a boundary between two samples
// lerped there, the samples strictly inside as they are. `at` is the
// segment to start looking from (the runs come in order, so a stroke is
// walked once) → [samples, the segment the run began in].
function arcSlice(samples, arc, from, to, at = 0) {
  const span = (j, v) => (arc[j + 1] > arc[j] ? (v - arc[j]) / (arc[j + 1] - arc[j]) : 0);
  let i = at;
  while (i < arc.length - 2 && arc[i + 1] <= from) i++;
  const run = [arc[i] === from ? samples[i] : lerpSample(samples[i], samples[i + 1], span(i, from))];
  let j = i + 1;
  for (; j < arc.length && arc[j] < to; j++) if (arc[j] > from) run.push(samples[j]);
  run.push(j < arc.length && arc[j] === to ? samples[j] : lerpSample(samples[j - 1], samples[j], span(j - 1, to)));
  return [run, i];
}
// A dashed pressure stroke's on-runs by arc length from phase 0 —
// [k(on + off), k(on + off) + on) clipped to the stroke's length — each the
// samples inside it with its boundaries lerped (a run of no length is
// skipped). A stroke without a pattern, or of no length (a dot), is one run.
export function dashRuns(samples, stroke) {
  const pattern = dashPattern(stroke);
  if (!pattern || samples.length < 2) return [samples];
  const [on, off] = pattern, arc = arcLengths(samples), length = arc[arc.length - 1];
  if (!(length > 0) || !(on > 0)) return [samples];
  const runs = [];
  let at = 0;
  for (let k = 0; k * (on + off) < length; k++) {
    const from = k * (on + off), to = Math.min(from + on, length);
    if (!(to > from)) continue;
    const [run, i] = arcSlice(samples, arc, from, to, at);
    runs.push(run);
    at = i;
  }
  return runs;
}
// A dotted pressure stroke's dots: one per on-run, at the run's middle arc
// (k(on + off) + on / 2, clipped to the length), as {x, y, w, o, arc}
// there; drawn as circles of diameter size × w. A stroke of no length is
// its first sample.
export function dashDots(samples, stroke) {
  const pattern = dashPattern(stroke);
  if (!pattern || !samples.length) return [];
  const [on, off] = pattern, arc = arcLengths(samples), length = arc[arc.length - 1];
  const dot = (s, at) => ({ x: s.x, y: s.y, w: s.w, o: s.o, arc: at });
  if (!(length > 0)) return [dot(samples[0], 0)];
  const out = [];
  let i = 0;
  for (let k = 0; k * (on + off) < length; k++) {
    const at = Math.min(k * (on + off) + on / 2, length);
    while (i < arc.length - 2 && arc[i + 1] < at) i++;
    const f = arc[i + 1] > arc[i] ? (at - arc[i]) / (arc[i + 1] - arc[i]) : 0;
    out.push(dot(lerpSample(samples[i], samples[i + 1], Math.min(1, f)), at));
  }
  return out;
}

// The pencil's shades on the page: a sample's opacity multiplier o drawn
// quantised up to one of three bands, each band one path (gamma/ink.py
// stroke_bands cuts the same way).
const BAND_EDGES = [0.33, 0.66];
export const OPACITY_BANDS = [0.33, 0.66, 1];
const bandIndex = (o) => (o <= BAND_EDGES[0] ? 0 : o <= BAND_EDGES[1] ? 1 : 2);
export const opacityBand = (o) => OPACITY_BANDS[bandIndex(o ?? 1)];
// The samples cut into runs of one band, [{band, samples}] in order: where
// o crosses a band's edge between two samples, a sample lerped there ends
// one run and starts the next. A run of no length (o meeting an edge right
// at a sample) is left out, as gamma/ink.py stroke_bands leaves it out: it
// would outline as a dot of its own shade; a stroke of no length at all is
// one run, in its last sample's band.
const runLength = (run) => run.some((s, i) => i && (s.x !== run[i - 1].x || s.y !== run[i - 1].y));
export function bandRuns(samples) {
  const runs = cutBands(samples);
  const kept = runs.filter((r) => runLength(r.samples));
  return kept.length || !runs.length ? kept : [{ band: runs.at(-1).band, samples }];
}
function cutBands(samples) {
  if (!samples.length) return [];
  const runs = [];
  let band = bandIndex(samples[0].o ?? 1), run = [samples[0]];
  for (let i = 1; i < samples.length; i++) {
    const a = samples[i - 1], b = samples[i], oa = a.o ?? 1, ob = b.o ?? 1, next = bandIndex(ob);
    while (band !== next) {
      const up = next > band, edge = BAND_EDGES[up ? band : band - 1];
      const cut = lerpSample(a, b, ob !== oa ? Math.max(0, Math.min(1, (edge - oa) / (ob - oa))) : 0);
      run.push(cut);
      runs.push({ band: OPACITY_BANDS[band], samples: run });
      band += up ? 1 : -1;
      run = [cut];
    }
    run.push(b);
  }
  runs.push({ band: OPACITY_BANDS[band], samples: run });
  return runs;
}

const polyline = (pts) => pts.map((s, i) => `${i ? "L" : "M"}${s.x.toFixed(2)},${s.y.toFixed(2)}`).join(" ")
  + (pts.length === 1 ? ` L${(pts[0].x + 0.01).toFixed(2)},${pts[0].y.toFixed(2)}` : "");

// --- the calligraphy nib -------------------------------------------------------
// The calligraphy pen on the screen (docs/dev/handwriting.md "Client"): a
// flat nib, a segment `size` long at the angle `nib` (its edge the page
// vector (cos nib, −sin nib)), swept along the samples (stabilised
// already: nothing smooths them again). The sweep of a segment along a run
// of samples whose travel stays on one side of the segment's direction is
// one polygon, the run moved by half the segment one way and back the
// other (their Minkowski sum: as wide across the travel as the segment's
// sine against it); where the travel crosses the segment's direction —
// where the band's width passes through zero, which every reversal does
// too — the run ends and the next begins with the segment itself between
// them, so a U-turn stays filled. The hairline along the nib's edge, the
// law's floor (NIB_HAIR, finishStroke.js BRUSHES), is the same sweep of a
// segment NIB_HAIR × size long across the nib, and the nib's footprint
// (the two segments' rectangle) stands at the first and the last sample,
// so a tap leaves the nib's mark and the ends show its corners. Every
// piece is wound one way (clockwise as seen), so the nonzero rule fills
// their union and an overlap never punches a hole. → one path `d`. The
// nib is `size` long at every pressure: the pressure's share of the law
// is in `w` alone, which the exporters draw.
const area2 = (poly) => {
  let a = 0;
  for (let i = 0; i < poly.length; i++) {
    const p = poly[i], q = poly[(i + 1) % poly.length];
    a += p[0] * q[1] - q[0] * p[1];
  }
  return a;
};
// A closed subpath of `poly` ([[x, y]]), wound clockwise as seen.
const woundPath = (poly) => {
  const pts = area2(poly) < 0 ? poly.slice().reverse() : poly;
  return "M" + pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" L") + " Z";
};
// The sweep of the segment between ±(vx, vy) along `pts`, as pieces into `out`.
function sweepSegment(pts, vx, vy, out) {
  let from = 0, side = 0;
  const piece = (a, b) => {
    if (!side) return;   // a run along the segment itself sweeps nothing: the other segment draws it
    const poly = [];
    for (let i = a; i <= b; i++) poly.push([pts[i].x + vx, pts[i].y + vy]);
    for (let i = b; i >= a; i--) poly.push([pts[i].x - vx, pts[i].y - vy]);
    out.push(woundPath(poly));
  };
  for (let i = 1; i < pts.length; i++) {
    const c = vx * (pts[i].y - pts[i - 1].y) - vy * (pts[i].x - pts[i - 1].x);
    const s = Math.abs(c) < 1e-9 ? 0 : Math.sign(c);
    if (s && side && s !== side) { piece(from, i - 1); from = i - 1; }
    if (s) side = s;
  }
  piece(from, pts.length - 1);
}
export function nibOutline(samples, size, nib = DEFAULT_NIB) {
  const pts = (samples || []).filter((s, i, all) => Number.isFinite(s?.x) && Number.isFinite(s?.y)
    && (!i || s.x !== all[i - 1].x || s.y !== all[i - 1].y));
  if (!pts.length) return "";
  const r = ((Number.isFinite(nib) ? nib : DEFAULT_NIB) * Math.PI) / 180, h = size / 2, g = (NIB_HAIR * size) / 2;
  const ex = Math.cos(r) * h, ey = -Math.sin(r) * h;   // half the nib, along its edge
  const nx = Math.sin(r) * g, ny = Math.cos(r) * g;    // half the hairline, across it
  const out = [];
  const stamp = (p) => out.push(woundPath([[p.x + ex + nx, p.y + ey + ny], [p.x + ex - nx, p.y + ey - ny],
    [p.x - ex - nx, p.y - ey - ny], [p.x - ex + nx, p.y - ey + ny]]));
  stamp(pts[0]);
  if (pts.length > 1) {
    sweepSegment(pts, ex, ey, out);
    sweepSegment(pts, nx, ny, out);
    stamp(pts[pts.length - 1]);
  }
  return out.join(" ");
}
const circle = (x, y, r) => {
  const R = r.toFixed(2);
  return `M${(x - r).toFixed(2)},${y.toFixed(2)} a${R},${R} 0 1,0 ${(2 * r).toFixed(2)},0 a${R},${R} 0 1,0 ${(-2 * r).toFixed(2)},0Z`;
};

// The SVG rendering of a stroke, never reading its colour (the outline
// cache keys every other key, docs/dev/handwriting.md "Caches"):
// - a pen of any brush with w: its filled outline, {d, stroke: false};
//   dashed, the outlines of its dash runs in one path; dotted, a circle
//   per dot;
// - with o (the pencil): {bands: [{d, alpha}], stroke: false}, a path per
//   opacity band, alpha the band (the renderer multiplies the opacity);
// - a constant-width pen (Ballpoint, a mouse or finger stroke): its
//   outline, or when dashed its centreline stroked with the pattern;
// - a highlighter, {d, stroke: true, width, multiply: true, dash}: its
//   polyline at its size, multiplied onto the page; an unknown tool the
//   same at its mean drawn width, source-over.
// `grain` marks a pencil, whose fill is the layer's grain pattern. A
// calligraphy pen without a shape record is its nib (nibOutline), its dash
// runs each a nib, its dots round dots of the drawn width; with a shape it
// is an even line, as every shape is. A filled closed shape (isClosedShape)
// also has `fill`, the closed polygon through its samples, which the layer
// fills in the stroke's fill under its outline.
function buildStrokePath(stroke) {
  const pts = decodeStroke(stroke);
  const out = buildOutline(stroke, pts);
  if (stroke.fill && isClosedShape(stroke) && pts.length >= 3) out.fill = polyline(pts) + " Z";
  return out;
}
function buildOutline(stroke, pts) {
  const tool = stroke.tool || "pen";
  const size = stroke.size ?? DEFAULT_SIZE;
  if (tool !== "pen") {
    const width = tool === "highlighter" || !pts.length ? size : pts.reduce((sum, s) => sum + strokeWidth(stroke, s), 0) / pts.length;
    return { d: polyline(pts), stroke: true, width, multiply: tool === "highlighter", dash: dashArray(stroke) };
  }
  const grain = stroke.brush === "pencil";
  const dash = dashPattern(stroke), dotted = !!dash && stroke.dash === "dotted";
  const pressure = pts[0]?.w != null, shaded = pts[0]?.o != null;
  const dotPath = (q) => circle(q.x, q.y, size * (q.w ?? 1) / 2);
  if (nibDrawn(stroke)) {
    const nib = Number.isFinite(stroke.nib) ? stroke.nib : DEFAULT_NIB;
    if (dotted) return { d: dashDots(pts, stroke).map(dotPath).join(" "), stroke: false, grain };
    const runs = dash ? dashRuns(pts, stroke) : [pts];
    return { d: runs.map((run) => nibOutline(run, size, nib)).join(" "), stroke: false, grain };
  }
  if (dash && !pressure && !shaded) return { d: polyline(pts), stroke: true, width: size, multiply: false, dash: dashArray(stroke), grain };
  const opts = outlineOptions(stroke);
  const dots = dotted ? dashDots(pts, stroke) : null;
  if (!shaded) {
    if (dots) return { d: dots.map(dotPath).join(" "), stroke: false, grain };
    // every dash outlined as a piece, the only one of a short stroke too
    // (runOutline: at the page's scale a dash under 3 pt is a blob)
    const d = dash ? dashRuns(pts, stroke).map((run) => runOutline(run, opts)).join(" ") : penOutline(pts, opts);
    return { d, stroke: false, grain };
  }
  const bands = new Map();
  const add = (band, d) => bands.set(band, bands.has(band) ? `${bands.get(band)} ${d}` : d);
  if (dots) for (const q of dots) add(opacityBand(q.o), dotPath(q));
  else {
    for (const run of dash ? dashRuns(pts, stroke) : [pts]) {
      const parts = bandRuns(run);
      parts.forEach((part, i) => add(part.band, runOutline(part.samples, opts, i === 0, i === parts.length - 1)));
    }
  }
  return { bands: OPACITY_BANDS.filter((b) => bands.has(b)).map((alpha) => ({ d: bands.get(alpha), alpha })), stroke: false, grain };
}

const paths = strokeCache("outline", outlineKey);
// The SVG rendering of a stroke (buildStrokePath above), cached.
export function strokePath(stroke) {
  const hit = paths.get(stroke);
  if (hit) return hit;
  const out = buildStrokePath(stroke);
  paths.set(stroke, out);
  return out;
}
