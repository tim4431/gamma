// Text boxes: typed text placed on a PDF page or a sheet of paper
// (docs/dev/text_boxes.md). The client half of gamma/text_box.py; pure, so
// it runs under node --test.
//
// A text box is a block whose `content` is its text, Markdown like any
// note. One property places it: `text_box: {x, y, w, h, auto, size, color,
// bg}` in points on its surface. A box under a sheet is on the nearest sheet
// above it, points from its top-left; any other box with `pdf_page` is on
// that PDF page, in the frame ink files use (the pdf.js viewport at scale 1,
// rotation applied, origin top-left, y down). `x`, `y` is the outer top-left
// corner and `w`, `h` the outer size, padding included; `h` is as the client
// last measured it. `auto` makes the width follow the text; otherwise the
// text wraps inside `w`. Stored values are read through normalizeTextBox,
// never trusted; tests/shared/textbox.json pins both halves.
import { isSheet, round2 } from "../notebook/notebook.js";

export const PAD = 4; // inner padding on every side, in points
export const LINE = 1.25; // line height, as a multiple of the font size
export const TEXT_SIZES = [9, 12, 16, 24, 36];
export const DEFAULT_COLOR = "#1f1f1f";
export const TEXT_BACKGROUNDS = [null, "#ffffff", "#fff4b8", "#dcecff"]; // none, white, note yellow, light blue
// The largest page side a PDF allows (ISO 32000-1, Annex C) bounds every
// coordinate and extent.
export const MAX_COORD = 14400;
export const MIN_WIDTH = 24, DEFAULT_WIDTH = 200;
export const MIN_SIZE = 6, MAX_SIZE = 96, DEFAULT_SIZE = 12;
const HEX = /^#[0-9a-f]{6}$/i;

const num = (v, lo, hi, fallback) => (typeof v === "number" && Number.isFinite(v)
  ? round2(Math.min(hi, Math.max(lo, v))) : fallback);
const color = (v, fallback) => (typeof v === "string" && HEX.test(v) ? v.toLowerCase() : fallback);

// A complete text box from a stored `text_box`, or null when it is not an
// object: each missing or bad key takes its default, numbers clamped and
// rounded to hundredths, colours lowercase, unknown keys dropped. `h`
// defaults to one line of the box's size.
export function normalizeTextBox(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const size = num(raw.size, MIN_SIZE, MAX_SIZE, DEFAULT_SIZE);
  return {
    x: num(raw.x, 0, MAX_COORD, 0),
    y: num(raw.y, 0, MAX_COORD, 0),
    w: num(raw.w, MIN_WIDTH, MAX_COORD, DEFAULT_WIDTH),
    h: num(raw.h, 0, MAX_COORD, round2(size * LINE + 2 * PAD)),
    auto: typeof raw.auto === "boolean" ? raw.auto : true,
    size,
    color: color(raw.color, DEFAULT_COLOR),
    bg: color(raw.bg, null),
  };
}

export const isTextBox = (block) => {
  const t = block?.properties?.text_box;
  return !!t && typeof t === "object" && !Array.isArray(t);
};

// The box a writer's change `base` → `mine` makes of the box `stored` now
// (gamma/text_box.py merge_text_box, where the server applies it to a set
// whose base_props names the box): the stored box with the keys the writer
// changed taken from `mine`, normalized, so a default spelled out is no
// change and where both changed a key the writer's value wins. `mine` as
// sent when it is no box (null removes it), normalized when `stored` or
// `base` is none. Every write sends the whole box (a keystroke stores the
// size it measured at), so taken as one value, someone typing would undo
// another's move. tests/shared/textboxmerge.json pins both halves.
export function mergeTextBox(stored, mine, base) {
  const ours = normalizeTextBox(mine);
  if (!ours) return mine;
  const now = normalizeTextBox(stored), was = normalizeTextBox(base);
  if (!now || !was) return ours;
  const out = { ...now };
  for (const k of Object.keys(ours)) if (ours[k] !== was[k]) out[k] = ours[k];
  return out;
}

// Map surface → its text-box blocks in document order, the tree's own
// objects (so an unchanged box keeps its identity): a sheet's id for a box
// under a sheet (the nearest one, as ink belongs to it), else the box's
// pdf_page. A box with neither is on no surface; a surface without boxes
// has no entry.
export function textBoxesBySurface(tree) {
  const out = new Map();
  const walk = (list, sheet) => {
    for (const b of list || []) {
      if (isSheet(b)) { walk(b.children, b.id); continue; }
      const page = b.properties?.pdf_page;
      const surface = sheet ?? (Number.isInteger(page) && page > 0 ? page : null);
      if (surface !== null && isTextBox(b)) {
        if (!out.has(surface)) out.set(surface, []);
        out.get(surface).push(b);
      }
      walk(b.children, sheet);
    }
  };
  walk(tree, null);
  return out;
}

// The style of new boxes ({size, color, bg}, the textBoxStyle preference),
// read the way a stored box is.
export function textStyle(raw) {
  const { size, color, bg } = normalizeTextBox(raw) || normalizeTextBox({});
  return { size, color, bg };
}

const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// The box a tap (`end` null) or a drag from `start` to `end` makes on a
// surface of `width` × `height` points, in `style`. A tap's box has auto
// width and puts the caret's first line under the point; a drag's spans the
// drag (at least MIN_WIDTH) with its first line at the drag's start, and
// keeps that width. Either is kept inside the surface.
export function placeBox({ width, height }, start, end, style) {
  const { size } = textStyle(style);
  const line = size * LINE + 2 * PAD;
  const w = Math.min(width, end ? Math.max(MIN_WIDTH, Math.abs(end.x - start.x)) : MIN_WIDTH);
  const x = end ? Math.min(start.x, end.x) : start.x - PAD;
  return normalizeTextBox({
    ...textStyle(style), x: clamp(x, 0, width - w), y: clamp(start.y - line / 2, 0, height - line), w, h: line, auto: !end,
  });
}

// `box` after it rendered at `w` × `h` points: its height, and for an auto
// box its width, up to the surface's right edge. The same object when both
// are within half a point of what it stores, so re-measuring an unchanged
// box writes nothing.
export function refitBox(box, { w, h }, surfaceWidth) {
  const next = normalizeTextBox({ ...box, h, w: box.auto ? Math.min(w, surfaceWidth - box.x) : box.w });
  return Math.abs(next.w - box.w) < 0.5 && Math.abs(next.h - box.h) < 0.5 ? box : next;
}

// `box` moved by `dx`, `dy` points, kept inside a surface of `width` ×
// `height`. `shown` is its size as drawn ({w, h} in points), which follows
// the text where the stored size may be stale.
export function moveBox(box, dx, dy, { width, height }, shown = box) {
  return normalizeTextBox({
    ...box, x: clamp(box.x + dx, 0, Math.max(0, width - shown.w)), y: clamp(box.y + dy, 0, Math.max(0, height - shown.h)),
  });
}

// `box` with the width `w` from its width handle: fixed from then on, at
// least MIN_WIDTH and no further than the surface's right edge.
export function resizeBox(box, w, { width }) {
  return normalizeTextBox({ ...box, w: clamp(w, MIN_WIDTH, Math.max(MIN_WIDTH, width - box.x)), auto: false });
}
