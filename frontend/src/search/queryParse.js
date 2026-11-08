// The search box's query language as syntax alone: which tokens are
// clauses and which are text. What a clause means is Gamma's own table,
// search/searchFilters.js. Pure, so node tests it (tests/queryParse.test.mjs).
//
// A query is tokens split by whitespace outside quotes. A token
// `key:value` whose key is one the caller names is a clause; every other
// token is text, an unknown key's (arXiv:2301.12345, doi:…, 10:30)
// included. Keys compare folded (lowercase, NFKC), so "Label:", "IS:" and
// a Chinese IME's full-width "：" count. A leading "-" negates a clause and
// nothing else: "-1/2" and "-3 dB" stay text. A value lists alternatives
// split by "," or "，" outside quotes, each an op (>, >=, <, <=, =) and a
// value, or a range (a..b, a.., ..b). Quotes (" “ ”) hold a value's spaces
// and commas and are taken off it; in free text a quoted run is one token
// and keeps its quotes. An unclosed quote runs to the end of the text, as
// the value still being typed does.
//
// The raw text is read, never textnorm's normalizeQuery, which drops digit
// separators and would make "year:2019,2020" one number.

// A quote's opener and the character that closes it. iOS and macOS turn
// " into “ or ”, and after a letter or a colon may start with ”.
const CLOSERS = { "\"": "\"", "“": "”", "”": "”" };
const isSpace = (c) => /\s/.test(c);
const COMMAS = new Set([",", "，"]);
const KEY_RE = /^([-－]?)(\p{L}+)[:：]/u;

// The quoted runs of `s`, as [opener, closer] indexes: an unclosed one ends
// at s.length.
function quoteSpans(s) {
  const spans = [];
  for (let i = 0; i < s.length; i++) {
    const close = CLOSERS[s[i]];
    if (!close) continue;
    const end = s.indexOf(close, i + 1);
    spans.push([i, end < 0 ? s.length : end]);
    i = end < 0 ? s.length : end;
  }
  return spans;
}

// `s` cut at every character `cut(c)` names outside quotes: [{text, from}].
function splitOutsideQuotes(s, cut) {
  const spans = quoteSpans(s);
  const pieces = [];
  let start = 0, span = 0, i = 0;
  while (i <= s.length) {
    if (span < spans.length && i === spans[span][0]) {
      i = Math.min(spans[span][1] + 1, s.length);
      span++;
      continue;
    }
    if (i === s.length || cut(s[i])) {
      pieces.push({ text: s.slice(start, i), from: start });
      start = i + 1;
    }
    i++;
  }
  return pieces;
}

// `s` without its quote marks: what a quoted value means.
function unquote(s) {
  const drop = new Set(quoteSpans(s).flat());
  return s.split("").filter((_, i) => !drop.has(i)).join("");
}

// The whitespace-separated tokens of `text`, quoted runs held together:
// [{raw, from, to}].
export function tokenize(text) {
  const tokens = [];
  for (const piece of splitOutsideQuotes(text, isSpace)) {
    if (piece.text) tokens.push({ raw: piece.text, from: piece.from, to: piece.from + piece.text.length });
  }
  return tokens;
}

const fold = (s) => s.normalize("NFKC").toLowerCase();
const OPS = [">=", "<=", ">", "<", "="];
const opFold = (s) => s.replace(/^[≥]/, ">=").replace(/^[≤]/, "<=").normalize("NFKC");

// One alternative of a value: {op, a, b, text}. `text` is the alternative
// as written, its quotes off, for a name; `op` is "=", ">", ">=", "<",
// "<=" or "range" (a..b, either end may be ""). A quoted alternative is
// literal: op "=", `a` its text.
function alternative(piece) {
  const raw = piece.trim();
  const text = unquote(raw).trim();
  const quoted = !!CLOSERS[raw[0]] && quoteSpans(raw)[0]?.[1] >= raw.length - 1;
  if (quoted) return { op: "=", a: text, b: "", text };
  const plain = opFold(text);
  const range = /^(.*?)(?:\.\.\.?|…)(.*)$/s.exec(plain);
  if (range) return { op: "range", a: range[1].trim(), b: range[2].trim(), text };
  const op = OPS.find((o) => plain.startsWith(o));
  return op ? { op, a: plain.slice(op.length).trim(), b: "", text } : { op: "=", a: plain, b: "", text };
}

// `text` as tokens: {type: "clause", key, neg, values, value, valueFrom,
// raw, from, to} for a known key (`key` folded, `values` its non-empty
// alternatives, `value` the raw text after the colon starting at
// `valueFrom`), else {type: "text", raw, from, to}. `keys`: the keys the
// caller knows (an array or a Set, lowercase).
export function parseQuery(text, keys) {
  const known = keys instanceof Set ? keys : new Set(keys);
  return tokenize(text).map((tok) => {
    const m = KEY_RE.exec(tok.raw);
    const key = m && fold(m[2]);
    if (!m || !known.has(key)) return { type: "text", ...tok };
    const value = tok.raw.slice(m[0].length);
    const values = splitOutsideQuotes(value, (c) => COMMAS.has(c))
      .map((p) => alternative(p.text))
      .filter((v) => v.text);
    return { type: "clause", key, neg: !!m[1], values, value, valueFrom: tok.from + m[0].length, ...tok };
  });
}

// The token the caret is in or just after, else an empty one at the caret.
export function tokenAt(text, caret) {
  return tokenize(text).find((tok) => tok.from <= caret && caret <= tok.to) || { raw: "", from: caret, to: caret };
}

// The part of a value before its last alternative (`label:a,fr` → "a,"),
// so a completion replaces only the alternative being typed.
export function valueHead(value) {
  const pieces = splitOutsideQuotes(value, (c) => COMMAS.has(c));
  return pieces.length > 1 ? value.slice(0, pieces[pieces.length - 1].from) : "";
}

// The part of a value from the comma that ends the alternative at `at`
// (`label:a,b` at 1 → ",b"), "" when that alternative is the last, so a
// completion in the middle keeps the alternatives after it.
export function valueTail(value, at) {
  const piece = splitOutsideQuotes(value, (c) => COMMAS.has(c)).find((p) => at <= p.from + p.text.length);
  return piece ? value.slice(piece.from + piece.text.length) : "";
}

// The alternative being typed at the end of `value`, its quotes off.
export const lastAlternative = (value) => unquote(value.slice(valueHead(value).length)).trim();

// A name as a value: quoted when it holds a space, a comma or a quote (or
// is empty), with the quote kind it does not hold.
export function quoteValue(v) {
  if (v && !/[\s,，"“”]/.test(v)) return v;
  if (!v.includes("\"")) return `"${v}"`;
  if (!v.includes("”")) return `“${v}”`;
  return `"${v.replace(/"/g, "")}"`;
}

// `text` with the `spans` ({from, to}) cut out and the whitespace at each
// cut collapsed to one space. With no span, text.trim(): a query without
// qualifiers reaches the matchers as typed.
export function cutSpans(text, spans) {
  if (!spans.length) return text.trim();
  const join = (left, right) => {
    const l = left.replace(/\s+$/, ""), r = right.replace(/^\s+/, "");
    return l && r ? `${l} ${r}` : l + r;
  };
  let out = "", at = 0;
  for (const { from, to } of [...spans].sort((a, b) => a.from - b.from)) {
    out = join(out, text.slice(at, from));
    at = to;
  }
  return join(out, text.slice(at)).trim();
}
