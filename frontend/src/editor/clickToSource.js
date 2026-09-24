// Where a click on a block's RENDERED markdown lands in its raw source, so
// the editor that replaces the rendered view opens with the caret on the
// clicked character. The two layouts differ too much for screen coordinates
// (images, tables, math and hidden markup change every line's height), so
// the click is located by text instead: the clicked text node's characters
// around the click are searched for in the source, and when they occur more
// than once, the rendered text on either side (matched loosely, skipping
// markup the source has and the rendered view doesn't) picks the occurrence.
// The same lookup finds where a rendered block begins, for the hover line in
// the gap between two blocks that opens the editor on a line between them,
// and the source range a Ctrl-selection of rendered text covers, for the
// chat's selection chip.
// `locateInSource` and `gapInSource` are pure; the rest reads the DOM.

const CTX = 40; // characters of context taken on each side of the click
const GAP = 12; // markup characters a context character may skip over

function indexesOf(text, needle) {
  const out = [];
  for (let i = text.indexOf(needle); i >= 0; i = text.indexOf(needle, i + 1)) out.push(i);
  return out;
}

// How many non-space characters of `ctx`, read outward from the candidate
// (backwards when dir = -1, from source index j), match the source in order.
// A context character with no match within GAP source characters is skipped
// (rendered text the source doesn't have: a KaTeX glyph, a chip's label).
function contextScore(source, j, ctx, dir) {
  let score = 0;
  const chars = dir < 0 ? [...ctx].reverse() : [...ctx];
  for (const ch of chars) {
    if (/\s/.test(ch)) continue;
    for (let k = 0; k < GAP; k++) {
      const at = j + dir * k;
      if (at < 0 || at >= source.length) break;
      if (source[at] === ch) { score++; j = at + dir; break; }
    }
  }
  return score;
}

// ctx: { core, anchor, before, after, nth } — `core` is text the source
// should contain verbatim, the click `anchor` characters into it; `before` /
// `after` the rendered text around it; `nth` how often `core` occurs in the
// rendered text before this one (the tie-break). Returns a source offset, or
// null when the text can't be found.
export function locateInSource(source, ctx) {
  if (!source || !ctx?.core) return null;
  const { core, anchor, before = "", after = "", nth = 0 } = ctx;
  // The whole core, else the part before the click, else the part after it.
  const tries = [[0, core.length]];
  if (anchor > 0 && anchor < core.length) tries.push([0, anchor], [anchor, core.length]);
  for (const [s, e] of tries) {
    const needle = core.slice(s, e);
    if (!needle.trim()) continue;
    const hits = indexesOf(source, needle);
    if (!hits.length) continue;
    const pre = (before + core.slice(0, s)).slice(-CTX);
    const post = (core.slice(e) + after).slice(0, CTX);
    let best = 0, bestScore = -1;
    hits.forEach((h, i) => {
      const score = contextScore(source, h - 1, pre, -1) + contextScore(source, h + needle.length, post, 1);
      if (score > bestScore || (score === bestScore && Math.abs(i - nth) < Math.abs(best - nth))) {
        best = i;
        bestScore = score;
      }
    });
    return hits[best] + anchor - s;
  }
  return null;
}

// Text that isn't the note's own: KaTeX's hidden MathML copy, tool buttons.
const SKIP = ".katex-mathml, [data-markdown-copy-ignore], .mdImgTools, style, script";

function caretAt(x, y) {
  if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(x, y);
    return p && { node: p.offsetNode, offset: p.offset };
  }
  const r = document.caretRangeFromPoint?.(x, y);
  return r && { node: r.startContainer, offset: r.startOffset };
}

// The context for locateInSource of a caret at `offset` in text node `node`
// (or anywhere in a KaTeX formula) inside `container`, a block's rendered
// view; null when that isn't the note's own text.
function contextAt(container, node, offset) {
  if (!node || !container.contains(node)) return null;
  const el = node.nodeType === 1 ? node : node.parentElement;
  const math = el?.closest(".katex");
  if (!math && node.nodeType !== 3) return null;

  let text = "", nodeFrom = -1, nodeTo = -1;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.parentElement?.closest(SKIP)) continue;
    const mine = math ? math.contains(n) : n === node;
    if (mine && nodeFrom < 0) nodeFrom = text.length;
    text += n.data;
    if (mine) nodeTo = text.length;
  }
  if (nodeFrom < 0) return null;

  if (math) {
    // Math: its TeX (first line — the rendered copy may have had its line
    // breaks folded) with the caret at its start.
    const tex = (math.querySelector("annotation")?.textContent || "").trim().split("\n")[0].slice(0, CTX);
    return {
      core: tex, anchor: 0,
      before: text.slice(Math.max(0, nodeFrom - CTX), nodeFrom),
      after: text.slice(nodeTo, nodeTo + CTX),
      nth: 0,
    };
  }
  const at = nodeFrom + Math.min(offset, nodeTo - nodeFrom);
  const cs = Math.max(nodeFrom, at - CTX), ce = Math.min(nodeTo, at + CTX);
  const core = text.slice(cs, ce);
  return {
    core, anchor: at - cs,
    before: text.slice(Math.max(0, cs - CTX), cs),
    after: text.slice(ce, ce + CTX),
    nth: core ? indexesOf(text.slice(0, cs + core.length - 1), core).length : 0,
  };
}

