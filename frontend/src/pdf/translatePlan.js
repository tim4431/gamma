// Translation planning for the PDF translated view: which text of a page goes
// to the model, in which request and with what context. Pure, like
// pdf/pdfTranslate.js: segmented blocks in, read through pdf/pageFlow.js, and
// units and chunks out; pdf/translateReply.js puts a unit's reply back on its
// blocks. The job (pdf/translateJob.js) and the translation bench
// (tests/bench/translateBench.mjs) both plan with it, so the bench measures
// what the viewer sends. docs/dev/translation.md "What a request carries"
// describes the rules.
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
import { CJK, ENDS_SENTENCE, TOKEN, isCaption, isHeading, isNumbered, isProse, nearSize, pageFlow, textOf } from "./pageFlow.js";
import { inList, pageList } from "./pageLists.js";
import { translatable } from "./transBlock.js";

const CHUNK_UNITS = 10;
const CHUNK_CHARS = 2500;
// The marker at a join. The prompt (backend gamma/translate_context.py) tells
// the model to keep every one, at the matching place.
export const BREAK = "{br}";
// A chain joins at most this many blocks, and stops growing past this size.
const MAX_CHAIN = 3;
const MAX_UNIT_CHARS = 6000;
// The context a request carries around its texts, and the most of the
// document's opening text it sends for the paper's brief (`head`, when the
// server has no stored copy to read; it reads as much itself, and no more of
// a head: BRIEF_TEXT_CHARS in gamma/translate_context.py).
const CONTEXT_CHARS = 1500;
export const HEAD_CHARS = 8000;
// How far the parts of a joined paragraph differ in size, as a share of it. A
// caption's or a figure's text set a size smaller than the body is no part of
// its paragraph, though it reads as body text.
const SAME_SIZE = 0.05;
// How far apart, in points, a text on two pages is one running head.
const SAME_PLACE = 4;
// The lines of a wrapped heading, in its size: how far their sizes and their
// middles differ, and the space between them.
const HEADING_SIZE = 0.05, HEADING_AXIS = 0.06, HEADING_GAP = 0.25;

// Words a sentence cannot stop on.
const OPEN_END = /(?:\s[–—]|[,(]|\b(?:the|a|an|of|in|on|at|to|for|and|or|nor|but|with|by|from|as|into|onto|than|that|which|whose|is|are|was|were|be|been|its|their|our|this|these|those|such|via|per))$/i;

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
  if (!a.translate || !b.translate || a.nLines < 2) return false;
  if (!nearSize(a.size, body) || !nearSize(b.size, body) || Math.abs(a.size - b.size) > a.size * SAME_SIZE) return false;
  const end = textOf(a), start = textOf(b);
  if (!start || ENDS_SENTENCE.test(end) || !isProse(end)) return false;
  if (isHeading(b, body) || isCaption(b)) return false;
  if (b.indent != null && (b.indent > b.size * 0.6 || !isProse(start))) return false;
  if (HYPHENATED.test(end)) return /^[a-z]/.test(start);
  const readsOn = /^\p{Ll}/u.test(start) || OPEN_END.test(end);
  if (b.indent == null) return readsOn && (ENDS_SENTENCE.test(start) || wide || intro);
  return readsOn || !spaced;
}

// Does heading A wrap onto line B? The segmenter keeps a line that starts
// deeper than the one above apart, for an author stack, so a centred heading
// that wraps comes as two blocks, and half a noun phrase is no text to
// translate. B is one line of A's size right under it, on the same middle.
// A fills half its column or more, and it reads as a heading by its words: it
// has a section number, breaks off on a word no phrase ends on, or is in
// capitals with no comma. Names and affiliations set large are none of these.
function headingWraps(a, b, { body, measure }) {
  if (!isHeading(a, body) || !b.translate || a.nLines > 2 || b.nLines > 1) return false;
  if (Math.abs(b.size - a.size) > a.size * HEADING_SIZE) return false;
  if (Math.abs(a.x1 + a.x2 - b.x1 - b.x2) / 2 > a.size * HEADING_AXIS || b.y1 - a.y2 > a.size * HEADING_GAP) return false;
  const start = textOf(a);
  if (a.x2 - a.x1 < measure * 0.5 || ENDS_SENTENCE.test(start) || isCaption(b) || isNumbered(textOf(b))) return false;
  return isNumbered(start) || (OPEN_END.test(start) && !start.endsWith(",")) || !/[,\p{Ll}]/u.test(start);
}

// A word of a title: a formula or a label ("RB BR RR") has none.
const TITLE_WORD = new RegExp(`\\p{L}{4}|(?:${CJK.source}){2}`, "u");

