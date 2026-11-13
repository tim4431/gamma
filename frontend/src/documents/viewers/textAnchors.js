// A range of a text file in the code view (docs/dev/api.md "The highlight
// shape", text_position): the one place a selection becomes a span and a
// stored span becomes CodeMirror offsets, and what each highlight is drawn
// as. A span's columns are code points of the decoded line (Python's
// indexes, gamma/highlights.py), CodeMirror's are UTF-16 code units:
// toCodePoints and toCodeUnits convert, and contextOf cuts a span's quote,
// prefix and suffix by the server's rule (span_text), the two held to one
// another by tests/shared/textposition.json. Pure: CodeMirror's state and
// text are read, never imported (tests/textAnchors.test.mjs).
import { commentOnlyHighlight } from "../../comments/comments.js";
import { TEXT_QUOTE_MAX, placedToCheck, rangePending, textSpan } from "../../shared/model/blockModel.js";
import { isLineStyle } from "../../shared/model/markStyle.js";
import { darkPageColor, darkPageLine, lineColor } from "../../shared/model/palette.js";

// The context kept on each side of a quote and the longest quote, in code
// points (gamma/highlights.py CONTEXT_CHARS, TEXT_QUOTE_MAX; the fixture's
// numbers).
export const CONTEXT_CHARS = 40;
export { TEXT_QUOTE_MAX };
// How much of a lost range's quote its tag's title shows.
const TITLE_CHARS = 80;

const cpLen = (s) => {
  let n = 0;
  for (const _ of s) n++;
  return n;
};

// A column of `lineText` in UTF-16 code units (CodeMirror's) as code points
// (a span's), `ch` clamped to the line; half of a surrogate pair counts as
// a character, as Python counts a lone surrogate.
export function toCodePoints(lineText, ch) {
  const end = Math.max(0, Math.min(ch, lineText.length));
  let n = 0;
  for (let i = 0; i < end; i++, n++) {
    const c = lineText.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < end) {
      const d = lineText.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) i++;
    }
  }
  return n;
}

// And back: a column of code points as UTF-16 code units, clamped to the
// line.
export function toCodeUnits(lineText, col) {
  let i = 0;
  for (let n = 0; n < col && i < lineText.length; n++) i += lineText.codePointAt(i) > 0xffff ? 2 : 1;
  return i;
}

// A span as it is stored over the file's lines (`lineOf(n)` the 1-based
// line's text, `count` how many): its lines within the file, each column at
// most its line's length, an end at column 0 of a later line, or after line
// breaks alone, moved back to the end of the line before, and a start at
// its line's end, or before line breaks alone, moved on to the start of the
// next line, so it never ends or starts in a line break
// (highlights.clamp_span); null when nothing is left.
function clamped(lineOf, count, span) {
  let { line, col, end, end_col: endCol } = span || {};
  if (![line, col, end, endCol].every(Number.isInteger) || line < 1 || !count || line > count) return null;
  if (end > count) {
    end = count;
    endCol = cpLen(lineOf(count));
  }
  col = Math.min(col, cpLen(lineOf(line)));
  endCol = Math.min(endCol, cpLen(lineOf(end)));
  while (end > line && endCol === 0) {
    end -= 1;
    endCol = cpLen(lineOf(end));
  }
  while (line < end && col === cpLen(lineOf(line))) {
    line += 1;
    col = 0;
  }
  return textSpan({ line, col, end, end_col: endCol });
}

// The quote, prefix and suffix of a stored span (`clamped`'s), as
// highlights.span_text cuts them: the lines joined with "\n", the quote cut
// at TEXT_QUOTE_MAX code points and never ending in a line break, the
// CONTEXT_CHARS code points before it and after it; never from a line
// `cutAt(n)` names (one the view cut short: its "…" and the rest of it are
// not the file's), which ends the context there, shorter.
function context(lineOf, count, s, cutAt = () => false) {
  let first = s.line, room = s.col;
  while (first > 1 && room < CONTEXT_CHARS && !cutAt(first - 1)) {
    first -= 1;
    room += cpLen(lineOf(first)) + 1;
  }
  let last = s.end;
  room = cpLen(lineOf(s.end)) - s.end_col;
  while (last < count && room < CONTEXT_CHARS && !cutAt(last + 1)) {
    last += 1;
    room += cpLen(lineOf(last)) + 1;
  }
  const parts = [];
  for (let n = first; n <= last; n++) parts.push(lineOf(n));
  const text = Array.from(parts.join("\n"));
  let start = s.col;
  for (let n = first; n < s.line; n++) start += cpLen(lineOf(n)) + 1;
  let stop = start - s.col + s.end_col;
  for (let n = s.line; n < s.end; n++) stop += cpLen(lineOf(n)) + 1;
  stop = Math.min(stop, start + TEXT_QUOTE_MAX);
  while (stop > start && text[stop - 1] === "\n") stop -= 1;
  return {
    quote: text.slice(start, stop).join(""),
    prefix: text.slice(Math.max(0, start - CONTEXT_CHARS), start).join(""),
    suffix: text.slice(stop, stop + CONTEXT_CHARS).join(""),
  };
}

// The quote, prefix and suffix of `span` ({line, col, end, end_col}, code
// points) in the decoded file's `lines`, columns clamped and an end at
// column 0 trimmed: the server's span_text, case for case. All three ""
// for a span with nothing in it.
export function contextOf(lines, span) {
  const lineOf = (n) => lines[n - 1];
  const s = clamped(lineOf, lines.length, span);
  return s ? context(lineOf, lines.length, s) : { quote: "", prefix: "", suffix: "" };
}

const ordered = (from, to) => (from <= to ? [from, to] : [to, from]);

