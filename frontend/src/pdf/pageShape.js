// How a page's blocks sit in their columns, for the PDF translated view: a
// block centred in its column is set centred, a one-line block takes the room
// beside it, and a block's text stops where the next one's starts inside its
// box. Pure, like pdf/pageFlow.js, whose columns it reads. It writes
// on the blocks, so it runs where a page is segmented, as pdf/pageFigures.js
// markFigures does: the overlay (pdf/transBlock.js) and the export
// (pdf/translateExport.js) both read a page's blocks, so they read the same
// box. docs/dev/translation.md "Centred blocks and room" describes it.
import { pageFlow } from "./pageFlow.js";
import { PAD } from "./transBlock.js";

// A centred block stands this many of its em clear of both edges of its column,
// within this much of its middle, or of a share of the column's width if that
// is more: a line set by hand is a little off.
const CLEAR = 1;
const OFF_MIDDLE = 0.25;
const OFF_MIDDLE_WIDTH = 0.01;
// A block of lines is centred only if they differ this much in width, of its
// em: lines of one width are a justified block set in from both edges, as an
// abstract is.
const LINES_DIFFER = 2;
// A long line that opens in lower case goes on from somewhere: a list item's
// last line, inset on the left, can happen to end as far in on the right.
const GOES_ON = /^\p{Ll}/u;
const LONG_LINE = 0.5;
// A one-line block stops this many of its em short of what stands beside it.
const BESIDE_GAP = 1;
// How much of the shorter one's height two boxes must share to stand beside each
// other: lines above and below touch through their glyph boxes.
const BESIDE_SHARE = 0.25;
const SHARED_X = 1; // pt two boxes must share across for one to stand under the other
// Of a block's em, how far its last line box may run into the next block's first:
// the leading and the room over the ascenders and under the descenders, all of
// which are empty, come to about a quarter of an em in Times and in a CJK face.
const SEAM = 0.15;

// Whether a block stands centred between its column's `edge`s: {x1, x2}.
export function isCentred(p, edge) {
  const width = edge.x2 - edge.x1;
  const middle = (edge.x1 + edge.x2) / 2;
  const lines = p.lines || [];
  if (!lines.length || p.x1 - edge.x1 < p.size * CLEAR || edge.x2 - p.x2 < p.size * CLEAR) return false;
  const near = Math.max(p.size * OFF_MIDDLE, width * OFF_MIDDLE_WIDTH);
  if (lines.some((l) => Math.abs((l.x1 + l.x2) / 2 - middle) > near)) return false;
  if (GOES_ON.test(p.text || "") && p.x2 - p.x1 > width * LONG_LINE) return false;
  const widths = lines.map((l) => l.x2 - l.x1);
  return lines.length === 1 || Math.max(...widths) - Math.min(...widths) >= p.size * LINES_DIFFER;
}

const beside = (a, b) => Math.min(a.y2, b.y2) - Math.max(a.y1, b.y1) >= Math.min(a.y2 - a.y1, b.y2 - b.y1) * BESIDE_SHARE;

// The box a one-line block's text may fill, {x1, x2}: out to its column's
// `edge`s, short of what stands beside it, `others` being the page's other
// blocks and its figures. What stands beside it are the others' lines, not their
// boxes, which can run past it above or below; a line it lies inside (a fragment
// of that line) leaves it no room either way. A block grows to the right, a
// centred one both ways alike. null when it has no more room than its own line.
export function roomOf(p, edge, others, centred) {
  const gap = p.size * BESIDE_GAP;
  let left = edge.x1, right = edge.x2;
  for (const o of others) {
    for (const l of o.lines || [o]) {
      if (!beside(p, l)) continue;
      if (l.x1 >= p.x2) right = Math.min(right, l.x1 - gap);
      else if (l.x2 <= p.x1) left = Math.max(left, l.x2 + gap);
      else { right = Math.min(right, p.x2); left = Math.max(left, p.x1); }
    }
  }
  const toRight = right - p.x2;
  if (!centred) return toRight > 0 ? { x1: p.x1, x2: right } : null;
  const both = Math.min(p.x1 - left, toRight);
  return both > 0 ? { x1: p.x1 - both, x2: p.x2 + both } : null;
}

// Where a block's text stops, in pt down the page: just past the top of the
// nearest of its `others` that starts inside the foot of its box, from below the
// middle of its last line, across its text's width, since that block's text
// starts at its own box's top. Tall glyph boxes make such a seam: a block filling
// its box would write its last line over the next one's first. Line boxes may
// touch by SEAM: they hold nothing there. null with none: the box's foot.
export function footOf(p, others) {
  const { x1, x2 } = p.room || p;
  const last = p.lines?.at(-1);
  if (!last) return null;
  const from = (last.y1 + last.y2) / 2, to = p.y2 + 2 * PAD - p.size * SEAM;
  let top = null;
  for (const o of others) {
    const first = o.lines?.[0];
    if (!first || first.y1 <= from || first.y1 >= to) continue;
    if (Math.min(x2, first.x2) - Math.max(x1, first.x1) < SHARED_X) continue;
    if (top === null || first.y1 < top) top = first.y1;
  }
  return top === null ? null : top + p.size * SEAM;
}

// The column a block out of the flow stands in (a caption, a running head): the
// nearest one, up or down the page, that holds most of it.
function nearestColumn(p, columns) {
  let best = null, far = Infinity;
  for (const c of columns) {
    const shared = Math.min(c.x2, p.x2) - Math.max(c.x1, p.x1);
    if (shared < (p.x2 - p.x1) / 2) continue;
    const away = Math.max(c.y1 - p.y2, p.y1 - c.y2, 0);
    if (away < far) { best = c; far = away; }
  }
  return best;
}

// Marks a page's translated blocks, in place: `centred`, a one-line block's
// `room` (roomOf) and a block's `foot` (footOf). A block over a `figures` figure
// stays in its own box: what is beside it there is the drawing.
export function markShape(paras, figures = []) {
  const { edges, col } = pageFlow(paras);
  const columns = paras.flatMap((p, i) => (edges[i] ? [{ ...edges[i], y1: p.y1, y2: p.y2 }] : []));
  paras.forEach((p, i) => {
    if (!p.translate) return;
    const others = paras.filter((o) => o !== p);
    const edge = edges[i] || (col[i] === "out" && nearestColumn(p, columns));
    if (edge) {
      const centred = isCentred(p, edge);
      if (centred) p.centred = true;
      const room = p.nLines === 1 && !p.overFigure && roomOf(p, edge, [...others, ...figures], centred);
      if (room) p.room = room;
    }
    const foot = footOf(p, others);
    if (foot !== null) p.foot = foot;
  });
}
