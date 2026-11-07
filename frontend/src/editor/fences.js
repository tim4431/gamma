// A block's code fences for the editor, pure: the source editor's cards, and
// whether a position sits in code, for its key handling, popups and formatting
// commands (markCommands.js). Where code is, is shared/lib/mdCode.js's one
// reading. Apart from codeHighlight.js so node tests load them without
// highlight.js and the DOM.

import { codeFences } from "../shared/lib/mdCode.js";
import { findMathAtCursor } from "./latexCompletion.js";
import { lineAt, marksBefore } from "./mdLines.js";
import { copyMarks } from "./sourceCopy.js";

// The fences the source editor draws as cards: ``` ones at no depth, in order:
//   [{from, to, innerFrom, innerTo, lang, closed}]
// from/to include the fence marker lines (to = end of the closing line, or
// text end while the fence is still open); innerFrom = start of the first
// code line, innerTo = start of the closing line (== code end + "\n").
export function scanFences(text) {
  return codeFences(text).filter((f) => !f.prefix && f.ticks[0] === "`").map((f) => ({
    from: f.from, to: f.end,
    innerFrom: Math.min(f.inner + 1, text.length), innerTo: f.to,
    lang: f.info.trim(), closed: f.closed,
  }));
}

// The fence whose CODE the caret sits in (from the end of the opening line
// through the start of the closing line's mark), mdCode's fence with its `cont`
// — where Enter must insert a line break instead of a new note, and math/slash
// popups stay quiet.
export function fenceInnerAt(text, pos) {
  if (!text || !(text.includes("```") || text.includes("~~~"))) return null;
  return codeFences(text).find((f) => pos >= f.inner && pos <= f.mark) || null;
}

// What a new line starts with at `pos` in code or display math, a quote's marks
// or a list item's indent, from the line it opened on; null anywhere else.
export function codeMarksAt(text, pos) {
  const fence = fenceInnerAt(text, pos);
  if (fence) return fence.cont;
  const math = findMathAtCursor(text, pos);
  return math?.display ? marksBefore(text, math.start - 2) : null;
}

// What a paste of lines at `from` in marked code or display math puts in, each
// line behind the marks, so it stays there; null anywhere else. A copy from
// behind the same marks holds them already, and a caret among a line's marks
// leaves that line its own after the paste's last line break.
export function codePaste(text, from, clip) {
  const marks = codeMarksAt(text, from);
  if (!marks || !clip.includes("\n")) return null;
  const same = copyMarks(clip) === marks;
  const lines = clip.split("\n").map((l) => (same && l.startsWith(marks) ? l.slice(marks.length) : l));
  const line = lineAt(text, from), start = from - line.from;
  const among = start < marks.length && line.text.startsWith(marks);
  const tail = among && !lines.at(-1) ? (lines.pop(), "\n" + marks.slice(0, start)) : "";
  return (among ? marks.slice(start) : "") + lines.join("\n" + marks) + tail;
}