// A selection's span, the one source of it: `from` and `to` are offsets in
// the view's document, read with view.posAtDOM over the DOM selection's two
// ends; `hash` the stem of the file the page shows (fileHash); `cutLines`
// the view's lines cut short with an "…" the file has not (textLoad.js), a
// Set of line numbers. {position: {line, col, end, end_col, prefix, suffix,
// hash}, quote}; null for an empty selection, one of a line break alone,
// and one touching a cut line (its quote would name text the file has not).
// Its context stops short of a cut line beside it.
export function spanOf(state, from, to, hash, cutLines) {
  if (from === to) return null;
  const doc = state.doc;
  const [a, b] = ordered(from, to);
  const first = doc.lineAt(a), last = doc.lineAt(b);
  const lineOf = (n) => doc.line(n).text;
  const s = clamped(lineOf, doc.lines, {
    line: first.number, col: toCodePoints(first.text, a - first.from),
    end: last.number, end_col: toCodePoints(last.text, b - last.from),
  });
  if (!s) return null;
  if (cutLines?.size) {
    for (const n of cutLines) if (n >= s.line && n <= s.end) return null;
  }
  const { quote, prefix, suffix } = context(lineOf, doc.lines, s, (n) => !!cutLines?.has(n));
  if (!quote) return null;
  return { position: { ...s, prefix, suffix, hash: hash || "" }, quote };
}

// A stored range as offsets in the view's document (a CodeMirror Text),
// each column clamped to its line, an end past the file at its end;
// `past` when its first line is past the file's last (a lost range, whose
// tag then stands at the last line's start). null for a malformed one.
export function rangeOf(doc, pos) {
  const span = textSpan(pos);
  if (!span) return null;
  const count = doc.lines;
  if (span.line > count) {
    const at = doc.line(count).from;
    return { from: at, to: at, past: true };
  }
  const first = doc.line(span.line);
  const from = first.from + toCodeUnits(first.text, span.col);
  let to = doc.length;
  if (span.end <= count) {
    const last = doc.line(span.end);
    to = last.from + toCodeUnits(last.text, span.end_col);
  }
  return { from, to: Math.max(from, to), past: false };
}

// The lines a selection covers ({line, end}, 1-based, end inclusive), for
// the chat: an end at the start of a later line belongs to the line before,
// and a start at the end of a line to the line after, as spanOf has it;
// null for an empty selection.
export function lineSpotOf(doc, from, to) {
  if (from === to) return null;
  const [a, b] = ordered(from, to);
  const first = doc.lineAt(a);
  let line = first.number, col = a - first.from;
  const last = doc.lineAt(b);
  let end = last.number, endCol = b - last.from;
  while (end > line && endCol === 0) {
    end -= 1;
    endCol = doc.line(end).length;
  }
  while (line < end && col === doc.line(line).length) {
    line += 1;
    col = 0;
  }
  return { line, end };
}

// The colour vars a highlight's mark, tag and line cue are drawn with, as
// the PDF's highlights carry them (pdf/PdfViewer.jsx): --hl its colour,
// --hl-dark / --hl-dark-line its dark-theme fill and line, --hl-mark its
// line tone (an underline, the line cue's bar).
function colorVars(color) {
  const vars = { "--hl": color, "--hl-dark": darkPageColor(color), "--hl-dark-line": darkPageLine(color), "--hl-mark": lineColor(color) };
  return Object.entries(vars).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("; ");
}

// What one highlight (treeDerived.js pageHighlights' item under
// text_position) is drawn as: {kind, class, attributes}. Kind "mark", a
// mark over its text: the wash (docHl), a line style by data-hl-style,
// docHlApprox for one to check, docHlPending for one measured on another
// version than `fileHash` that the server's run has not placed yet,
// docHlCommented for one with threads and no note, docHlLink for a link,
// docHlActive for the active card's, docHlFlash while it flashes. Kind
// "lost" (its text is gone): no mark over the text that stands at its old
// lines now, a tag at its old first line (docHlLost) whose title is its
// quote. Both carry data-hl-id, which a click and the margin find it by.
export function markSpec(h, { active = false, flash = false, fileHash = "" } = {}) {
  const pos = h.position || {};
  const style = colorVars(h.color);
  const extra = [active ? "docHlActive" : "", flash ? "docHlFlash" : ""];
  if (pos.lost) {
    const title = Array.from(h.quote || "").slice(0, TITLE_CHARS).join("");
    return { kind: "lost", class: ["docHlLost", ...extra].filter(Boolean).join(" "),
      attributes: { "data-hl-id": h.id, title, style } };
  }
  const link = !!h.linkTarget;
  const classes = [
    "docHl",
    placedToCheck(pos) ? "docHlApprox" : "",
    rangePending(pos, fileHash) ? "docHlPending" : "",
    !link && commentOnlyHighlight(h) ? "docHlCommented" : "",
    link ? "docHlLink" : "",
    ...extra,
  ];
  const attributes = { "data-hl-id": h.id, style };
  if (!link && isLineStyle(h.style)) attributes["data-hl-style"] = h.style;
  return { kind: "mark", class: classes.filter(Boolean).join(" "), attributes };
}

// The cue on every line a range covers, placed or lost (docHlLine, a bar
// on the gutter side in its colour's line tone; lost dashed, one to check
// fainter): how a file is scanned for its commented lines when the wash is
// wrapped, scrolled out or gone.
export function lineCueSpec(h) {
  const pos = h.position || {};
  const classes = ["docHlLine", pos.lost ? "lost" : placedToCheck(pos) ? "approx" : ""];
  const vars = { "--hl-mark": lineColor(h.color), "--hl-dark-line": darkPageLine(h.color) };
  const style = Object.entries(vars).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("; ");
  return { class: classes.filter(Boolean).join(" "), attributes: style ? { style } : {} };
}
