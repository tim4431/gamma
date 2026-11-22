// Redraw as shapes and Convert to text, the client's pure half
// (docs/dev/handwriting_ai.md): what a request sends and how each stroke of
// it is found again, and the edits an answer makes — the shape strokes that
// take the place of the strokes the AI redrew, and the text box that takes
// the place of the handwriting it read. The answer comes seconds later, so
// every plan is built when it is applied, from the groups as they are then
// (`inkOf(id)`, useInkDocument.js: a group's strokes now, null for one it
// does not know), and leaves alone what changed meanwhile. The hook
// (useInkSelection.js) sends, waits and says what happened; nothing here
// speaks to the user. frontend/tests/inkAi.test.mjs pins it.
import { DEFAULT_COLOR, LINE, PAD, normalizeTextBox } from "../markup/textBox.js";
import { findBlock } from "../shared/model/blockModel.js";
import { INK_COLORS, hexColor } from "../shared/model/palette.js";
import { CLOSED_SHAPES, DEFAULT_SIZE, MAX_BYTES, MAX_SAMPLES, MAX_STROKES, clamp, decodeStroke, encodeStroke, isDash,
  removeStrokes, round2, sizesFor, strokeBounds, strokeId, unionBox } from "./ink.js";
import { shapeSamples } from "./shapes.js";

// The most pen strokes one redraw takes (gamma/routers/ink_ai.py, whose
// 413 says the same): the client refuses a larger selection before asking.
export const MAX_REDRAW_STROKES = 300;

const isPen = (s) => !s?.tool || s.tool === "pen";
// The strokes a pen drew (a stroke without a tool is one), never a
// highlighter's: what the AI is offered of a selection.
export const penOnly = (strokes) => (strokes || []).filter(isPen);

// The request for the selection `sel` ({page, items: [{id, ids}]}) →
// {page, space, strokes, refs, box}, or null when it holds no pen stroke:
// - `strokes`, the selected strokes as the groups hold them, in the order
//   selectedStrokes (selectionEdits.js) gives them — each group's in its
//   own order, the groups in the selection's — of every tool (the server
//   leaves the highlighters out); with `space`, the first such group's, the
//   body the routes take ({space, strokes}). A stroke id a group
//   before it already used goes out under a fresh one, since the server
//   reads the strokes as one file, where an id is unique; the answer names
//   strokes by index, never by id.
// - `refs[i]`, {group, id, pts}: where request stroke i lives, by its own
//   id, and its `pts` array as sent, which redrawPlan compares with the
//   stroke it finds there then.
// - `box`, the pen strokes' union box (strokeBounds, width included), in
//   the surface's points: where a text box goes.
export function inkAiRequest(sel, inkOf) {
  if (!sel) return null;
  const strokes = [], refs = [], used = new Set();
  let space = null, box = null;
  for (const item of sel.items || []) {
    const ink = inkOf(item.id);
    if (!ink) continue;
    const want = new Set(item.ids);
    const mine = ink.strokes.filter((s) => want.has(s.id));
    if (!mine.length) continue;
    space = space || ink.space;
    for (const s of mine) {
      refs.push({ group: item.id, id: s.id, pts: s.pts });
      const id = used.has(s.id) ? freshId(used, strokeId) : s.id;
      used.add(id);
      strokes.push(id === s.id ? s : { ...s, id });
      if (isPen(s)) box = unionBox(box, strokeBounds(s));
    }
  }
  return box ? { page: sel.page, space, strokes, refs, box } : null;
}

// What the request's file wrapper adds to its strokes' bytes ({"format": …,
// "space": …, "strokes": […]}), with room to spare.
const WRAPPER_BYTES = 4096;

