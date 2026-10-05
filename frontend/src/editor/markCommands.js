// The formatting commands of the block editor (bold, italic, code, strike,
// highlight, link, inline math) and the "/" that opens the insert menu, as
// functions of a CodeMirror view over plans of the text alone (`…Plan`,
// `…At`: {changes, selection} or null), which the iPad app's editing bar
// applies too (ipad/core/entry.js `format`). Plain JS, no editor imports:
// editor/blockCommands.js makes them catalog commands and node tests load
// the catalog. Toggle semantics live in mdMarks.toggleMark; inside math, a
// code fence or inline code a formatting key is swallowed and does nothing —
// letting it through would hand Ctrl+B to the browser (Firefox: bookmarks).
import { fenceInnerAt } from "./fences.js";
import { insertLink, isUrl, scanMarks, toggleMark } from "./mdMarks.js";
import { scanMathSpans } from "./mdScan.js";

// Whether [from, to] is in a code fence or reaches inside a math span (a
// caret: strictly between its delimiters), where marks, "/" and "$" mean
// nothing.
function inFenceOrMath(doc, from, to) {
  return fenceInnerAt(doc, from) || fenceInnerAt(doc, to)
    || scanMathSpans(doc).some((s) => s.from < to && from < s.to);
}

function markBlockedAt(doc, from, to, marker) {
  if (inFenceOrMath(doc, from, to)) return true;
  if (marker === "`") return false;
  return scanMarks(doc).some((s) => s.marker === "`" && s.from < from && to < s.to);
}

// `marker` toggled on [from, to], or null where marks mean nothing.
export function markPlan(doc, from, to, marker) {
  return markBlockedAt(doc, from, to, marker) ? null : toggleMark(doc, from, to, marker);
}

// [from, to] made a link with the caret in its empty (…) slot, or null
// where marks mean nothing.
export function linkPlan(doc, from, to) {
  return markBlockedAt(doc, from, to, "") ? null : insertLink(doc, from, to);
}

// The insert menu without typing (the touch editing bar, editor/EditBar.jsx):
// a "/" after the selection, with a space before it when it would follow a
// word, so the row's slash trigger opens the menu as if it had been typed.
// Null inside math or a code fence, where "/" is no command.
export function slashInsertAt(doc, from, to) {
  if (inFenceOrMath(doc, to, to)) return null;
  const insert = to > 0 && !/\s/.test(doc[to - 1]) ? " /" : "/";
  return { changes: [{ from: to, to, insert }], selection: { anchor: to + insert.length } };
}

// Inline math without typing "$": the selection wrapped in $…$ with its
// text still selected, or "$x$" with the x selected at the caret (as /math
// inserts it). Null inside math or a fence, or across lines.
export function mathInsertAt(doc, from, to) {
  if (inFenceOrMath(doc, from, to)) return null;
  if (from === to) return { changes: [{ from, to, insert: "$x$" }], selection: { anchor: from + 1, head: from + 2 } };
  if (doc.slice(from, to).includes("\n")) return null;
  return {
    changes: [{ from, to: from, insert: "$" }, { from: to, to, insert: "$" }],
    selection: { anchor: from + 1, head: to + 1 },
  };
}

function runInsert(view, plan) {
  const { from, to } = view.state.selection.main;
  const r = plan(view.state.doc.toString(), from, to);
  if (r) view.dispatch({ changes: r.changes, selection: r.selection, userEvent: "input" });
  return true;
}

export const runInsertSlash = (view) => runInsert(view, slashInsertAt);
export const runInsertMath = (view) => runInsert(view, mathInsertAt);

export const runToggleMark = (view, marker) => runInsert(view, (doc, from, to) => markPlan(doc, from, to, marker));

export function runInsertLink(view) {
  const doc = view.state.doc.toString();
  const { from, to } = view.state.selection.main;
  const r = linkPlan(doc, from, to);
  if (!r) return true;
  view.dispatch({ changes: r.changes, selection: r.selection, userEvent: "input" });
  // A URL on the clipboard fills the empty (…) slot — read asynchronously
  // (and not at all on plain-HTTP origins, where navigator.clipboard is
  // missing); only applied if the doc hasn't moved on meanwhile.
  const slot = from + 1 + (to - from) + 2;
  const expect = view.state.doc.toString();
  navigator.clipboard?.readText?.().then((clip) => {
    if (!isUrl(clip) || view.state.doc.toString() !== expect) return;
    const url = clip.trim();
    const label = to - from;
    view.dispatch({
      changes: { from: slot, insert: url },
      selection: { anchor: label ? slot + url.length + 1 : from + 1 },
      userEvent: "input",
    });
  }).catch(() => {});
  return true;
}
