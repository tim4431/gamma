// Result-row text for the workspace search (SearchPanel.jsx): a note's
// markdown as one plain line, and the ranges of that text a query matched.
// Pure, so node tests it (tests/searchSnippets.test.mjs).
//
// Matching goes through the same normalized view the viewer and the
// backend search use (shared/lib/textnorm.js: ligatures, hyphenated line
// breaks, digit separators), and each match is mapped back to the source
// characters it came from. A match that cannot be mapped cleanly is left
// unmarked: no mark beats a wrong one.

import { buildSearchRegex, normalizeChars, normalizeQuery } from "../shared/lib/textnorm.js";
import { scanImageSyntax } from "../editor/mdMarks.js";

// A note's markdown → the words a reader sees, as one line: fence lines,
// heading hashes, quote and callout markers (nested ones too), list bullets
// and to-do boxes go; [[refs]] and ![[embeds]] keep their alias or target,
// links their text, images their alt text (without a `|300` or Logseq
// `{:width 300}` size); inline HTML tags (the colour spans, <mark>, <br>)
// go and their content stays; emphasis, highlight, strike and inline-code
// marks go. Math: by default the $ delimiters go and the TeX stays (search
// rows, the [[ picker). `mathApart` is the chat chip's prose run
// (chat/chipText.js): the caller cut the math out already, so any $ left is
// literal — \$ reads as $ — and the run is not trimmed (a space beside a
// formula matters).
export function plainSnippet(src, { mathApart = false } = {}) {
  let s = String(src || "");
  s = s.replace(/^[ \t]*```[^\n]*$/gm, " ");
  s = s.replace(/^[ \t]*(?:>[ \t]*)+\[![\w-]+\][+-]?[ \t]*/gm, "");
  s = s.replace(/^[ \t]*(?:>[ \t]*)+/gm, "");
  s = s.replace(/^[ \t]{0,3}#{1,6}[ \t]+/gm, "");
  s = s.replace(/^[ \t]*(?:[-*+]|\d+[.)])[ \t]+(?:\[[ xX]\][ \t]+)?/gm, "");
  s = s.replace(/!?\[\[([^\]|]*)(?:\|([^\]]*))?\]\]/g, (_, target, alias) => alias || target);
  s = withoutImages(s);
  s = s.replace(/\[([^\]]*)\]\([^)]*\)/g, "$1");
  s = s.replace(/<br\s*\/?>/gi, " ");
  s = s.replace(/<\/?[a-z][a-z0-9-]*(?:\s[^<>]*)?\/?>/gi, "");
  s = s.replace(/(\*\*|__|~~|==)(?=\S)([\s\S]*?\S)\1/g, "$2");
  s = s.replace(/(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])/g, "$1");
  s = s.replace(/(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_])/g, "$1");
  s = s.replace(/`([^`\n]*)`/g, "$1");
  if (mathApart) return s.replace(/\\\$/g, "$").replace(/\s+/g, " ");
  s = s.replace(/(?<!\\)\$\$?([^$\n]+?)\$\$?/g, "$1");
  return s.replace(/\s+/g, " ").trim();
}

// Every image (the notes' own scanner, editor/mdMarks.js) → its alt text.
function withoutImages(s) {
  let out = "", at = 0;
  for (const im of scanImageSyntax(s)) {
    out += s.slice(at, im.from) + im.alt;
    at = im.to;
  }
  return out + s.slice(at);
}

// [start, end) UTF-16 ranges of `text` that `query` matches, merged and in
// order. The whole phrase first; when it appears nowhere (the library's PDF
// index matches words scattered over a page, the title scorer takes terms
// in any order) each term on its own. A regex match that does not start
// and end on a normalized character is skipped.
export function matchRanges(text, query, { caseSensitive = false, wholeWord = false } = {}) {
  if (!text || !normalizeQuery(query)) return [];
  const cps = Array.from(text);
  const offs = [];
  let o = 0;
  for (const c of cps) { offs.push(o); o += c.length; }
  offs.push(o);
  const { norm, src } = normalizeChars(cps.map((ch) => ({ ch })));
  const hay = norm.join("");
  const at = new Map(); // hay offset → normalized index
  let h = 0;
  norm.forEach((c, k) => { at.set(h, k); h += c.length; });
  at.set(h, norm.length);
  const find = (re) => {
    const out = [];
    if (!re) return out;
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(hay))) {
      if (!m[0]) { re.lastIndex++; continue; }
      const k0 = at.get(m.index), k1 = at.get(m.index + m[0].length);
      if (k0 == null || k1 == null || k1 <= k0) continue;
      out.push([offs[src[k0]], offs[src[k1 - 1] + 1]]);
    }
    return out;
  };
  const opts = { caseSensitive, wholeWord };
  let ranges = find(buildSearchRegex(query, opts));
  if (!ranges.length) {
    const terms = normalizeQuery(query).split(" ").filter((term) => term.length > 1);
    if (terms.length > 1) ranges = terms.flatMap((term) => find(buildSearchRegex(term, opts)));
  }
  ranges.sort((a, b) => a[0] - b[0]);
  const merged = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) last[1] = Math.max(last[1], r[1]);
    else merged.push([...r]);
  }
  return merged;
}

// `text` cut into [{text, mark}] pieces for rendering. With `lead`, a line
// whose first mark sits further in than that starts a little before it
// (at a word boundary, behind "…"), so a one-line row shows the match.
export function markedParts(text, query, opts = {}, lead = 0) {
  let s = String(text || "");
  let ranges = matchRanges(s, query, opts);
  if (lead && ranges.length && ranges[0][0] > lead) {
    let cut = ranges[0][0] - Math.floor(lead / 2);
    const space = s.indexOf(" ", cut);
    if (space >= 0 && space < ranges[0][0]) cut = space + 1;
    s = `…${s.slice(cut)}`;
    ranges = ranges.map(([a, b]) => [a - cut + 1, b - cut + 1]);
  }
  const parts = [];
  let pos = 0;
  for (const [a, b] of ranges) {
    if (a > pos) parts.push({ text: s.slice(pos, a), mark: false });
    parts.push({ text: s.slice(a, b), mark: true });
    pos = b;
  }
  if (pos < s.length) parts.push({ text: s.slice(pos), mark: false });
  return parts;
}