// Whether a heading, in its lines, can name the section under it for the
// model. It opens on a capital or a number, not on punctuation or in lower
// case (a line of a paragraph or a title); it has a word; it breaks off
// nowhere (a fragment); and it is no sentence ("3. If we exchange two rows,
// the sign changes.").
function isSection(lines, body) {
  if (!isHeading(lines[0], body)) return false;
  const t = lines.map((p) => p.text.trim()).join(" ");
  if (!/^[\p{Lu}\p{Lo}\d]/u.test(t) || !TITLE_WORD.test(lines.map(textOf).join(" "))) return false;
  if (/[,;:]$/.test(t) || OPEN_END.test(t) || HYPHENATED.test(t)) return false;
  return !(/\.$/.test(t) && t.split(/\s+/).length >= 6);
}

// Whether block `k` of the flow, at the body's size, stands right under text
// of its column: a line of a paragraph or a list that opens on a number ("1.
// The resonator supports…"). A heading is set apart, or follows a heading.
function runsOn(f, k) {
  const i = f.flow[k], j = f.flow[k - 1];
  if (k === 0 || f.col[i] !== f.col[j] || f.paras[i].size >= f.body * 1.12) return false;
  return f.paras[i].y1 - f.paras[j].y2 < f.paras[i].size * HEADING_GAP && !isHeading(f.paras[j], f.body);
}

// A page's possible section headings along its flow: {at, i, text}, `at` the
// place in the flow and `i` the block of a heading's first line.
function sectionsOf(f) {
  const heads = [];
  for (let k = 0; k < f.flow.length; k++) {
    const lines = [f.paras[f.flow[k]]];
    const i = f.flow[k], at = k;
    while (k + 1 < f.flow.length && headingWraps(lines[lines.length - 1], f.paras[f.flow[k + 1]], f)) lines.push(f.paras[f.flow[++k]]);
    if (isSection(lines, f.body) && !runsOn(f, at)) heads.push({ at, i, text: lines.map((p) => p.text.trim()).join(" ") });
  }
  return heads;
}

// A short block's text with its numbers left out, the same on every page a
// running head stands on.
const headKey = (p) => p.text.trim().replace(/\d+/g, "#").toLowerCase();

// Note where each short block of page `pn` stands, in `places` (headKey →
// [{pn, i, y}]).
function notePlaces(places, pn, blocks) {
  blocks.forEach((b, i) => {
    if (b.nLines > 3) return;
    const k = headKey(b);
    if (!places.has(k)) places.set(k, []);
    places.get(k).push({ pn, i, y: b.y1 });
  });
}
// Whether one of `list` (notePlaces) stands at `y` on a page other than `pn`.
const elsewhere = (list, pn, y) => list.some((o) => o.pn !== pn && Math.abs(o.y - y) <= SAME_PLACE);

