// A selection on a page that shows its translation (docs/dev/translation.md
// "Selecting translated text"), the page's marks that point at the original's
// words, and the block a paragraph's menu is for and what it says of it ("A
// paragraph's menu"). The translation is reflowed, so nothing of a selection in
// it maps to the original's words: a selection there stands for the whole
// blocks it touches, and a highlight made from it lies on their ORIGINAL lines
// with their original text, so it survives hiding the translation and exports
// as any other. Pure, so the tests run it in Node; pdf/PdfViewer.jsx reads the
// selection's ends off the page.
import { pageFlow } from "./pageFlow.js";
import { pageList } from "./pageLists.js";
import { NO_SPACE } from "./pdfTranslate.js";
import { PAD, drawnOver, translatable } from "./transBlock.js";
import { createPlanner } from "./translatePlan.js";
import { textParts } from "./translateReply.js";

// A page as the planner reads it (pdf/translatePlan.js): `order`, every block
// in reading order, a reference list the page holds by itself read as text, and
// `flow`, the body text along it, without the furniture (running heads,
// captions, a figure's labels). The overlay is drawn in this order, so a drag
// over its paragraphs selects them and copies them as they read, column by
// column.
export const pageReading = (paras) => pageFlow(paras, pageList(paras));

// The block whose lines hold the point (`x`, `y`), in pt of the page at scale
// 1, or else whose box does; -1 off every block. A line is taken a little
// wider than its glyphs, as the masks are.
export function blockAt(paras, x, y, pad = PAD) {
  const inside = (r) => x >= r.x1 - pad && x <= r.x2 + pad && y >= r.y1 - pad && y <= r.y2 + pad;
  const byLine = paras.findIndex((p) => (p.lines || []).some(inside));
  return byLine >= 0 ? byLine : paras.findIndex(inside);
}

// The blocks on page `pn` of each block's planner unit (pdf/translatePlan.js),
// by block index: a paragraph cut by a column break is one unit, translated as
// one and split back over its blocks, so a selection in either part stands for
// both. `parasOf(pn)` gives a page's segmented blocks, or null; the unit is
// planned again from them, as a job plans it with nothing done yet.
export function unitsOnPage(parasOf, pn) {
  const out = new Map();
  for (const unit of createPlanner({ paras: parasOf }).planPage(pn)) {
    const here = unit.targets.filter((t) => t.pn === pn).map((t) => t.i);
    for (const i of here) out.set(i, here);
  }
  return out;
}

// The blocks of a page's `entry` a selection from block `from` to block `to`
// covers, in reading order (pageReading, the overlay's DOM order): the two, the
// body text between them when both are in it, and every block of a translated
// block's unit on this page (`units`, from unitsOnPage). Furniture reads last,
// so between it and the body nothing is taken. Either end may be -1, off every
// block: the other one alone. An end in the original's text (a reference list
// left original, a figure's label) counts as its block, so a drag from it into
// a translation is one selection over both.
export function selectionBlocks(entry, from, to, units = new Map(), reading = pageReading(entry.paras)) {
  const { order, flow } = reading;
  const a = from >= 0 ? from : to, b = to >= 0 ? to : from;
  if (a < 0) return [];
  const body = new Set(flow);
  const [lo, hi] = [order.indexOf(a), order.indexOf(b)].sort((x, y) => x - y);
  const between = body.has(a) && body.has(b) ? order.slice(lo, hi + 1).filter((i) => body.has(i)) : [];
  const picked = new Set([a, b, ...between]);
  for (const i of [...picked]) {
    if (!drawnOver(entry, i)) continue;
    for (const j of units.get(i) || []) if (drawnOver(entry, j)) picked.add(j);
  }
  return order.filter((i) => picked.has(i));
}

// What a highlight over `blocks` of `paras` keeps: a rect per original line, in
// pt of the page at scale 1, and the quote, the blocks' original text.
export function blocksQuote(paras, blocks) {
  const rects = blocks.flatMap((i) => {
    const p = paras[i];
    const lines = p.lines?.length ? p.lines : [p];
    return lines.map((l) => ({ x1: l.x1, y1: l.y1, x2: l.x2, y2: l.y2 }));
  });
  return { rects, text: blocks.map((i) => paras[i].text.trim()).filter(Boolean).join("\n") };
}

// Whether block `p` has a paragraph menu: text the reader can have translated,
// a figure's included (transBlock translatable).
export const hasMenu = translatable;

// The block of a page's `entry` a press at (`x`, `y`), in pt at scale 1, asks
// a paragraph menu for: the block there, when it has one (hasMenu), else -1.
export function menuBlockAt(entry, x, y) {
  const i = blockAt(entry.paras, x, y);
  return i >= 0 && hasMenu(entry.paras[i]) ? i : -1;
}

// Two pieces of text read on as one: a space between them, none between kana
// or Han.
const joinText = (a, b) => (!a ? b : !b ? a : NO_SPACE.test(a[a.length - 1]) && NO_SPACE.test(b[0]) ? a + b : `${a} ${b}`);