// The source offset a click at (x, y) on `container` (a block's rendered
// view) points at, or null when it didn't hit the note's own text.
export function sourceOffsetAtPoint(container, source, x, y) {
  const hit = container && caretAt(x, y);
  return hit ? locateInSource(source, contextAt(container, hit.node, hit.offset)) : null;
}

// ---- gaps between the rendered view's blocks (paragraphs, formulas, lists…)

// Where the gap before the source construct containing offset `at` puts the
// caret: on the blank line just above that construct's first line, or — when
// the line above isn't blank — on a new empty line inserted there (insert:
// true). `spans` are multi-line constructs ([{from, to}]: $$ math, ```
// fences) whose first line is their start, not the line the text was on.
export function gapInSource(source, at, spans = []) {
  const span = spans.find((sp) => at >= sp.from && at < sp.to);
  if (span) at = span.from;
  const start = source.lastIndexOf("\n", at - 1) + 1;
  if (start === 0) return { offset: 0, insert: true };
  const prevStart = source.lastIndexOf("\n", start - 2) + 1;
  if (!source.slice(prevStart, start - 1).trim()) return { offset: prevStart, insert: false };
  return { offset: start, insert: true };
}

// The rendered view's top-level blocks, top to bottom, with the gap between
// each pair: [{y, half, below}] — `y` the gap's middle (client coords),
// `half` its hover reach, `below` the block under it. The reach covers the
// gap plus up to 6px into each block (a third of the shorter one at most, so
// the reaches around a short block — an empty line — never meet).
export function renderedGaps(container, skip) {
  const kids = [...container.children].filter((k) => k !== skip && k.getClientRects().length);
  const gaps = [];
  for (let i = 1; i < kids.length; i++) {
    const a = kids[i - 1].getBoundingClientRect(), b = kids[i].getBoundingClientRect();
    const into = Math.min(6, a.height / 3, b.height / 3);
    gaps.push({ y: (a.bottom + b.top) / 2, half: Math.max(2, (b.top - a.bottom) / 2) + into, below: kids[i] });
  }
  return gaps;
}

// The source offset where the block `below` (a top-level child of the
// rendered view) begins — located by its first text — or null.
export function blockStartInSource(container, source, below) {
  const walker = document.createTreeWalker(below, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (n.parentElement?.closest(SKIP) || !n.data.trim()) continue;
    return locateInSource(source, contextAt(container, n, n.data.length - n.data.trimStart().length));
  }
  return null;
}

// ---- a selection made in the rendered view

// A DOM range boundary as a text node + offset: a boundary before a child
// is the start of that child's first text, one past the last child the end
// of the element's last text.
function textPoint(node, offset) {
  if (node.nodeType === 3) return { node, offset };
  const next = node.childNodes[offset];
  if (next) {
    const t = next.nodeType === 3 ? next : document.createTreeWalker(next, NodeFilter.SHOW_TEXT).nextNode();
    return t ? { node: t, offset: 0 } : null;
  }
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  let last = null;
  for (let t = walker.nextNode(); t; t = walker.nextNode()) last = t;
  return last ? { node: last, offset: last.data.length } : null;
}

// The source range {from, to} a selection inside one block's rendered view
// covers, or null when an end can't be placed. `spans` ([{from, to}]: math)
// are taken whole — an end inside a formula widens to cover it.
export function sourceRangeOfSelection(container, source, range, spans = []) {
  const ends = [textPoint(range.startContainer, range.startOffset), textPoint(range.endContainer, range.endOffset)];
  if (ends.some((p) => !p)) return null;
  const [a, b] = ends.map((p) => locateInSource(source, contextAt(container, p.node, p.offset)));
  if (a == null || b == null) return null;
  let from = Math.min(a, b), to = Math.max(a, b);
  for (const sp of spans) {
    if (from > sp.from && from < sp.to) from = sp.from;
    if (to > sp.from && to < sp.to) to = sp.to;
  }
  return to > from ? { from, to } : null;
}