// The running heads and feet among `pages` (`paras(pn)`: a page's blocks, or
// null while not segmented), as `${pn}:${i}`: a short block whose text,
// numbers aside, stands in the same place on another of those pages, as
// the planner's `repeats` tells them.
export function runningBlocks(paras, pages) {
  const places = new Map();
  for (const pn of pages) notePlaces(places, pn, paras(pn) || []);
  const running = new Set();
  for (const list of places.values()) {
    for (const a of list) if (elsewhere(list, a.pn, a.y)) running.add(`${a.pn}:${a.i}`);
  }
  return running;
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
  const texts = chain.map((t) => blockAt(t).src.replace(TOKEN, (_, k) => {
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
// the two pages comes first, and fills both. `leave(pn, i)`, when given, hears
// each block of a reference list (pdf/pageLists.js) still without a
// translation as its page is planned, and the block goes unplanned; without
// it the list is planned like the rest. No paragraph joins a left block.
export function createPlanner({ paras, inJob = () => true, filled = () => false, leave = null }) {
  const flows = new Map();
  const claimed = new Set();
  // Where each short text stands on the pages read so far (notePlaces).
  const places = new Map();
  const id = (t) => `${t.pn}:${t.i}`;
  const flowOf = (pn) => {
    if (!flows.has(pn)) {
      const p = paras(pn);
      if (!p) return null;
      const f = pageFlow(p, pageList(p));
      f.heads = sectionsOf(f);
      notePlaces(places, pn, p);
      flows.set(pn, f);
    }
    return flows.get(pn);
  };
  const blockAt = (t) => flowOf(t.pn).paras[t.i];
  // The block before or after `t` in the body flow, across a page break
  // into a segmented page, past pages with no flow (a full-page figure).
  const step = (t, dir) => {
    const f = flowOf(t.pn);
    const k = f.pos.get(t.i);
    if (k === undefined) return null;
    if (f.flow[k + dir] !== undefined) return { pn: t.pn, i: f.flow[k + dir] };
    let pn = t.pn + dir, other = flowOf(pn);
    while (other && !other.flow.length) { pn += dir; other = flowOf(pn); }
    if (!other) return null;
    return { pn, i: dir > 0 ? other.flow[0] : other.flow[other.flow.length - 1] };
  };
  const open = (t) => t && inJob(t.pn) && !claimed.has(id(t)) && !filled(t.pn, t.i);
  const listed = (t) => inList(t.i, flowOf(t.pn).list, flowOf(t.pn - 1)?.list, flowOf(t.pn + 1)?.list);
  const free = (t) => open(t) && !(leave && listed(t));
  // Does `a` go on in `b`? A heading that wraps does. Otherwise only across
  // a column or page break: two blocks of one column are two paragraphs (the
  // segmenter split them for a reason). Down the same x-column (past a
  // figure or a display equation that cut the page across) the indent test
  // says nothing on a page whose paragraphs are set apart by space instead,
  // so B has to start in lower case there. Body size is a's page's; a page
  // break needs both pages to agree on it.
  const joins = (a, b) => {
    const fa = flowOf(a.pn), fb = flowOf(b.pn);
    const A = blockAt(a), B = blockAt(b);
    if (a.pn === b.pn && headingWraps(A, B, fa)) return true;
    if (a.pn === b.pn && fa.col[a.i] === fa.col[b.i]) return false;
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
  // Text in the same place on another page is a running head. The pages
  // read so far tell, and they lie on both sides of a page being planned.
  const repeats = (t) => {
    const b = blockAt(t);
    return elsewhere(places.get(headKey(b)), t.pn, b.y1);
  };
  // The heading the unit sits under: the nearest section heading before it in
  // the flow, over earlier pages as far as they are segmented. A numbered
  // reference reads as a heading and is none, and a block outside the flow
  // (a caption, which reads last) sits under what stands last on its page.
  const sectionOf = (t) => {
    const first = flowOf(t.pn).pos.get(t.i) ?? Infinity;
    for (let pn = t.pn, f = flowOf(pn), before = first; f && pn >= 1; f = flowOf(--pn), before = Infinity) {
      for (const { at, i, text } of [...f.heads].reverse()) {
        if (at < before && !listed({ pn, i }) && !repeats({ pn, i })) return text;
      }
    }
    return "";
  };
  // The unit of block `t`: its paragraph, walked back to where it starts (a
  // page planned before the one it starts on), then forward from there,
  // within the size cap counted from the block being planned, its blocks
  // claimed. `joinable(t)` says whether a block may be part of it. A block
  // that translates nothing of itself (a figure's label) is a unit alone.
  const unitOf = (t, joinable) => {
    let chain = [t];
    if (blockAt(t).translate) {
      let start = t, chars = blockAt(t).text.length;
      for (let k = 1; k < MAX_CHAIN; k++) {
        const prev = step(start, -1);
        if (!joinable(prev) || !joins(prev, start)) break;
        chars += blockAt(prev).text.length;
        if (chars > MAX_UNIT_CHARS) break;
        start = prev;
      }
      chain = [start];
      let size = blockAt(start).text.length;
      while (chain.length < MAX_CHAIN) {
        const next = step(chain[chain.length - 1], 1);
        if (!joinable(next) || !joins(chain[chain.length - 1], next)) break;
        size += blockAt(next).text.length;
        if (size > MAX_UNIT_CHARS) break;
        chain.push(next);
      }
      if (!chain.some((c) => c.pn === t.pn && c.i === t.i)) chain = [t];
    }
    chain.forEach((c) => claimed.add(id(c)));
    const unit = makeUnit(chain, blockAt);
    unit.before = around(chain[0], -1);
    unit.after = around(chain[chain.length - 1], 1);
    unit.section = sectionOf(chain[0]);
    return unit;
  };
  return {
    planPage(pn) {
      const f = flowOf(pn);
      if (!f) return [];
      const units = [];
      for (const i of f.order) {
        const t = { pn, i };
        if (!f.paras[i].translate || !open(t)) continue;
        if (leave && listed(t)) {
          leave(pn, i);
          continue;
        }
        units.push(unitOf(t, free));
      }
      return units;
    },
    // The unit of block `i` on page `pn` alone, as planPage plans it but for
    // what the reader asked for by hand ("Translate this paragraph", Translate
    // again): a block of a reference list too, the list check aside, and a
    // block of a figure's text, which planPage never plans, as a unit of its
    // own. A block that is no text to translate, or one claimed already, has
    // none: null.
    planBlock(pn, i) {
      const f = flowOf(pn);
      const p = f?.paras[i];
      if (!translatable(p) || claimed.has(id({ pn, i }))) return null;
      return unitOf({ pn, i }, open);
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
// (whether the server may keep the translations in the workspace), `fresh`
// (the reader asked for the texts afresh: the server's memory and kept
// translations are not read, and the answer replaces them) and the chunk's
// `before`, `after` and `section`. The viewer's onTranslate and the bench both
// send these, so the bench measures the requests the viewer makes.
export function requestFields({ pageId = "", head = "", waitBrief = true, keep = true, fresh = false, before = "", after = "", section = "" } = {}) {
  const out = { wait_brief: waitBrief !== false, keep: keep !== false };
  if (fresh) out.fresh = true;
  if (pageId) out.page_id = pageId;
  if (head) out.head = head;
  if (before) out.before = before;
  if (after) out.after = after;
  if (section) out.section = section;
  return out;
}
