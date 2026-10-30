// Translation planning for the PDF translated view: which text of a page goes
// to the model, in which request, with what context, and where each piece of
// the reply goes. Pure, like pdf/pdfTranslate.js: segmented blocks in, units
// and chunks out. The viewer's queue (pdf/PdfViewer.jsx) and the translation
// bench (tests/bench/translateBench.mjs) both plan with it, so the bench
// measures what the viewer sends. docs/dev/translation.md "What a request
// carries" describes the rules.
//
// A unit is one text the model translates and where its translation goes:
// {src, chars, parts, targets: [{pn, i}], tokens, before, after, section}, `i`
// a block's index on page `pn`. A paragraph that a column or page break cut
// in two (or three) is ONE unit: its parts are joined with BREAK, which the
// model keeps at the matching place, and the reply is split there again
// (`results`). Each block's formula tokens are renumbered across the unit
// ({v1}… in order); `tokens[n - 1]` says whose token {vn} is. A chunk is one
// request: {units, texts, context}.

import { HYPHENATED } from "./pdfTranslate.js";

export const CHUNK_UNITS = 10;
export const CHUNK_CHARS = 2500;
// The marker at a join. The prompt (backend gamma/translate_context.py) tells
// the model to keep every one, at the matching place.
export const BREAK = "{br}";
// A chain joins at most this many blocks, and stops growing past this size.
const MAX_CHAIN = 3;
const MAX_UNIT_CHARS = 6000;
// The context a request carries around its texts, and the most of the
// document's opening text it sends for the paper's brief (`head`, when the
// server has no stored copy to read; it reads as much itself).
const CONTEXT_CHARS = 1500;
export const HEAD_CHARS = 12000;
const SECTION_REACH = 80; // flow blocks searched back for the section heading