// Whether a request is more than the server reads as one file (gamma/ink.py:
// MAX_STROKES strokes, MAX_SAMPLES samples, MAX_BYTES bytes), which it
// parses the pen strokes into. Each group is held to those caps alone, so
// a selection across two or three can pass them; the hook refuses it
// before anything is sent, as the server would with a 413. The pen strokes
// alone count: the server drops the highlighters before it parses
// (routers/ink_ai.py _pen_ink).
export function requestTooBig(request) {
  const strokes = penOnly(request?.strokes || []);
  if (strokes.length > MAX_STROKES) return true;
  let samples = 0;
  for (const s of strokes) samples += Math.floor((s.pts?.length || 0) / (s.ch || "xy").length);
  if (samples > MAX_SAMPLES) return true;
  return new TextEncoder().encode(JSON.stringify(strokes)).length > MAX_BYTES - WRAPPER_BYTES;
}

// The groups as `inkOf` has them, but a group whose block has left the
// page's `tree` (deleted while the AI worked, with its sheet or alone) is
// gone: inkOf still answers with the draft such a group left behind, and
// an edit of it would put its block back (useInkDocument.js applyInk). The
// reader an answer's plan is built with.
export const inkInTree = (inkOf, tree) => (id) => (findBlock(tree || [], id) ? inkOf(id) : null);

// An id `make()` gives that is not in `used`, else one strokeId gives (so
// a `make` that keeps answering a taken id cannot stall it).
function freshId(used, make) {
  const id = make();
  if (id && !used.has(id)) return id;
  let fresh;
  do { fresh = strokeId(); } while (used.has(fresh));
  return fresh;
}

// A params object a shape stroke can carry: numbers that are finite, and
// the booleans a curve's flags are (docs/dev/handwriting.md "The stroke
// file").
const goodParams = (params) => !!params && typeof params === "object" && !Array.isArray(params)
  && Object.values(params).every((v) => typeof v === "boolean" || (typeof v === "number" && Number.isFinite(v)));

// A shape stroke from its record, as the Shapes tool commits one
// (InkLayer.jsx commitShape → finishStroke's hold stage): a pen at
// constant width, `pen: false` with x, y and t alone (`ch: "xyt"`), the
// monoline brush, the record `{kind, params}` and its samples
// (shapes.js shapeSamples) spread over `span` ms from `t0`. `look` gives
// its colour, size, opacity and line (dash); its `fill` ({color,
// opacity?}) stays only on a kind with an inside (ink.js CLOSED_SHAPES). A
// circle is written as the ellipse the file stores. `id`, else a fresh
// one. null for params that are no shape's, or a record that draws nothing.
export function shapeStroke(kind, params, look = {}, { t0 = null, span = 0, id } = {}) {
  if (!goodParams(params)) return null;
  const record = kind === "circle"
    ? { kind: "ellipse", params: { cx: params.cx, cy: params.cy, rx: params.r, ry: params.r, angle: 0 } }
    : { kind, params: { ...params } };
  const samples = shapeSamples(record.kind, record.params, { span: Math.max(0, span || 0) });
  if (!samples.length || !samples.every((q) => Number.isFinite(q.x) && Number.isFinite(q.y))) return null;
  return encodeStroke({
    id, samples, ch: "xyt", tool: "pen", brush: "monoline", pen: false,
    color: look.color || INK_COLORS[0], size: look.size ?? DEFAULT_SIZE, opacity: look.opacity ?? 1, t0,
    dash: isDash(look.dash) ? look.dash : null,
    fill: look.fill && CLOSED_SHAPES.has(record.kind) ? { ...look.fill } : null,
    shape: record,
  });
}

const samePts = (a, b) => a === b || (Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((v, i) => v === b[i]));

