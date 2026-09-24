// Where a click on a block's RENDERED markdown lands in its raw source, so
// the editor that replaces the rendered view opens with the caret on the
// clicked character. The two layouts differ too much for screen coordinates
// (images, tables, math and hidden markup change every line's height), so
// the click is located by text instead: the clicked text node's characters
// around the click are searched for in the source, and when they occur more
// than once, the rendered text on either side (matched loosely, skipping
// markup the source has and the rendered view doesn't) picks the occurrence.
// The same lookup finds where a rendered block begins, for the hover line in
// the gap between two blocks that opens the editor on a line between them.
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

// The context for locateInSource of a click at (x, y) inside `container`
// (a block's rendered view), or null when it didn't hit its text.
export function renderedClickContext(container, x, y) {
  const hit = caretAt(x, y);
  return hit ? contextAt(container, hit.node, hit.offset) : null;
}

// The source offset a click on the rendered view points at, or null.
export function sourceOffsetAtPoint(container, source, x, y) {
  if (!container) return null;
  return locateInSource(source, renderedClickContext(container, x, y));
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
// `half` its hover reach, `below` the block under it.
export function renderedGaps(container, skip) {
  const kids = [...container.children].filter((k) => k !== skip && k.getClientRects().length);
  const gaps = [];
  for (let i = 1; i < kids.length; i++) {
    const a = kids[i - 1].getBoundingClientRect(), b = kids[i].getBoundingClientRect();
    gaps.push({ y: (a.bottom + b.top) / 2, half: Math.max(4, (b.top - a.bottom) / 2), below: kids[i] });
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
