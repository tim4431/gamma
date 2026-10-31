// Text boxes: typed text placed on a PDF page or a sheet of paper
// (docs/dev/text_boxes.md). The client half of gamma/text_box.py; pure, so
// it runs under node --test.
//
// A text box is a block whose `content` is its text, Markdown like any
// note. One property places it: `text_box: {x, y, w, h, auto, size, color,
// bg}` (and `zotero`, below) in points on its surface. A box under a sheet
// is on the nearest sheet above it, points from its top-left; any other box
// with `pdf_page` is on that PDF page, in the frame ink files use (the pdf.js
// viewport at scale 1, rotation applied, origin top-left, y down). `x`, `y`
// is the outer top-left corner and `w`, `h` the outer size, padding
// included; `h` is as the client last measured it. `auto` makes the width
// follow the text; otherwise the text wraps inside `w`. `zotero: true`
// (stored only when true) makes a Zotero box, laid out as Zotero's reader
// lays out a text annotation: no padding, so `x`, `y`, `w`, `h` are the
// text area and equal Zotero's rect, in the reader's font stack at its
// normal line height (markup.css), showing the text Zotero is sent
// (shownText); an auto one stores its measured width plus ZOTERO_SLACK,
// the width Zotero wraps it at. Stored values are read
// through normalizeTextBox, never trusted; tests/shared/textbox.json and
// tests/shared/zoterotext.json pin both halves.
import { protectedSpans } from "../editor/mdScan.js";
import { isSheet, round2 } from "../notebook/notebook.js";
import { mapOutsideCodeFences } from "../shared/lib/diagramMarkdown.js";

export const PAD = 4; // inner padding on every side, in points
export const LINE = 1.25; // line height, as a multiple of the font size
// A Zotero box takes up to ZOTERO_LINE em a line on screen (Segoe UI at
// line-height normal in whole-pixel line boxes), and an auto one is sent
// ZOTERO_SLACK points wider than its text, as Zotero sizes one.
export const ZOTERO_LINE = 1.4;
export const ZOTERO_SLACK = 1;
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
// rounded to hundredths, colours lowercase, unknown keys dropped, `zotero`
// kept only when it is true. `h` defaults to one line of the box's size.
export function normalizeTextBox(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const size = num(raw.size, MIN_SIZE, MAX_SIZE, DEFAULT_SIZE);
  const box = {
    x: num(raw.x, 0, MAX_COORD, 0),
    y: num(raw.y, 0, MAX_COORD, 0),
    w: num(raw.w, MIN_WIDTH, MAX_COORD, DEFAULT_WIDTH),
    h: num(raw.h, 0, MAX_COORD, round2(size * LINE + 2 * PAD)),
    auto: typeof raw.auto === "boolean" ? raw.auto : true,
    size,
    color: color(raw.color, DEFAULT_COLOR),
    bg: color(raw.bg, null),
  };
  if (raw.zotero === true) box.zotero = true;
  return box;
}

// The box's inner padding on every side: none for a Zotero box.
export const padOf = (box) => (box?.zotero === true ? 0 : PAD);

// A blank line as gamma/pdf_document.py's chunks reads one: a line of
// only the characters Python's str.strip() takes (a no-break or full-width
// space too, which Markdown would show as text).
const BLANK = /^[\t-\r\x1c-\x20\x85\xa0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]*$/;
// Where remark-gfm would make a bare URL a link: its scheme or www. Not in
// a raw HTML tag, a link's destination or a link reference definition,
// where the URL is the markup's. URL_LABEL is a link labelled with a URL,
// which the renderer shows as it shows a bare one.
const BARE_LINK = /(https?)(?=:\/\/)|(www)(?=\.)/gi;
const LINK_MARKUP = /<\/?[A-Za-z][\w-]*(?:\s[^<>]*)?\/?>|\]\([^)\n]*\)|^ {0,3}\[[^\]\n]+\]:.*$/gm;
const URL_LABEL = /(?<!!)\[((?:https?:\/\/|www\.)[^\]\s]*)\]\([^)\n]*\)/gi;

