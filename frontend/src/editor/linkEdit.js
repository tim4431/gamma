// The links of a note's text as the link card (shared/ui/LinkCard.jsx) reads
// and writes them: where each one is in the source, and the Markdown a
// change makes. Pure, so node tests it (tests/linkEdit.test.mjs).
//
// A link is one of
//   - a page or note link, [[id]] or [[id|words]] (refLabels.js REF_RE; an
//     ![[embed]] is no link),
//   - a Markdown link, [label](href) (an image's ![…](…) is none),
//   - a bare web address (https://…, as GFM links one) or <https://…>,
// outside code and math, where nothing is a link.
import { protectedSpans } from "./mdScan.js";
import { CHIP_RE, refWords } from "./refLabels.js";
import { parseGammaLink } from "../shared/model/gammaLinks.js";

// [label](href "title"): a label of balanced brackets one level deep, an
// escape kept whole; the target in angle brackets, or holding a "(" only as
// one of a balanced pair, as on screen (note_markup.py LINK_TARGET).
const MD_LINK_RE = /(!?)\[((?:[^[\]\\\n]|\\.|\[[^[\]\n]*\])*)\]\(\s*(<[^<>\n]*>|(?:[^()\s]|\([^()\s]*\))+)(?:\s+"[^"\n]*")?\s*\)/g;
// A bare address and an angle-bracket one. GFM's trailing punctuation and an
// unbalanced ")" are taken off a bare one (bareEnd).
const BARE_RE = /<(https?:\/\/[^\s<>]+)>|https?:\/\/[^\s<>]+/gi;

// A bare address's end as GFM links it: without the punctuation that ends
// a sentence, and without a ")" no "(" in it opens.
function bareEnd(url) {
  let end = url.length;
  for (;;) {
    const c = url[end - 1];
    if ("?!.,:*_~'\"".includes(c)) { end -= 1; continue; }
    if (c === ")") {
      const s = url.slice(0, end);
      if ((s.match(/\(/g) || []).length < (s.match(/\)/g) || []).length) { end -= 1; continue; }
    }
    break;
  }
  return url.slice(0, end);
}

const inside = (spans, from, to) => spans.some((s) => from < s.to && to > s.from);

// Every link of `content`, in source order: {from, to, kind, ...}
//   ref: {id, words}  — words "" for none
//   md:  {label, href} — href as written, without its angle brackets
//   bare: {href}
export function linkSpans(content) {
  const text = String(content || "");
  const hidden = protectedSpans(text);
  const out = [];
  const taken = [];
  const take = (span) => {
    if (inside(hidden, span.from, span.to) || inside(taken, span.from, span.to)) return;
    taken.push(span);
    out.push(span);
  };
  for (const m of text.matchAll(CHIP_RE)) {
    const from = m.index, to = from + m[0].length;
    if (m[0][0] === "!") { taken.push({ from, to }); continue; }   // an embed: no link, nor anything in it
    take({ from, to, kind: "ref", id: m[1], words: refWords(m[2]) });
  }
  for (const m of text.matchAll(MD_LINK_RE)) {
    const from = m.index, to = from + m[0].length;
    if (m[1]) { taken.push({ from, to }); continue; }              // an image
    take({ from, to, kind: "md", label: m[2], href: m[3].replace(/^<|>$/g, "") });
  }
  for (const m of text.matchAll(BARE_RE)) {
    // GFM links a bare address at a line's start or after a space, *, _, ~ or (.
    if (!m[1] && m.index > 0 && !/[\s*_~(]/.test(text[m.index - 1])) continue;
    const href = m[1] || bareEnd(m[0]);
    const from = m.index, to = from + (m[1] ? m[0].length : href.length);
    take({ from, to, kind: "bare", href });
  }
  return out.sort((a, b) => a.from - b.from);
}

// The link the caret at `pos` is on (its ends included), or null.
export function linkSpanAt(content, pos) {
  return linkSpans(content).find((s) => s.from <= pos && pos <= s.to) || null;
}

// What the rendered view's link points to, as its <a> gets it: `ref:<id>`
// for a page or note link, else `url:` and the href through `transform` (the
// renderer's urlTransform). The nth rendered link of a key is the nth span
// of that key, both in document order.
export function linkKey(span, transform = (u) => u) {
  return span.kind === "ref" ? `ref:${span.id}` : `url:${transform(span.href)}`;
}

export function nthLinkSpan(content, key, n, transform) {
  let seen = 0;
  for (const span of linkSpans(content)) {
    if (linkKey(span, transform) !== key) continue;
    if (seen === n) return span;
    seen += 1;
  }
  return null;
}

// --- writing ----------------------------------------------------------------

// The pipe a page link's words take at `at`: "\|" on a table's row, whose
// cells a bare one would split (note_markup.py table_pipe).
export function tablePipe(content, at) {
  const text = String(content || "");
  return text.slice(text.lastIndexOf("\n", at - 1) + 1, at).trimStart().startsWith("|") ? "\\|" : "|";
}

// Words as a [[ref]]'s: one line, no brackets (they would end it).
const refText = (words) => (words || "").replace(/[[\]]/g, "").replace(/\s+/g, " ").trim();
// Words as a Markdown link's label: one line, a bracket escaped, and a
// backslash ending it doubled, so it never escapes the "]".
const labelText = (words) => (words || "").replace(/\s+/g, " ").trim()
  .replace(/(?<!\\)([[\]])/g, "\\$1").replace(/(?<!\\)(\\\\)*\\$/, "$&\\");
// An address as a link's target: in angle brackets when it holds a space,
// an angle bracket or an unbalanced parenthesis.
function target(url) {
  const opens = (url.match(/\(/g) || []).length, shuts = (url.match(/\)/g) || []).length;
  return /[\s<>]/.test(url) || opens !== shuts ? `<${url.replace(/[<>]/g, encodeURIComponent)}>` : url;
}
// Whether an address written bare reads back whole as one link.
const bareSafe = (url) => /^https?:\/\/[^\s<>]+$/i.test(url) && bareEnd(url) === url;

// The Markdown of a link to `to` showing `words`:
//   {kind: "block", id} → [[id]] or [[id|words]] (`pipe` "\|" in a table's row)
//   {kind: "url", url}  → [words](url), or the address alone without words
export function linkMarkdown(to, words, pipe = "|") {
  if (to.kind === "block") {
    const w = refText(words);
    return w ? `[[${to.id}${pipe}${w}]]` : `[[${to.id}]]`;
  }
  const url = (to.url || "").trim();
  const label = labelText(words);
  if (label) return `[${label}](${target(url)})`;
  if (bareSafe(url)) return url;
  return /^https?:\/\//i.test(url) && !/[\s<>]/.test(url) ? `<${url}>` : `[${labelText(url)}](${target(url)})`;
}

// What a span links to, as linkMarkdown takes it.
export const spanTarget = (span) => (span.kind === "ref" ? { kind: "block", id: span.id } : { kind: "url", url: span.href });

// A link's words as the card's Text field holds them: a page link's own
// words, a Markdown link's label with its escapes read; "" for none.
export function spanWords(span) {
  if (span.kind === "ref") return span.words;
  if (span.kind === "md") return span.label.replace(/\\([[\]\\])/g, "$1");
  return "";
}

// `content` with the link `span` changed: {words, to} (`to` null keeps its
// target), or {remove: true, shown} — the words left as text, `shown` (what
// the chip showed) for a page link of none. A bare address can't be
// unlinked: null.
export function applyLinkChange(content, span, change) {
  const text = String(content || "");
  let insert;
  if (change.remove) {
    if (span.kind === "bare") return null;
    insert = span.kind === "ref" ? (span.words || change.shown || "") : spanWords(span);
  } else {
    insert = linkMarkdown(change.to || spanTarget(span), change.words, tablePipe(text, span.from));
  }
  return text.slice(0, span.from) + insert + text.slice(span.to);
}

// Words selected and an address pasted over them: the link to it showing
// them, or null to paste as usual — no words, words over more than one line
// or holding a link already, or a clipboard that is not one address. A
// copied link to a page or a note of this library (`origin`) is that page
// link, [[id|words]]; a citation stays its address.
export function pastedLink(selected, clip, origin, pipe = "|") {
  const words = String(selected || "");
  const url = String(clip || "").trim();
  if (!words.trim() || words.includes("\n") || /\[\[|\]\(|https?:\/\//i.test(words)) return null;
  if (!/^https?:\/\/\S+$/i.test(url)) return null;
  const gl = parseGammaLink(url, origin);
  if (gl && !gl.foreign && (gl.kind === "page" || gl.kind === "block")) {
    return linkMarkdown({ kind: "block", id: gl.kind === "block" ? gl.blockId : gl.pageId }, words, pipe);
  }
  return linkMarkdown({ kind: "url", url }, words);
}
