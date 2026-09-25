// The formatting commands of the block editor (bold, italic, code, strike,
// highlight, link) as functions of a CodeMirror view, and the math-span
// scanner they share with the live renderer. Plain JS, no editor imports:
// editor/blockCommands.js binds them to keys and node tests load the
// catalog. Toggle semantics live in mdMarks.toggleMark; inside math, a code
// fence or inline code a formatting key is swallowed and does nothing —
// letting it through would hand Ctrl+B to the browser (Firefox: bookmarks).
import { escapedAt } from "./latexInput.js";
import { fenceInnerAt } from "./fences.js";
import { insertLink, isUrl, scanMarks, toggleMark } from "./mdMarks.js";

// All CLOSED math spans in the text: [{from, to, display}] with from/to
// including the delimiters. Same tokenizer as latexEditor's findMathAtCursor
// (escaped \$ skipped), but only complete pairs — an unclosed opener stays
// raw text while it's being typed. Inline spans must sit on one line and be
// non-empty; "$5 and $3" across prose otherwise pairs into a bogus formula.
export function scanMathSpans(text) {
  const re = /\$\$?/g;
  const spans = [];
  let m, open = null;
  while ((m = re.exec(text))) {
    if (escapedAt(text, m.index)) continue;
    const tok = { i: m.index, len: m[0].length };
    if (!open) {
      open = tok;
    } else if (tok.len === open.len) {
      const inner = text.slice(open.i + open.len, tok.i);
      const ok = inner.trim() && (open.len === 2 || !inner.includes("\n"));
      if (ok) spans.push({ from: open.i, to: tok.i + tok.len, display: open.len === 2 });
      open = null;
    } else {
      // Mismatched pair ($ ... $$): treat the later token as a fresh opener.
      open = tok;
    }
  }
  return spans;
}

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