// `text` with each match of `re` outside code and math (and `markup`'s
// matches) replaced by `fn(match, ...groups)`.
function outside(text, re, markup, fn) {
  const spans = [...protectedSpans(text), ...(markup ? Array.from(text.matchAll(markup),
    (m) => ({ from: m.index, to: m.index + m[0].length })) : [])];
  return text.replace(re, (...m) => (spans.some((s) => m.at(-2) >= s.from && m.at(-2) < s.to) ? m[0] : fn(...m)));
}

// `prose` with each bare URL outside code, math and markup split by an
// empty <span>, which the renderer shows as nothing, so it stays text
// rather than becoming a link chip, and a link labelled with a URL its
// label.
function unlinked(prose) {
  return outside(outside(prose, URL_LABEL, null, (_link, label) => label), BARE_LINK, LINK_MARKUP,
    (word) => `${word}<span></span>`);
}

// `prose` (whole lines) with a paragraph holding a no-break space before
// each run of blank lines outside display math, and each blank line empty.
function blankLines(prose) {
  if (!prose) return prose;
  const tail = prose.endsWith("\n");
  const lines = (tail ? prose.slice(0, -1) : prose).split("\n"), out = [];
  for (let i = 0, blank = false; i < lines.length;) {
    const line = lines[i++], s = line.trim(), rest = s.slice(2);
    if (s.startsWith("$$") && !(rest.endsWith("$$") && rest.slice(0, -2).trim())) {
      out.push(line);
      while (i < lines.length && !lines[i].includes("$$")) out.push(lines[i++]);
      if (i < lines.length) out.push(lines[i++]);
      blank = false;
      continue;
    }
    const empty = BLANK.test(line);
    if (empty && !blank) out.push("", "\u00a0");
    out.push(empty ? "" : line);
    blank = empty;
  }
  return out.join("\n") + (tail ? "\n" : "");
}

// The Markdown read mode renders for `box`'s text `md`, so that a Zotero
// box shows what Zotero shows of the text gamma/text_box.py plain_text
// sends it, laid out as that module lays the box out. Its blank lines at
// either end are dropped, as plain_text drops them. Each blank line
// between takes a line, outside fenced code (``` or ~~~, as the renderer
// reads them) and display math: the renderer already shows every blank
// line of a run but its first, which is the paragraph break
// (editor/mdMarks.js expandBlankLines), so a paragraph holding a no-break
// space goes before each run, the run's first line. A bare URL stays its
// text (unlinked), and a link's label, a [[ref]] or a citation reads as
// its text (markup.css), as plain_text gives them. Any other box's text
// renders as it is. The stored text is never changed.
export function shownText(md, box) {
  if (box?.zotero !== true || !md) return md;
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  let a = 0, b = lines.length;
  while (a < b && BLANK.test(lines[a])) a++;
  while (b > a && BLANK.test(lines[b - 1])) b--;
  return mapOutsideCodeFences(lines.slice(a, b).join("\n"), (prose) => blankLines(unlinked(prose)));
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
// keeps that width. Either is kept inside the surface. `zotero` makes a
// Zotero box: no padding, so a tap's text starts at the point, and a first
// line ZOTERO_LINE high.
export function placeBox({ width, height }, start, end, style, { zotero = false } = {}) {
  const { size } = textStyle(style);
  const pad = zotero ? 0 : PAD;
  const line = zotero ? size * ZOTERO_LINE : size * LINE + 2 * PAD;
  const w = Math.min(width, end ? Math.max(MIN_WIDTH, Math.abs(end.x - start.x)) : MIN_WIDTH);
  const x = end ? Math.min(start.x, end.x) : start.x - pad;
  return normalizeTextBox({
    ...textStyle(style), x: clamp(x, 0, width - w), y: clamp(start.y - line / 2, 0, height - line), w, h: line, auto: !end,
    zotero,
  });
}

// `box` after it rendered at `w` × `h` points: its height, and for an auto
// box its width (a Zotero box's ZOTERO_SLACK wider, the width Zotero wraps
// it at), up to the surface's right edge. The same object when both are
// within half a point of what it stores, so re-measuring an unchanged box
// writes nothing.
export function refitBox(box, { w, h }, surfaceWidth) {
  const fit = w + (box.zotero === true ? ZOTERO_SLACK : 0);
  const next = normalizeTextBox({ ...box, h, w: box.auto ? Math.min(fit, surfaceWidth - box.x) : box.w });
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