const CJK = /[぀-ヿ㐀-鿿가-힯]/;
export const ENDS_SENTENCE = /[.!?:;。！？：；…]["'”’)\]）】」』]*$/;
// A caption: "FIG. 1.", "Figure 2:", "TABLE I.", "Fig. S.2.", "Figure A1.",
// "Extended Data Fig. 3 |". The number is followed by punctuation;
// "Figure 1 compares…" and "Figs. 2–4 show…" in running text are not.
const CAPTION_WORD = /^(?:(?:supplementa(?:ry|l)(?:\s+information)?|extended\s+data)\s+)?(?:fig(?:ure)?s?|tab(?:le)?s?|scheme|algorithm|listing|chart|plate)\.?\s*/i;
const CAPTION_NUMBER = /^(?:S\.?\s?)?(?:[A-Z]\.?\s?\d+|\d+|[IVXLC]+)(?:\.\d+)*[a-z]?/;
const NUMBERED_HEADING = /^((\d+|[IVXLC]+|[A-Z])(\.\d+)*\.?)\s+\S/;
const NAMED_HEADING = /^(abstract|introduction|background|related work|methods?|materials and methods|results|discussion|conclusions?|summary|acknowledge?ments?|references|bibliography|appendix(\s+[A-Z\d]+)?)\.?$/i;
// Words a sentence cannot stop on.
const OPEN_END = /(?:\s[–—]|[,(]|\b(?:the|a|an|of|in|on|at|to|for|and|or|nor|but|with|by|from|as|into|onto|than|that|which|whose|is|are|was|were|be|been|its|their|our|this|these|those|such|via|per))$/i;
export const TOKEN = /\{v(\d+)\}/g;

// --- page structure ---------------------------------------------------------

// The page's body text size: the most common block size, weighted by text.
export function bodySize(paras) {
  const weight = new Map();
  for (const p of paras || []) {
    if (!p.translate) continue;
    const k = Math.round(p.size * 4) / 4;
    weight.set(k, (weight.get(k) || 0) + p.text.length);
  }
  let best = 0, size = 0;
  for (const [k, w] of weight) if (w > best) { best = w; size = k; }
  return size || (paras?.[0]?.size ?? 10);
}

const nearSize = (a, b) => Math.abs(a - b) <= b * 0.12;

// After the number: punctuation (an optional "(color online)" first), or —
// for a caption word that is not a plural — a capitalised word ("Fig. 1
// Conceptual overview."); a word in capitals ("FIGURE 1 (a)", "TABLE II")
// is a caption whatever follows, running text never sets it so.
export function isCaption(p) {
  const t = p.text.trim();
  const word = t.match(CAPTION_WORD);
  if (!word) return false;
  const rest = t.slice(word[0].length);
  const number = rest.match(CAPTION_NUMBER);
  if (!number) return false;
  if (/^[A-Z]{3,}/.test(word[0]) && !/S\.?\s*$/.test(word[0])) return true;
  const after = rest.slice(number[0].length).replace(/^\s*\((?:colou?r(?:\s+online)?|online)\)/i, "");
  if (/^\s*(?:[.:|]|[—–-](?!\s*\d))/.test(after)) return true;
  return !/s\.?\s*$/i.test(word[0]) && /^\s+\p{Lu}/u.test(after);
}

export function isHeading(p, body) {
  if (!p.translate || p.nLines > 3) return false;
  const t = p.text.trim();
  if (p.size >= body * 1.12 && t.length < 200) return true;
  if (p.nLines > 2 || t.length > 100) return false;
  if (NAMED_HEADING.test(t)) return true;
  if (ENDS_SENTENCE.test(t) && !/^\S+\.\s/.test(t)) return false;
  return NUMBERED_HEADING.test(t) || (/[A-Z]{3}/.test(t) && !/[a-z]/.test(t));
}

// Running text, not a label, a table row or a list of symbols: six words or
// more, half of them with three letters in lower case (or, without spaces,
// CJK text of fifteen characters).
function isProse(text) {
  const t = text.trim();
  if ((t.match(new RegExp(CJK.source, "g")) || []).length >= 15) return true;
  const words = t.split(/\s+/);
  return words.length >= 6 && words.filter((w) => /\p{Ll}{3}/u.test(w)).length >= words.length / 2;
}

// Blocks a paragraph flows past without ending: page numbers and stray
// symbols, running heads and footnotes in a smaller size, figure captions,
// and one-line blocks set apart in the page's top or bottom margin, above or
// below all its paragraphs (a running head at body size, a folio, a
// journal line) — unless they start in lower case (the end of a paragraph
// carried over from the page before) or are a numbered or named heading.
function furniture(paras, body) {
  const multi = paras.filter((p) => p.translate && p.nLines >= 2 && nearSize(p.size, body));
  const top = multi.length ? Math.min(...multi.map((p) => p.y1)) : -Infinity;
  const bottom = multi.length ? Math.max(...multi.map((p) => p.y2)) : Infinity;
  return paras.map((p) => {
    // A longer block that is no text (a display equation, whose fraction
    // parts can make it look small; a table of numbers) stays in the flow:
    // a paragraph never joins across it.
    if (!p.translate) return p.text.trim().length <= 4;
    if (p.size < body * 0.85 || isCaption(p)) return true;
    const t = textOf(p);
    // A numbered or named section heading at the page's top is no running
    // head: those carry no section number.
    return p.nLines === 1 && (p.y2 <= top || p.y1 >= bottom) && !/^\p{Ll}/u.test(t)
      && !NUMBERED_HEADING.test(t) && !NAMED_HEADING.test(t);
  });
}

// A block's words as the model reads them: its text with its formulas,
// citation markers and URLs left out (a citation superscript after a
// sentence's period is no end of the sentence's text).
function textOf(p) {
  return (p.src ?? p.text).replace(TOKEN, "").replace(/\s+/g, " ").trim();
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
// (`isOut`) takes no part in forming columns and reads last. `col` keys a
// block's column ("region.band.column"), `colWidth` is that column's width.
export function pageLayout(paras, isOut = () => false) {
  const n = (paras || []).length;
  const order = [];
  const col = new Array(n);
  const colWidth = new Array(n);
  if (!n) return { order, col, colWidth };
  const body = bodySize(paras);
  const textual = paras.filter((p, i) => !isOut(i) && nearSize(p.size, body));
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
  return { order, col, colWidth };
}

export function readingOrder(paras) {
  const body = bodySize(paras);
  const out = furniture(paras || [], body);
  return pageLayout(paras, (i) => out[i]).order;
}

// The page as the planner reads it: blocks in reading order, and the body
// flow (reading order without the furniture) that paragraphs continue along.
export function pageFlow(paras) {
  const body = bodySize(paras);
  const out = furniture(paras, body);
  const { order, col, colWidth } = pageLayout(paras, (i) => out[i]);
  const flow = order.filter((i) => !out[i]);
  const pos = new Map(flow.map((i, k) => [i, k]));
  // The width a paragraph's lines fill: the median of the page's
  // multi-line paragraphs (a label alone in its "column" has its own).
  const widths = paras.filter((p) => p.translate && p.nLines >= 3 && nearSize(p.size, body))
    .map((p) => p.x2 - p.x1).sort((a, b) => a - b);
  const measure = widths.length ? widths[widths.length >> 1] : Math.max(...colWidth.filter(Boolean), 0);
  return { paras, body, order, col, colWidth, flow, pos, measure, spaced: !indented(paras, body) };
}

// Does paragraph A go on in block B (the next block of the body flow, at
// the top of the next column or page)? A is running text of two lines or
// more that ends mid-sentence; B is body text of the same size that opens
// no paragraph of its own: no heading or caption, no first-line indent, and
// running text itself when it has two lines or more (labels and table
// cells are not). B reads on from A: it starts in lower case, or A breaks
// off on a word no sentence ends on — needed where the indent tells
// nothing: a page without indents (`spaced`), and a one-line B. A one-line B
// must also look like a paragraph's last line: it ends a sentence, fills
// its column (`wide`), or introduces a display equation (`intro`: "…in the
// rotating frame the" / "Hamiltonian reads" / the equation); a figure's
// label or a table's header does none of that. A word hyphenated at A's end
// goes on only in lower case.
export function continues(a, b, body, { spaced = false, wide = true, intro = false } = {}) {
  if (!a?.translate || !b?.translate || a.nLines < 2) return false;
  if (!nearSize(a.size, body) || !nearSize(b.size, body) || !nearSize(b.size, a.size)) return false;
  const end = textOf(a), start = textOf(b);
  if (!start || ENDS_SENTENCE.test(end) || !isProse(end)) return false;
  if (isHeading(b, body) || isCaption(b)) return false;
  if (b.indent != null && (b.indent > b.size * 0.6 || !isProse(start))) return false;
  if (HYPHENATED.test(end)) return /^[a-z]/.test(start);
  const readsOn = /^\p{Ll}/u.test(start) || OPEN_END.test(end);
  if (b.indent == null) return readsOn && (ENDS_SENTENCE.test(start) || wide || intro);
  return readsOn || !spaced;
}

// --- units ------------------------------------------------------------------

// Join a chain's texts with BREAK. A word hyphenated across the join goes
// whole to the first part; a part left empty by that (the column held only
// the word's tail) is kept, its block to be masked rather than written.
function joinParts(texts) {
  const parts = texts.map((t) => t.trim());
  for (let k = 0; k + 1 < parts.length; k++) {
    if (HYPHENATED.test(parts[k]) && /^[a-z]/.test(parts[k + 1])) {
      const m = parts[k + 1].match(/^(\S+)\s*([\s\S]*)$/);
      parts[k] = parts[k].slice(0, -1) + m[1];
      parts[k + 1] = m[2];
    }
  }
  return parts;
}

function makeUnit(chain, blockAt) {
  const tokens = [];
  const texts = chain.map((t) => (blockAt(t).src ?? blockAt(t).text).replace(TOKEN, (_, k) => {
    tokens.push({ pn: t.pn, i: t.i, k: Number(k) });
    return `{v${tokens.length}}`;
  }));
  const parts = joinParts(texts);
  const src = parts.filter(Boolean).join(` ${BREAK} `);
  return { src, chars: src.length, parts, targets: chain, tokens };
}

// A planner for one job. `paras(pn)` gives a page's segmented blocks, or null
// while the page isn't segmented (a join into it waits for that);
// `inJob(pn)` says whether the job covers the page; `filled(pn, i)` whether
// a block already has its translation. Units are claimed once per job: a
// paragraph that runs from page 3 into page 4 is planned by whichever of
// the two pages comes first, and fills both.
export function createPlanner({ paras, inJob = () => true, filled = () => false }) {
  const flows = new Map();
  const claimed = new Set();
  const id = (t) => `${t.pn}:${t.i}`;
  const flowOf = (pn) => {
    if (!flows.has(pn)) {
      const p = paras(pn);
      if (!p) return null;
      flows.set(pn, pageFlow(p));
    }
    return flows.get(pn);
  };
  const blockAt = (t) => flowOf(t.pn).paras[t.i];
  // The block before or after `t` in the body flow, across a page break
  // into a segmented page.
  const step = (t, dir) => {
    const f = flowOf(t.pn);
    const k = f.pos.get(t.i);
    if (k === undefined) return null;
    if (f.flow[k + dir] !== undefined) return { pn: t.pn, i: f.flow[k + dir] };
    const other = flowOf(t.pn + dir);
    if (!other || !other.flow.length) return null;
    return { pn: t.pn + dir, i: dir > 0 ? other.flow[0] : other.flow[other.flow.length - 1] };
  };
  const free = (t) => t && inJob(t.pn) && !claimed.has(id(t)) && !filled(t.pn, t.i);
  // Does `a` go on in `b`? Only across a column or page break: two blocks
  // of one column are two paragraphs (the segmenter split them for a
  // reason). Down the same x-column (past a figure or a display equation
  // that cut the page across) the indent test says nothing on a page whose
  // paragraphs are set apart by space instead, so B has to start in lower
  // case there. Body size is a's page's; a page break needs both pages to
  // agree on it.
  const joins = (a, b) => {
    const fa = flowOf(a.pn), fb = flowOf(b.pn);
    if (a.pn === b.pn && fa.col[a.i] === fa.col[b.i]) return false;
    const A = blockAt(a), B = blockAt(b);
    if (a.pn === b.pn && B.x1 < A.x2 - fa.body && !/^\p{Ll}/u.test(textOf(B))) return false;
    const after = step(b, 1);
    const next = after && blockAt(after);
    return nearSize(fa.body, fb.body) && continues(A, B, fa.body, {
      spaced: fb.spaced,
      wide: B.x2 - B.x1 >= fb.measure * 0.6,
      intro: !!next && !next.translate,
    });
  };
  // The text around a unit, for the model to read: the flow block before its
  // first part and after its last, as extracted.
  const around = (t, dir) => {
    const n = step(t, dir);
    if (!n) return "";
    const text = blockAt(n).text.trim();
    return dir < 0 ? text.slice(-CONTEXT_CHARS) : text.slice(0, CONTEXT_CHARS);
  };
  // The heading the unit sits under: the nearest one before it in the flow,
  // over earlier pages as far as they are segmented.
  const sectionOf = (t) => {
    let cur = t;
    for (let k = 0; k < SECTION_REACH; k++) {
      cur = step(cur, -1);
      if (!cur) return "";
      const f = flowOf(cur.pn);
      if (isHeading(f.paras[cur.i], f.body)) return f.paras[cur.i].text.trim();
    }
    return "";
  };
  return {
    planPage(pn) {
      const f = flowOf(pn);
      if (!f) return [];
      const units = [];
      for (const i of f.order) {
        const t = { pn, i };
        if (!f.paras[i].translate || !free(t)) continue;
        // Walk back to where the paragraph starts (a page planned before the
        // one it starts on), then forward from there, within the size cap
        // counted from the block being planned.
        let start = t, chars = blockAt(t).text.length;
        for (let k = 1; k < MAX_CHAIN; k++) {
          const prev = step(start, -1);
          if (!free(prev) || !joins(prev, start)) break;
          chars += blockAt(prev).text.length;
          if (chars > MAX_UNIT_CHARS) break;
          start = prev;
        }
        let chain = [start];
        let size = blockAt(start).text.length;
        while (chain.length < MAX_CHAIN) {
          const next = step(chain[chain.length - 1], 1);
          if (!free(next) || !joins(chain[chain.length - 1], next)) break;
          size += blockAt(next).text.length;
          if (size > MAX_UNIT_CHARS) break;
          chain.push(next);
        }
        if (!chain.some((c) => c.pn === pn && c.i === i)) chain = [t];
        chain.forEach((c) => claimed.add(id(c)));
        const unit = makeUnit(chain, blockAt);
        unit.before = around(chain[0], -1);
        unit.after = around(chain[chain.length - 1], 1);
        unit.section = sectionOf(chain[0]);
        units.push(unit);
      }
      return units;
    },
  };
}

// Units → requests: a chunk closes once it holds `maxUnits` units or its text
// reaches `maxChars`. Its context is the text before its first unit, after
// its last, and the section its first unit sits under.
export function chunkUnits(units, { maxUnits = CHUNK_UNITS, maxChars = CHUNK_CHARS } = {}) {
  const chunks = [];
  let cur = [], chars = 0;
  const close = () => {
    chunks.push({
      units: cur, texts: cur.map((x) => x.src),
      context: { before: cur[0].before || "", after: cur[cur.length - 1].after || "", section: cur[0].section || "" },
    });
    cur = [];
    chars = 0;
  };
  for (const u of units) {
    cur.push(u);
    chars += u.chars;
    if (cur.length >= maxUnits || chars >= maxChars) close();
  }
  if (cur.length) close();
  return chunks;
}

// The fields a request adds to /api/ai/translate for its context, from the
// viewer's names: the page being translated (`pageId`, which names the paper
// and its brief), the document's opening text when there is no page to name
// it by (`head`), `waitBrief: false` for a text that must not wait while the
// paper's brief is being made (a job's first chunk, a selection), `keep`
// (whether the server may keep the translations in the workspace) and the
// chunk's `before`, `after` and `section`. The viewer's onTranslate and the
// bench both send these, so the bench measures the requests the viewer makes.
export function requestFields({ pageId = "", head = "", waitBrief = true, keep = true, before = "", after = "", section = "" } = {}) {
  const out = { wait_brief: waitBrief !== false, keep: keep !== false };
  if (pageId) out.page_id = pageId;
  if (head) out.head = head;
  if (before) out.before = before;
  if (after) out.after = after;
  if (section) out.section = section;
  return out;
}

// A chunk's request (`run`, resolving to the server's final reply), asked
// again when it fails. A refusal because the account's AI calls are all in
// use (a 429 with Retry-After: the server is still asking the model again
// for other chunks) waits as long as the server says, up to `patienceMs` in
// all; any other failure (the network, the provider, a stream cut off
// before its final line) is asked once more after `pauseMs`. A 429 without
// Retry-After (the server's shared allowance used up) is final. Resolves to
// the reply, or null once `signal` (the job's) aborts; throws the last
// failure.
export const RETRY_PAUSE_MS = 2000;
export const SLOT_PATIENCE_MS = 120000;
export async function requestChunk(run, signal, { pauseMs = RETRY_PAUSE_MS, patienceMs = SLOT_PATIENCE_MS } = {}) {
  const start = Date.now();
  let retried = false;
  for (;;) {
    let err = null;
    try {
      const res = await run();
      if (res) return res;
    } catch (e) {
      err = e;
    }
    if (signal?.aborted) return null;
    const left = patienceMs - (Date.now() - start);
    let wait;
    if (err?.retryAfter > 0 && left > 0) wait = Math.min(err.retryAfter * 1000, left);
    else if (!retried && !err?.retryAfter && err?.status !== 429) { wait = pauseMs; retried = true; }
    else if (err) throw err;
    else return null;
    await pause(wait, signal);
    if (signal?.aborted) return null;
  }
}

function pause(ms, signal) {
  return new Promise((resolve) => {
    const done = () => { clearTimeout(id); signal?.removeEventListener("abort", done); resolve(); };
    const id = setTimeout(done, ms);
    signal?.addEventListener("abort", done);
  });
}

// --- results ----------------------------------------------------------------

// Abbreviations whose period ends no sentence.
const ABBREVIATION = /(?:^|[\s(])(?:[A-Za-z]|e\.g|i\.e|cf|vs|al|etc|approx|Fig|Figs|Eq|Eqs|Ref|Refs|Sec|Secs|Tab|Abb|Gl|Kap|bzw|z\.B|vgl|Nr|No|resp)\.$/;

// Where to cut `text` near `at`: a sentence end, else a clause mark, else a
// space, else (CJK) anywhere, searching outward up to a quarter of the text.
// A period ends a sentence only before a space and a word that is no number,
// and not after an abbreviation ("Fig.", "e.g.", "0.987").
function cutNear(text, at) {
  const reach = Math.max(8, Math.round(text.length * 0.25));
  const sentence = (k) => {
    const c = text[k - 1];
    if (/[!?;。！？；]/.test(c)) return true;
    if (c !== ".") return false;
    if (!/\s/.test(text[k] || "") || /^\s*\d/.test(text.slice(k))) return false;
    return !ABBREVIATION.test(text.slice(Math.max(0, k - 8), k));
  };
  // A comma between digits ("0,987", "1,000") is part of a number.
  const clause = (k) => /[,，、:：]/.test(text[k - 1]) && !(text[k - 1] === "," && /\d/.test(text[k - 2] || "") && /\d/.test(text[k] || ""));
  for (const test of [sentence, clause, (k) => /\s/.test(text[k - 1])]) {
    for (let d = 0; d <= reach; d++) {
      for (const k of [at + d, at - d]) {
        if (k > 0 && k < text.length && test(k)) return k;
      }
    }
  }
  return Math.max(0, Math.min(text.length, at));
}

// Split `text` into `weights.length` pieces sized like the weights.
function splitByShare(text, weights) {
  const total = weights.reduce((n, w) => n + w, 0) || 1;
  const pieces = [];
  let rest = text, left = total;
  for (let k = 0; k < weights.length - 1; k++) {
    const at = cutNear(rest, Math.round(rest.length * weights[k] / left));
    pieces.push(rest.slice(0, at).trim());
    rest = rest.slice(at);
    left -= weights[k];
  }
  pieces.push(rest.trim());
  return pieces;
}

// Remove the markers from a text, joining around each the way lines join:
// no space between CJK characters.
function unmark(s) {
  return s.split(BREAK).map((x) => x.trim()).filter(Boolean)
    .reduce((acc, x) => (!acc ? x : CJK.test(acc[acc.length - 1]) && CJK.test(x[0]) ? acc + x : `${acc} ${x}`), "");
}

// The non-empty parts' pieces of a unit's reply: one per non-empty part, cut
// at the markers, or by the parts' lengths at the nearest sentence or clause
// boundary when the model lost, doubled or misplaced one.
function pieces(unit, text) {
  const live = unit.parts.filter(Boolean);
  if (live.length <= 1) return [String(text ?? "").replaceAll(BREAK, "").trim()];
  const cut = String(text ?? "").split(BREAK).map((x) => x.trim());
  if (cut.length === live.length && cut.every(Boolean)) return cut;
  return splitByShare(unmark(String(text ?? "")), live.map((p) => p.length || 1));
}

// A piece's tokens, numbered for the block it lands in: its own {vk}, or
// {v:pn.i.k} for a token of another block of the unit (the model moved a
// formula across a join), which the overlay looks up there.
function localize(unit, target, piece) {
  return piece.replace(TOKEN, (whole, n) => {
    const t = unit.tokens[Number(n) - 1];
    if (!t) return "";
    return t.pn === target.pn && t.i === target.i ? `{v${t.k}}` : `{v:${t.pn}.${t.i}.${t.k}}`;
  });
}

// A unit's reply → what each of its blocks shows: [{pn, i, text}], text a
// translation, null for one that came back as its source (the original
// shows; "Translate this page" asks again), or "" for a block whose words
// all went to the part before it (it is masked).
export function results(unit, reply) {
  const got = pieces(unit, reply);
  let k = 0;
  return unit.targets.map((t, j) => {
    const part = unit.parts[j];
    if (!part) return { ...t, text: "" };
    const piece = got[k++] ?? "";
    return { ...t, text: piece && piece !== part ? localize(unit, t, piece) : null };
  });
}

// The text streamed so far → what each block can show yet: the pieces
// before the markers written so far, and the one in progress (a marker or
// token half-written at its end dropped). Blocks past it get nothing yet.
export function partials(unit, text) {
  const live = unit.targets.filter((_, j) => unit.parts[j]);
  const cut = String(text ?? "").split(BREAK).slice(0, live.length);
  cut[cut.length - 1] = cut[cut.length - 1].replace(/\{[^}]*$/, "");
  return cut.map((piece, k) => ({ ...live[k], text: localize(unit, live[k], piece.trim()) })).filter((x) => x.text);
}
