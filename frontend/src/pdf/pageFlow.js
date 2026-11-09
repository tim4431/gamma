// A page's structure for the PDF translated view, read from its segmented
// blocks: the body size, captions and headings, the furniture a paragraph
// flows past, the columns, the reading order and the body flow. Pure, like
// pdf/pdfTranslate.js, whose blocks it reads. The planner
// (pdf/translatePlan.js) walks the flow it gives. docs/dev/translation.md
// "What a request carries" describes the rules.
import { CJK } from "./pdfTranslate.js";

export { CJK };
export const ENDS_SENTENCE = /[.!?:;。！？：；…]["'”’)\]）】」』]*$/;
// A caption: "FIG. 1.", "Figure 2:", "TABLE I.", "Fig. S.2.", "Figure A1.",
// "Extended Data Fig. 3 |". The number is followed by punctuation;
// "Figure 1 compares…" and "Figs. 2–4 show…" in running text are not.
const CAPTION_WORD = /^(?:(?:supplementa(?:ry|l)(?:\s+information)?|extended\s+data)\s+)?(?:fig(?:ure)?s?|tab(?:le)?s?|scheme|algorithm|listing|chart|plate)\.?\s*/i;
const CAPTION_NUMBER = /^(?:S\.?\s?)?(?:[A-Z]\.?\s?\d+|\d+|[IVXLC]+)(?:\.\d+)*[a-z]?/;
// A section number before a word that opens a title. A capital letter counts
// with a period ("A. Results", "A1. Review"), or before a capitalised word ("A
// Proof of Theorem 1"): "A similar argument" is prose, and a line that opens
// on a fraction's lower part ("2 to incident power") or a postcode ("70569
// Stuttgart") is none.
const NUMBERED_HEADING = /^(?:\d{1,3}|[IVXLC]{2,}|[A-Z]\d*(?=\.))(?:\.\d+)*\.?\s+[\p{Lu}\p{Lo}]|^[A-Z]\s+\p{Lu}/u;
const NAMED_HEADING = /^(abstract|introduction|background|related work|methods?|materials and methods|results|discussion|conclusions?|summary|acknowledge?ments?|references|bibliography|appendix(\s+[A-Z\d]+)?)\.?$/i;
// A running head or foot, which a number or an initial makes look like a
// numbered heading: a journal line with its folio ("12 | Nature | Vol 600 | 14
// October 2021", where Wiley's "1 | INTRODUCTION" has one bar and is a
// heading), "M. Saffman et al.", or two names ("12 J. Smith and B. Jones"),
// each with an initial, where "A. Results and discussion" has a word.
const BYLINE = /^\d+\s*\|[^|]*\||^(?:\d+[\s|]+)?(?:(?:\p{Lu}\.[\s-]*)*\p{Lu}[\p{L}'’-]+\s+(?:et al|ET AL)\b|(?:\p{Lu}\.[\s-]*)+\p{Lu}[\p{L}'’-]+(?:\s*,\s*(?:and\s+)?|\s+(?:and|AND)\s+|\s*&\s*)\p{Lu}\.)/u;
// A journal's folio: digits and capitals, hyphenated numbers after them
// ("035002-2", "1700123", "L12345"). A figure's row of ticks has spaces,
// periods or parentheses ("0 1 2 3", "0.2 0.4", "(1) (2)", "-0.25").
const FOLIO = /^(?=.*\d)[\p{Lu}\d]+(?:[-–]\d+)*$/u;
// A formula token in a block's `src`.
export const TOKEN = /\{v(\d+)\}/g;

const NO_LIST = Object.freeze({ blocks: new Set(), alone: false });
// The share of a page's text in paragraphs outside its list below which the
// page holds little but the list.
const LIST_PAGE = 0.05;

// The page's body text size: the most common block size, weighted by text.
// The blocks of a reference list (`listed`) do not count: on a page that ends
// in a small list they can hold most of its text. A page that holds little
// but its list, a running head or a stray piece of an entry, reads by the
// list.
export function bodySize(paras, listed = NO_LIST.blocks) {
  const rest = paras.filter((p, i) => p.translate && !listed.has(i));
  const chars = (ps) => ps.reduce((n, p) => n + p.text.length, 0);
  const running = chars(rest.filter((p) => p.nLines >= 2));
  const weight = new Map();
  for (const p of running > chars(paras.filter((b) => b.translate)) * LIST_PAGE ? rest : paras) {
    if (!p.translate) continue;
    const k = Math.round(p.size * 4) / 4;
    weight.set(k, (weight.get(k) || 0) + p.text.length);
  }
  let best = 0, size = 0;
  for (const [k, w] of weight) if (w > best) { best = w; size = k; }
  return size || (paras[0]?.size ?? 10);
}

export const nearSize = (a, b) => Math.abs(a - b) <= b * 0.12;
export const isNumbered = (t) => NUMBERED_HEADING.test(t);

// After the number: punctuation (an optional "(color online)" or "(left)"
// first), or — for a caption word that is not a plural — a capitalised word
// ("Fig. 1 Conceptual overview."); a word in capitals ("FIGURE 1 (a)",
// "TABLE II") is a caption whatever follows, running text never sets it so.
export function isCaption(p) {
  const t = p.text.trim();
  const word = t.match(CAPTION_WORD);
  if (!word) return false;
  const rest = t.slice(word[0].length);
  const number = rest.match(CAPTION_NUMBER);
  if (!number) return false;
  if (/^[A-Z]{3,}/.test(word[0]) && !/S\.?\s*$/.test(word[0])) return true;
  const after = rest.slice(number[0].length).replace(/^\s*\((?:colou?r(?:\s+online)?|online|left|right|top|bottom|above|below)\)/i, "");
  if (/^\s*(?:[.:|]|[—–-](?!\s*\d))/.test(after)) return true;
  return !/s\.?\s*$/i.test(word[0]) && /^\s+\p{Lu}/u.test(after);
}

export function isHeading(p, body) {
  if (!p.translate || p.nLines > 3) return false;
  const t = p.text.trim();
  if (BYLINE.test(t)) return false;
  if (p.size >= body * 1.12 && t.length < 200) return true;
  if (p.nLines > 2 || t.length > 100) return false;
  if (NAMED_HEADING.test(t)) return true;
  if (ENDS_SENTENCE.test(t) && !/^\S+\.\s/.test(t)) return false;
  return NUMBERED_HEADING.test(t) || (/[A-Z]{3}/.test(t) && !/[a-z]/.test(t));
}

// Running text, not a label, a table row or a list of symbols: six words or
// more, half of them with three letters in lower case (or, without spaces,
// CJK text of fifteen characters).
export function isProse(text) {
  const t = text.trim();
  if ((t.match(new RegExp(CJK.source, "g")) || []).length >= 15) return true;
  const words = t.split(/\s+/);
  return words.length >= 6 && words.filter((w) => /\p{Ll}{3}/u.test(w)).length >= words.length / 2;
}

// Blocks a paragraph flows past without ending: text in a figure
// (pdf/pageFigures.js), page numbers and stray symbols, running heads and
// footnotes in a smaller size, figure captions, and one-line blocks set apart
// in the page's top or bottom margin, above or below all its paragraphs (a
// running head at body size, a folio, a journal line) — unless they start in
// lower case (the end of a paragraph carried over from the page before) or
// are a numbered or named heading, which a byline only looks like. A
// reference list (`listed`) is none of these, in whatever size it is set: it
// has columns of its own.
function furniture(paras, body, listed) {
  const multi = paras.filter((p) => p.translate && p.nLines >= 2 && nearSize(p.size, body));
  const top = multi.length ? Math.min(...multi.map((p) => p.y1)) : -Infinity;
  const bottom = multi.length ? Math.max(...multi.map((p) => p.y2)) : Infinity;
  const inMargin = (p) => p.nLines === 1 && (p.y2 <= top || p.y1 >= bottom);
  return paras.map((p, i) => {
    if (p.figure) return true;
    if (listed.has(i)) return false;
    // A longer block that is no text (a display equation, whose fraction
    // parts can make it look small; a table of numbers) stays in the flow:
    // a paragraph never joins across it. A folio is no such block, though a
    // display equation can stand in the margin too.
    if (!p.translate) return p.text.trim().length <= 4 || (inMargin(p) && FOLIO.test(p.text.trim()));
    if (p.size < body * 0.85 || isCaption(p)) return true;
    const t = textOf(p);
    // A numbered or named section heading at the page's top is no running
    // head: those carry no section number.
    return inMargin(p) && !/^\p{Ll}/u.test(t)
      && (BYLINE.test(t) || (!NUMBERED_HEADING.test(t) && !NAMED_HEADING.test(t)));
  });
}

// A block's words as the model reads them: its text with its formulas,
// citation markers and URLs left out (a citation superscript after a
// sentence's period is no end of the sentence's text).
export function textOf(p) {
  return p.src.replace(TOKEN, "").replace(/\s+/g, " ").trim();
}

// Whether a page sets its paragraphs apart by a first-line indent (a
// paragraph without one goes on from before) or by space alone (then the
// indent tells nothing).
function indented(paras, body) {
  return paras.filter((p) => p.translate && p.nLines >= 2 && nearSize(p.size, body) && p.indent > p.size * 0.6).length >= 2;
}

// Reading order of a page's blocks (the segmenter emits them by baseline, so
// two columns interleave), and the column each block sits in. The page is
// cut first where a band of whitespace runs across its full width with text
// in both halves of the page on both sides (above a reference list, under a
// title block) — not beside a figure, where only one column has a gap. Within such
// a region, blocks wider than 60% of the text width — a title, an abstract,
// a full-width caption — each stand alone; the narrower blocks between them
// form a band, read column by column, each column top to bottom. A
// single-column page is all wide blocks: top to bottom. Furniture
// (`isOut`) takes no part in forming columns and reads last. The text width
// is the body's and the reference list's (`listed`), whatever the list's size:
// beside a column of body text, a smaller list's lines would otherwise read as
// wide and stand between the body's. `col` keys a block's column
// ("region.band.column"), `colWidth` is that column's width, `edges` its left
// and right edges, the text width's where a band has one column: nothing stands
// beside it. Furniture has none.
export function pageLayout(paras, isOut = () => false, body = bodySize(paras), listed = NO_LIST.blocks) {
  const n = paras.length;
  const order = [];
  const col = new Array(n);
  const colWidth = new Array(n);
  const edges = new Array(n).fill(null);
  if (!n) return { order, col, colWidth, edges };
  const textual = paras.filter((p, i) => !isOut(i) && (nearSize(p.size, body) || listed.has(i)));
  const pool = textual.length ? textual : paras;
  const left = Math.min(...pool.map((p) => p.x1));
  const right = Math.max(...pool.map((p) => p.x2));
  const span = right - left || 1;
  const wide = (p) => p.x2 - p.x1 > span * 0.6;
  const inFlow = paras.map((_, i) => i).filter((i) => !isOut(i));
  const byY = inFlow.sort((a, b) => (paras[a].y1 - paras[b].y1) || (paras[a].x1 - paras[b].x1));
  // Whether the blocks within eight body sizes of a gap, on one side of it,
  // hold text in both halves of the page: a column may end a few lines
  // early above a reference list, or hold only a table's sparse cells, but
  // beside a figure the other column has nothing at all.
  const middle = (left + right) / 2;
  const covered = (ids) => ids.some((i) => paras[i].x1 < middle - span * 0.1)
    && ids.some((i) => paras[i].x2 > middle + span * 0.1);
  const regions = [];
  let bottom = -Infinity;
  for (const i of byY) {
    const p = paras[i];
    if (regions.length && p.y1 - bottom > body * 1.2) {
      const above = regions[regions.length - 1].filter((j) => paras[j].y2 >= bottom - body * 8);
      const below = byY.filter((j) => paras[j].y1 >= p.y1 && paras[j].y1 <= p.y1 + body * 8);
      if (covered(above) && covered(below)) regions.push([]);
    }
    if (!regions.length) regions.push([]);
    regions[regions.length - 1].push(i);
    bottom = Math.max(bottom, p.y2);
  }
  regions.forEach((region, r) => {
    let band = [], b = 0;
    const flush = () => {
      const cols = [];
      for (const i of band.sort((x, y) => paras[x].x1 - paras[y].x1)) {
        const p = paras[i];
        const mid = (p.x1 + p.x2) / 2;
        let best = null, bestOv = 0;
        for (const c of cols) {
          const ov = Math.min(c.x2, p.x2) - Math.max(c.x1, p.x1);
          if (ov < 0.5 * Math.min(p.x2 - p.x1, c.x2 - c.x1)) continue;
          // On a tie, the column the block's middle stands in.
          const better = ov > bestOv || (ov === bestOv && mid >= c.x1 && mid <= c.x2);
          if (better) { best = c; bestOv = ov; }
        }
        if (!best) { best = { x1: p.x1, x2: p.x2, items: [] }; cols.push(best); }
        best.items.push(i);
        best.x1 = Math.min(best.x1, p.x1);
        best.x2 = Math.max(best.x2, p.x2);
      }
      cols.sort((x, y) => x.x1 - y.x1);
      cols.forEach((c, k) => {
        for (const i of c.items.sort((x, y) => paras[x].y1 - paras[y].y1)) {
          order.push(i);
          col[i] = `${r}.${b}.${k}`;
          colWidth[i] = c.x2 - c.x1;
          edges[i] = cols.length > 1 ? { x1: c.x1, x2: c.x2 } : { x1: left, x2: right };
        }
      });
      band = [];
      b += 1;
    };
    // Wide blocks of one region are one column: the text of a single-column
    // page runs down it.
    for (const i of region) {
      if (wide(paras[i])) {
        flush();
        order.push(i);
        col[i] = `${r}.w`;
        colWidth[i] = span;
        edges[i] = { x1: left, x2: right };
      } else band.push(i);
    }
    flush();
  });
  for (let i = 0; i < n; i++) {
    if (!isOut(i)) continue;
    order.push(i);
    col[i] = "out";
    colWidth[i] = paras[i].x2 - paras[i].x1;
  }
  return { order, col, colWidth, edges };
}

// The page as the planner reads it: blocks in reading order, and the body
// flow (reading order without the furniture) that paragraphs continue along.
// `list` is the page's reference list (pageLists.pageList), kept with the flow.
// A list the page holds by itself takes part in the page's structure. The
// short end of one, which only a neighbouring page can tell, changes nothing
// here, so a page's flow is the same whichever pages are segmented. A block
// with no text in a figure (`inFigure`, pdf/pageFigures.js) keeps its place in
// the columns and leaves the flow: a paragraph goes on past a tick number.
export function pageFlow(paras, list = NO_LIST) {
  const listed = list.alone ? list.blocks : NO_LIST.blocks;
  const body = bodySize(paras, listed);
  const out = furniture(paras, body, listed);
  const { order, col, colWidth, edges } = pageLayout(paras, (i) => out[i], body, listed);
  const flow = order.filter((i) => !out[i] && !paras[i].inFigure);
  const pos = new Map(flow.map((i, k) => [i, k]));
  // The width a paragraph's lines fill: the median of the page's
  // multi-line paragraphs (a label alone in its "column" has its own).
  const widths = paras.filter((p) => p.translate && p.nLines >= 3 && nearSize(p.size, body))
    .map((p) => p.x2 - p.x1).sort((a, b) => a - b);
  const measure = widths.length ? widths[widths.length >> 1] : Math.max(...colWidth.filter(Boolean), 0);
  return { paras, list, body, order, col, edges, flow, pos, measure, spaced: !indented(paras, body) };
}

export const readingOrder = (paras) => pageFlow(paras).order;
