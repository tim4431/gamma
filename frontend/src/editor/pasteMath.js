// Math pasted as \( … \) and \[ … \], as ChatGPT and LaTeX write it, goes
// into a note as the $ … $ and $$ … $$ notes render (remark-math and
// scanMathSpans read only dollars). It is converted on the way in, never on
// screen: in Markdown "\[" is also an escaped bracket, and Gamma writes those
// itself (text_box.escape_markdown, zotero_notes.escape: "\[n = 30\]"), so
// a \[ … \] converts only when it is shaped as a formula is written.
// backend/gamma/paste_math.py applies the same rules to what the AI writes;
// tests/shared/pastemath.json pins both. Pure: no DOM, no React.
import { findMathAtCursor } from "./latexCompletion.js";
import { escapedAt } from "./latexInput.js";

const DELIM = /\\[[\]()]/g;
const ESCAPED = /\\[!-/:-@[-`{-~]/g;
// A code fence's opening line, after the quote marks and list marker
// Markdown allows in front (as diagramMarkdown.mapOutsideCodeFences reads it).
const FENCE = /^(?:[ \t]*>)*(?:[ \t]*(?:[-+*]|\d+[.)])[ \t]+)?[ \t]*(`{3,}|~{3,})([^\r\n]*)/;
// All a line may hold before a \[ that stands alone on it.
const LINE_PREFIX = /^(?:[ \t]*>)*[ \t]*(?:(?:[-+*]|\d+[.)])[ \t]+)?$/;
// Markdown inside brackets, no TeX: a link, an address, bold, a ==mark==, a tag.
const MARKDOWN = /\]\(|:\/\/|\*\*|==|<\/?[a-zA-Z]/;
// Han, kana and Hangul, which set a formula against the text without a space.
const CJK = /[ᄀ-ᇿ⺀-⿟々-〇〡-〩〸-〻぀-ヿ㄰-㆏ㇰ-ㇿ㐀-䶿一-鿿ꥠ-꥿가-퟿豈-﫿ｦ-ￜ\u{20000}-\u{3ffff}]/u;
const LETTER = /[\p{L}\p{N}]/u;
const TEXT_DOLLAR = "\\text{\\textdollar}";

// A letter or digit of a script that writes words with spaces.
const isWord = (c) => LETTER.test(c) && !CJK.test(c);
// The character before (at) `at`, read as a whole code point; a space past the ends.
const charBefore = (s, at) => (at < 1 ? " " : String.fromCodePoint(s.codePointAt(/[\udc00-\udfff]/.test(s[at - 1]) && at >= 2 ? at - 2 : at - 1)));
const charAt = (s, at) => (at >= s.length ? " " : String.fromCodePoint(s.codePointAt(at)));

// The code fences in `text`: `from` the opening line's start, `inner` its
// end, `to` the closing line's start and `end` its end (the text's end
// while it is open). Quoted, listed and ~~~ fences count.
function fences(text) {
  const out = [];
  let open = null, off = 0;
  for (const line of text.split("\n")) {
    const m = FENCE.exec(line);
    if (open) {
      if (m && m[1][0] === open.ticks[0] && m[1].length >= open.ticks.length && !m[2].trim()) {
        out.push({ from: open.from, inner: open.inner, to: off, end: off + line.length });
        open = null;
      }
    } else if (m && !(m[1][0] === "`" && m[2].includes("`"))) {
      open = { from: off, inner: off + line.length, ticks: m[1] };
    }
    off += line.length + 1;
  }
  if (open) out.push({ from: open.from, inner: open.inner, to: text.length, end: text.length });
  return out;
}

// The inline code spans in `text`, [from, to): a run of backticks to the
// next run as long on its line (CommonMark's rule, kept to one line), found
// in one pass.
function codeSpans(text) {
  const spans = [];
  let off = 0;
  for (const line of text.split("\n")) {
    const runs = [...line.matchAll(/`+/g)];
    const next = new Array(runs.length).fill(-1), last = new Map();
    for (let k = runs.length - 1; k >= 0; k--) {
      next[k] = last.get(runs[k][0].length) ?? -1;
      last.set(runs[k][0].length, k);
    }
    for (let k = 0; k < runs.length; k++) {
      if (next[k] < 0) continue;
      const close = runs[next[k]];
      spans.push([off + runs[k].index, off + close.index + close[0].length]);
      k = next[k];
    }
    off += line.length + 1;
  }
  return spans;
}

// `text` with `fn` applied to what lies outside its fences and code spans.
function mapProse(text, fn) {
  let out = "", pos = 0;
  for (const f of fences(text)) {
    out += outsideCode(text.slice(pos, f.from), fn) + text.slice(f.from, f.end);
    pos = f.end;
  }
  return out + outsideCode(text.slice(pos), fn);
}

function outsideCode(s, fn) {
  let out = "", pos = 0;
  for (const [from, to] of codeSpans(s)) {
    out += fn(s.slice(pos, from)) + s.slice(from, to);
    pos = to;
  }
  return out + fn(s.slice(pos));
}

// Whether a position in `s` is inside a web address or a link's target,
// for positions asked in increasing order (it reads `s` once). An address
// runs from "://" or a "www." to the next space, a link's target from "]("
// to its balanced ")", an autolink from "<scheme:" to ">". `skip(to)` steps
// over a formula, which ends an address.
function urlScanner(s) {
  let i = 0, mode = null, depth = 0;
  const inUrl = (at) => {
    for (; i < at; i++) {
      const c = s[i];
      if (mode && /\s/.test(c)) mode = null;
      else if (mode === "target") {
        if (c === "(" && !escapedAt(s, i)) depth++;
        else if (c === ")" && !escapedAt(s, i) && --depth === 0) mode = null;
      } else if (mode === "auto") {
        if (c === ">") mode = null;
      } else if (!mode) {
        if (c === "(" && s[i - 1] === "]") [mode, depth] = ["target", 1];
        else if ((c === "/" && s[i - 1] === "/" && s[i - 2] === ":")
          || (c === "." && i >= 3 && s.slice(i - 3, i) === "www" && !isWord(charBefore(s, i - 3)))) mode = "address";
        else if (c === "<" && /^[a-zA-Z][a-zA-Z0-9+.-]{1,31}:/.test(s.slice(i + 1, i + 34))) mode = "auto";
      }
    }
    return mode !== null;
  };
  inUrl.skip = (to) => { i = Math.max(i, to); mode = null; };
  return inUrl;
}

// The \( … \) and \[ … \] pairs as written in `s`, matched opener to closer;
// a second opener before the close starts afresh.
function* rawPairs(s) {
  let open = null;
  for (const m of s.matchAll(DELIM)) {
    if (escapedAt(s, m.index)) continue;
    const c = m[0][1];
    if (c === "[" || c === "(") open = { at: m.index, display: c === "[" };
    else if (open && open.display === (c === "]")) {
      yield { from: open.at, to: m.index + 2, inner: s.slice(open.at + 2, m.index), display: open.display };
      open = null;
    }
  }
}

// The unescaped dollars in `tex`.
const bareDollars = (tex) => [...tex.matchAll(/\$/g)].filter((m) => !escapedAt(tex, m.index)).length;

// A display pair is shaped as a formula is written, not as a bracketed
// phrase ("\[1\]", "\[n = 30\]", "\[x\](y)", "\[ see appendix \]"): alone on
// lines of its own, or holding a \command (an escaped backslash is none), or
// padded with spaces as ChatGPT writes it ("\[ E = mc^2 \]") around a sign
// only math uses and no Markdown of its own (another app's "Copy as
// Markdown" escapes "[ Reply ]" so too); never empty or across a blank line.
// Gamma writes a literal bracket as "\[x]" (text_box.escape_markdown), which
// has no closer to pair; an older note's "\[x\]" is unpadded.
function displayFormula(s, p) {
  if (s[p.to] === "(" || !p.inner.trim() || /\n[ \t]*\n/.test(p.inner)) return false;
  const lineStart = s.lastIndexOf("\n", p.from - 1) + 1;
  if (/^[ \t]*\n/.test(p.inner) && /\n[ \t>]*$/.test(p.inner) && LINE_PREFIX.test(s.slice(lineStart, p.from))) return true;
  const tex = p.inner.replace(ESCAPED, "");
  return /\\[a-zA-Z]/.test(tex)
    || (/^[ \t]/.test(p.inner) && /[ \t]$/.test(p.inner) && /[\^_={}]/.test(tex) && !MARKDOWN.test(p.inner));
}

// The pairs in a stretch of prose that convert: none inside a dollar
// formula it already holds (`formulas`, in order), part of an address, or
// one the text read already held. An inline one keeps to its line and holds
// no math of its own in dollars.
function pairsIn(s, kept, formulas) {
  const pairs = [];
  const inUrl = urlScanner(s);
  let k = 0;
  for (const p of rawPairs(s)) {
    while (k < formulas.length && formulas[k][1] <= p.from) k++;
    if ((k < formulas.length && formulas[k][0] < p.from) || inUrl(p.from) || kept.has(s.slice(p.from, p.to))) continue;
    if (!(p.display ? displayFormula(s, p) : p.inner.trim() && !p.inner.includes("\n") && bareDollars(p.inner) < 2)) continue;
    pairs.push(p);
    inUrl.skip(p.to);
  }
  return pairs;
}

// A line that ends a paragraph or starts a block of its own: blank, a list
// item, a heading, a table row.
const BLOCK_START = /^[ \t>]*(?:\n|$|(?:[-+*]|\d+[.)])[ \t]|#{1,6}[ \t]|\|)/;

// The next unescaped dollar after `at` in its paragraph (a formula may
// wrap, as LaTeX source does, but not into another block), or -1.
function nextDollar(s, at) {
  for (let j = at + 1; j < s.length; j++) {
    if (s[j] === "\n" && BLOCK_START.test(s.slice(j + 1, j + 64))) return -1;
    if (s[j] === "$" && !escapedAt(s, j)) return j;
  }
  return -1;
}

// Whether a lone "$" at `at` opens a dollar formula that closes at `close`,
// by Pandoc's rule made strict for a text that writes "$" as money: the
// opener before a non-space and no closing mark ("($)"), after no letter or
// digit; the closer lone, after a non-space (or an escaped one) and before
// no letter or digit. Han, kana and Hangul are no such letters: they set a
// formula without spaces.
function opensMath(s, at, close) {
  if (close < 0 || /[\s)\]},.;:!?]/.test(charAt(s, at + 1)) || isWord(charBefore(s, at))) return false;
  return s[close + 1] !== "$" && (!/\s/.test(s[close - 1]) || escapedAt(s, close - 1)) && !isWord(charAt(s, close + 1));
}

// The dollar formulas `s` holds, [from, to) in order: a $$ … $$, and a lone
// $ … $ that opensMath or `kept` takes for one; and its other lone dollars.
function dollarsIn(s, kept) {
  const formulas = [], lone = [];
  const runs = /\$+/g;
  let display = -1;
  for (let m; (m = runs.exec(s));) {
    if (escapedAt(s, m.index)) continue;
    if (m[0].length > 1) {
      if (display < 0) display = m.index;
      else {
        formulas.push([display, m.index + m[0].length]);
        display = -1;
      }
    } else if (display < 0) {
      const close = nextDollar(s, m.index);
      if (opensMath(s, m.index, close) || (close >= 0 && kept.has(s.slice(m.index, close + 1)))) {
        formulas.push([m.index, close + 1]);
        runs.lastIndex = close + 1;
      } else lone.push(m.index);
    }
  }
  return { formulas, lone };
}

// A text that writes its formulas with backslashes writes "$" as a dollar
// ("$5", "($)", "US$"): escaped, so it can't pair with a converted formula's.
// A dollar formula of its own ("$2x$") or one the text read already held, a
// "$$" and a dollar in an address keep theirs.
function escapeDollars(s, kept) {
  let out = "", pos = 0;
  const inUrl = urlScanner(s);
  for (const at of dollarsIn(s, kept).lone) {
    if (inUrl(at)) continue;
    out += s.slice(pos, at) + "\\$";
    pos = at + 1;
  }
  return out + s.slice(pos);
}

// A formula's dollars, which remark-math would end it at: a pair of them
// in a display formula (math in its \text{…}) as \( … \), any other as
// KaTeX's text dollar.
function texDollars(tex, display) {
  const bare = bareDollars(tex), paired = display ? bare - (bare % 2) : 0;
  let n = 0;
  return tex.replace(/(\\*)\$/g, (m, bs) => {
    if (bs.length % 2) return bs.slice(1) + TEXT_DOLLAR;
    const k = n++;
    return bs + (k < paired ? (k % 2 ? "\\)" : "\\(") : TEXT_DOLLAR);
  });
}

// The formula a pair becomes; inline math loses its padding. A control
// space ("\ ") ending a one-line formula keeps its space: the trim takes the
// inline one's, and remark-math one off each side of a padded display one.
function formulaOf(p) {
  const tex = texDollars(p.display ? p.inner : p.inner.trim(), p.display);
  const tail = tex.trimEnd();
  const space = escapedAt(tail, tail.length) && !tex.includes("\n") ? " " : "";
  return p.display ? `$$${tex}${space}$$` : `$${tex}${space}$`;
}

function convert(s, kept, dollars) {
  const fix = dollars ? (gap) => escapeDollars(gap, kept) : (gap) => gap;
  let out = "", pos = 0;
  for (const p of pairsIn(s, kept, dollarsIn(s, kept).formulas)) {
    const gap = fix(s.slice(pos, p.from));
    let formula = formulaOf(p);
    // A formula touching another dollar would read as "$$": a space between.
    if (gap ? gap.endsWith("$") : pos > 0) formula = " " + formula;
    if (s[p.to] === "$") formula += " ";
    out += gap + formula;
    pos = p.to;
  }
  return out + fix(s.slice(pos));
}

// The \( … \) pairs and dollar spans `text` holds in its prose, as written:
// what a rewrite of it keeps.
function keptIn(text) {
  const kept = new Set();
  if (text) mapProse(text, (s) => {
    for (const p of rawPairs(s)) kept.add(s.slice(p.from, p.to));
    for (const m of s.matchAll(/\$/g)) {
      const close = escapedAt(s, m.index) ? -1 : nextDollar(s, m.index);
      if (close >= 0) kept.add(s.slice(m.index, close + 1));
    }
    return s;
  });
  return kept;
}

// `text` with its \( … \) and \[ … \] formulas as $ … $ and $$ … $$, in
// place (each line keeps its quote marks and indent), outside code. What
// `keep` already holds as written stays (an AI rewrite of the text it read).
export function dollarMath(text, keep = "") {
  const kept = keptIn(keep);
  const plain = mapProse(text, (s) => convert(s, kept, false));
  return plain === text ? text : mapProse(text, (s) => convert(s, kept, true));
}

// What a paste at `pos` in `doc` puts in: `text` converted, unless it lands
// in source — a code fence, inline code or math — where it stays as copied.
export function pasteMath(doc, pos, text) {
  const fenced = fences(doc), spans = codeSpans(doc);
  if (fenced.some((f) => pos >= f.inner && pos <= f.to) || spans.some(([a, b]) => pos > a && pos < b)) return text;
  // Dollars in code are no math: blanked before asking.
  const code = [...fenced.map((f) => [f.from, f.end]), ...spans].sort((x, y) => x[0] - y[0]);
  let prose = "", at = 0;
  for (const [a, b] of code) {
    if (a < at) continue;
    prose += doc.slice(at, a) + doc.slice(a, b).replace(/[^\n]/g, " ");
    at = b;
  }
  return findMathAtCursor(prose + doc.slice(at), pos) ? text : dollarMath(text);
}
