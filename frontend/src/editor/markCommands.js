// The formatting commands of the block editor (bold, italic, code, strike,
// highlight, link) as functions of a CodeMirror view. Plain JS, no editor
// imports: editor/blockCommands.js binds them to keys and node tests load
// the catalog. Toggle semantics live in mdMarks.toggleMark; inside math, a
// code fence or inline code a formatting key is swallowed and does nothing —
// letting it through would hand Ctrl+B to the browser (Firefox: bookmarks).
import { fenceInnerAt } from "./fences.js";
import { insertLink, isUrl, scanMarks, toggleMark } from "./mdMarks.js";
import { scanMathSpans } from "./mdScan.js";

function markBlockedAt(doc, from, to, marker) {
  if (fenceInnerAt(doc, from) || fenceInnerAt(doc, to)) return true;
  if (scanMathSpans(doc).some((s) => s.from < to && from < s.to)) return true;
  if (marker === "`") return false;
  return scanMarks(doc).some((s) => s.marker === "`" && s.from < from && to < s.to);
}

export function runToggleMark(view, marker) {
  const doc = view.state.doc.toString();
  const { from, to } = view.state.selection.main;
  if (markBlockedAt(doc, from, to, marker)) return true;
  const r = toggleMark(doc, from, to, marker);
  if (r) view.dispatch({ changes: r.changes, selection: r.selection, userEvent: "input" });
  return true;
}

export function runInsertLink(view) {
  const doc = view.state.doc.toString();
  const { from, to } = view.state.selection.main;
  if (markBlockedAt(doc, from, to, "")) return true;
  const r = insertLink(doc, from, to);
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