// The look a redrawn shape takes from the stroke it replaces first: its
// colour, opacity and line, its size held to a pen's range (ink.js
// sizesFor), its fill (closed kinds keep it, shapeStroke), and its timing,
// so a replay draws the shape when the stroke was drawn: from the stroke's
// first sample over its duration.
function lookOf(stroke) {
  const sizes = sizesFor("pen"), samples = decodeStroke(stroke);
  const first = samples[0]?.t ?? 0, last = samples.at(-1)?.t ?? first;
  return {
    look: { color: stroke.color, size: round2(clamp(stroke.size ?? DEFAULT_SIZE, sizes[0], sizes.at(-1))), opacity: stroke.opacity,
      dash: stroke.dash, fill: stroke.fill },
    t0: stroke.t0 != null ? stroke.t0 + first : null,
    span: Math.max(0, last - first),
  };
}

// Redraw as shapes, applied: `shapes`, the answer's [{kind, params,
// strokes: [request indices]}] (params in the strokes' own page points),
// as edits of the groups now → {changes, items, count, skipped}, or
// {refused: true} when a group would pass MAX_STROKES.
// - A shape replaces its strokes (`request.refs`, in request order) only
//   while every one of them is still in its group with the samples it was
//   sent with (the same `pts`, or the same numbers in it); else it is left
//   out and counted in `skipped`. An index that names no pen stroke of the
//   request, or one an earlier shape took, is dropped from it; a shape left
//   with none, or whose record draws nothing (shapeStroke), is left out
//   uncounted.
// - Its stroke takes the look and timing of the first stroke it replaces
//   (lookOf), an id `makeStrokeId()` gives that its group has not held
//   (else a strokeId one), and that stroke's place in its group; the other
//   strokes it replaces leave their groups, whichever they are.
// - `changes`, [{id, page, before, after}] per group changed, for one
//   applyInk step; `items`, [{id, ids}] the new strokes by group, to
//   select; `count` the shapes drawn.
export function redrawPlan(request, shapes, inkOf, makeStrokeId = strokeId) {
  const work = new Map();   // group → {before, strokes, used}: the group as the plan leaves it
  const groupOf = (id) => {
    if (!work.has(id)) {
      const before = inkOf(id);
      work.set(id, before ? { before, strokes: [...before.strokes], used: new Set(before.strokes.map((s) => s.id)), changed: false } : null);
    }
    return work.get(id);
  };
  const taken = new Set(), added = new Map();
  let count = 0, skipped = 0;
  for (const shape of shapes || []) {
    const indices = [...new Set((shape?.strokes || []).filter((i) => Number.isInteger(i)))].sort((a, b) => a - b)
      .filter((i) => request.refs[i] && isPen(request.strokes[i]) && !taken.has(i));
    if (!indices.length) continue;
    const found = indices.map((i) => {
      const ref = request.refs[i], g = groupOf(ref.group);
      const s = g?.before.strokes.find((q) => q.id === ref.id);
      return s && isPen(s) && samePts(s.pts, ref.pts) ? { ref, s } : null;
    });
    if (found.some((f) => !f)) { skipped++; continue; }
    const { look, t0, span } = lookOf(found[0].s);
    const home = groupOf(found[0].ref.group);
    const made = shapeStroke(shape.kind, shape.params, look, { t0, span, id: freshId(home.used, makeStrokeId) });
    if (!made) continue;
    home.used.add(made.id);
    for (const [k, { ref }] of found.entries()) {
      const g = groupOf(ref.group), at = g.strokes.findIndex((q) => q.id === ref.id);
      if (k === 0) g.strokes.splice(at, 1, made); else g.strokes.splice(at, 1);
      g.changed = true;
    }
    for (const i of indices) taken.add(i);
    added.set(found[0].ref.group, [...(added.get(found[0].ref.group) || []), made.id]);
    count++;
  }
  const changes = [];
  for (const [id, g] of work) {
    if (!g?.changed) continue;
    if (g.strokes.length > MAX_STROKES) return { refused: true };
    changes.push({ id, page: request.page, before: g.before, after: { ...g.before, strokes: g.strokes } });
  }
  return { changes, items: [...added].map(([id, ids]) => ({ id, ids })), count, skipped };
}