// What a paragraph's menu says of the paragraph whose unit is `unit` ([{pn,
// i}], translateState.unitOf): `entryOf(pn)` gives a page's entry under the
// viewer's key, or null; `spanOf(pn, i, k)` a token's span (translateState
// span); `parasOf(pn)` a page's segmented blocks, or null (translateState
// blocks), the entry's when not given. A joined paragraph is one: `translated`,
// some block of it holds a translation; `original`, the reader shows some
// block of it in the original; `translation`, its text as the overlay draws
// it, a formula as its extracted text; `source`, its original text, a block a
// line, every part of the unit, those on a page with no entry under the key
// included.
export function paragraphMenu(unit, entryOf, spanOf = null, parasOf = null) {
  const blocks = unit.map((t) => ({ t, e: entryOf(t.pn) })).filter(({ t, e }) => e?.paras[t.i]);
  const text = ({ t, e }) => e.texts[t.i];
  const plain = ({ t, e }) => textParts(text({ t, e }), { spans: e.paras[t.i].spans, page: t.pn, spanOf })
    .map((part) => (typeof part === "string" ? part : part.span.str)).join("").trim();
  const block = (t) => (parasOf?.(t.pn) || entryOf(t.pn)?.paras)?.[t.i];
  return {
    translated: blocks.some((b) => typeof text(b) === "string" && !!text(b)),
    original: blocks.some(({ t, e }) => !!e.original?.has(t.i)),
    translation: blocks.filter((b) => typeof text(b) === "string").map(plain).reduce(joinText, ""),
    source: unit.map((t) => block(t)?.text.trim()).filter(Boolean).join("\n"),
  };
}

// A page's link boxes (`links`, {left, top, w, h} at scale 1) less those over
// the `blocks` the translation is drawn on: their words have moved, and a box
// over reflowed text would take a click or a drag's start meant for it.
export function linksOff(links, paras, blocks) {
  if (!blocks.length) return links;
  const over = new Set(blocks);
  return links.filter((l) => !over.has(blockAt(paras, l.left + l.w / 2, l.top + l.h / 2, 0)));
}

// Whether a point (`x`, `y`, pt at scale 1) lies on one of the `blocks` of
// `paras` the translation is drawn on. A highlight's rect there takes no
// pointer (PdfViewer): it is the original's place, and as a link box would
// (linksOff), it would take the right-click, the drag's start and the press
// meant for the translated text. Its rows are in the paragraph's menu
// (highlightOn).
export function onBlocks(paras, blocks) {
  const over = new Set(blocks);
  return (x, y) => over.size > 0 && over.has(blockAt(paras, x, y, 0));
}

// A highlight's rects in pt at scale 1, from its position's rects (px at its
// `width` and `height`), on a page of `size` ({width, height} in pt).
function highlightRects(h, size) {
  const kx = size.width / (h.position?.width || 1), ky = size.height / (h.position?.height || 1);
  return (h.position?.rects || []).map((r) => ({ x1: r.x1 * kx, y1: r.y1 * ky, x2: r.x2 * kx, y2: r.y2 * ky }));
}

// The highlight whose rows a paragraph's menu for block `i` of `paras` carries:
// of a page's `highlights` ({id, position}), on a page of `size`, the one under
// the point (`x`, `y`, pt at scale 1; null when there is none, the popup's ⋯),
// else the first with a rect on the block; its id, or null.
export function highlightOn(highlights, paras, i, x, y, size) {
  const rects = highlights.map((h) => [h, highlightRects(h, size)]);
  const inside = (r) => x != null && x >= r.x1 && x <= r.x2 && y >= r.y1 && y <= r.y2;
  const on = (r) => blockAt(paras, (r.x1 + r.x2) / 2, (r.y1 + r.y2) / 2, 0) === i;
  const found = rects.find(([, rs]) => rs.some((r) => inside(r) && on(r))) || rects.find(([, rs]) => rs.some(on));
  return found ? found[0].id : null;
}

// Of a page's `highlights`, on a page of `size`, one that lies on exactly
// `rects` (pt at scale 1, a selection's original lines, blocksQuote) within
// `tol` pt; its id, or null. A selection in a translation stands for whole
// blocks, so a selection there again stands for the highlight made from the
// first.
export function sameHighlight(highlights, rects, size, tol = 1) {
  const near = (a, b) => Math.abs(a.x1 - b.x1) <= tol && Math.abs(a.y1 - b.y1) <= tol
    && Math.abs(a.x2 - b.x2) <= tol && Math.abs(a.y2 - b.y2) <= tol;
  const found = highlights.find((h) => {
    if (h.position?.area) return false;
    const own = highlightRects(h, size);
    return own.length === rects.length && rects.every((r) => own.some((q) => near(q, r)));
  });
  return found ? found.id : null;
}

// A page's find marks (`marks`, {rect: {x1, y1, x2, y2} at scale 1, active})
// with those that fall in a block the translation is drawn on as one mark of
// that block, `block: true`, active if any of them is: the search reads the
// original, whose words are not where the translation puts its own, so the
// mark says which paragraph holds the match.
export function marksOver(marks, paras, blocks) {
  if (!blocks.length || !marks?.length) return marks;
  const over = new Set(blocks);
  const out = [];
  const byBlock = new Map();
  for (const m of marks) {
    const i = blockAt(paras, (m.rect.x1 + m.rect.x2) / 2, (m.rect.y1 + m.rect.y2) / 2, 0);
    if (!over.has(i)) { out.push(m); continue; }
    const had = byBlock.get(i);
    if (had) { had.active = had.active || !!m.active; continue; }
    const p = paras[i];
    const mark = { rect: { x1: p.x1, y1: p.y1, x2: p.x2, y2: p.y2 }, active: !!m.active, block: true };
    byBlock.set(i, mark);
    out.push(mark);
  }
  return out;
}