// The pen colour drawn longest among `strokes` (by their polylines' total
// length per colour), as `#rrggbb` (an rgb() or rgba() one converted,
// palette.js hexColor); null with none.
function inkColour(strokes) {
  const lengths = new Map();
  for (const s of strokes) {
    const hex = hexColor(s.color || INK_COLORS[0]);
    if (!hex) continue;
    const pts = decodeStroke(s);
    let len = 0;
    for (let i = 1; i < pts.length; i++) len += Math.hypot(pts[i].x - pts[i - 1].x, pts[i].y - pts[i - 1].y);
    lengths.set(hex, (lengths.get(hex) || 0) + len);
  }
  let best = null;
  for (const [hex, len] of lengths) if (!best || len > best.len) best = { hex, len };
  return best?.hex || null;
}

// The lines of a transcription that take a row in the box: every one with
// writing on it, but a bare `$$` (the fence of a displayed formula).
const textLines = (text) => String(text || "").split("\n").map((l) => l.trim()).filter((l) => l && l !== "$$").length;

// Convert to text's box (markup/textBox.js, through normalizeTextBox) for
// `text` in the place of the handwriting `at` replaces ({box, strokes}:
// textPlan's, the strokes it removes as they are now, or the request's):
// its text where that handwriting is (the box's padding PAD outside the
// strokes' box), its font size the handwriting's line height (the box's
// height over its lines, a row being LINE × the size; clamped to 6..96
// pt), as wide as the handwriting and as tall as its lines, its width
// following the text (`auto`), in the colour the handwriting was mostly
// written in, no fill. null without a box.
export function textBoxFor(at, text) {
  const box = at?.box;
  if (!box) return null;
  const [x0, y0, x1, y1] = box, lines = Math.max(1, textLines(text));
  const size = clamp(Math.round((y1 - y0) / lines / LINE), 6, 96);
  return normalizeTextBox({
    x: x0 - PAD, y: y0 - PAD, w: x1 - x0 + 2 * PAD, h: lines * size * LINE + 2 * PAD, auto: true, size,
    color: inkColour(penOnly(at.strokes)) || DEFAULT_COLOR, bg: null,
  });
}

// Convert to text, applied: the request's pen strokes removed where they
// are still in their groups as they were sent — a pen with the samples it
// was sent with, redrawPlan's test, so handwriting moved, resized, turned
// or flipped meanwhile (which keep its ids) stays where it is now → {changes,
// removed, skipped, box, strokes}:
// - `changes` as redrawPlan's; `removed` the strokes they take (0: nothing
//   to do).
// - `skipped` the strokes left because they changed: still in their group
//   by id, but no longer as sent. One erased away (an eraser's cut leaves
//   pieces under new ids), or whose group is gone, is neither.
// - `box` and `strokes`: the removed strokes' union box (strokeBounds) and
//   the strokes themselves as they are now, which textBoxFor puts the box
//   on, so it sits on the handwriting it replaces and is sized by it; null
//   and [] when none is.
export function textPlan(request, inkOf) {
  const byGroup = new Map();
  request.refs.forEach((ref, i) => {
    if (!isPen(request.strokes[i])) return;
    if (!byGroup.has(ref.group)) byGroup.set(ref.group, []);
    byGroup.get(ref.group).push(ref);
  });
  const changes = [], strokes = [];
  let skipped = 0, box = null;
  for (const [id, refs] of byGroup) {
    const before = inkOf(id);
    if (!before) continue;
    const byId = new Map(before.strokes.map((s) => [s.id, s]));
    const ids = [];
    for (const ref of refs) {
      const s = byId.get(ref.id);
      if (!s) continue;
      if (!isPen(s) || !samePts(s.pts, ref.pts)) { skipped++; continue; }
      ids.push(s.id);
      strokes.push(s);
      box = unionBox(box, strokeBounds(s));
    }
    if (ids.length) changes.push({ id, page: request.page, before, after: removeStrokes(before, ids) });
  }
  return { changes, removed: strokes.length, skipped, box, strokes };
}
