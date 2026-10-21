// Live LaTeX aids in a CodeMirror editor (BlockCmEditor): a note row's,
// the embedded-note card's in-place editor and a comment's field
// (docs/dev/latex_editing.md). `ta` is the editor's textarea-like facade.
import { useState } from "react";
import {
  envCompletions, findMathAtCursor, latexCompletionEdit, latexCompletions, mathTabJump,
} from "./LatexEditor";
import { fenceInnerAt } from "./fences.js";

// Live LaTeX aids while the caret sits inside $...$ / $$...$$: the floating
// preview plus \command autocomplete state. Recomputed on every edit AND caret move
// (the preview must track the caret); the autocomplete only OPENS on typing.
export function useMathUi() {
  const [mathUi, setMathUi] = useState(null);
  const [mathAcIdx, setMathAcIdx] = useState(0);

  function updateMathUi(ta, typing) {
    const cursor = ta.selectionStart;
    if (cursor !== ta.selectionEnd) { setMathUi(null); return; }
    // A "$" inside a ``` fence is code (shell vars), never math.
    const seg = fenceInnerAt(ta.value, cursor) ? null : findMathAtCursor(ta.value, cursor);
    if (!seg) { setMathUi(null); return; }
    // \command autocomplete: a backslash-word ending at the caret, only
    // inside math (a bare "\" in prose — file paths — must not trigger it),
    // and only opened by TYPING — clicking into an existing formula must not
    // pop the menu. Caret moves (typing=false) keep an already-open popup
    // only while the caret stays on the same trigger; React fires onSelect
    // right after onChange for a keystroke, so this must not wipe it.
    // "\begin{name" (even with the } already typed) completes environment
    // names instead — accepting replaces the whole \begin{… with the snippet.
    const before = ta.value.slice(seg.start, cursor);
    const mEnv = before.match(/\\begin\{([a-zA-Z*]*)\}?$/);
    const m = mEnv ? null : before.match(/\\([a-zA-Z]+)$/);
    // The completion popup hangs off the caret; the preview is docked to the
    // editor column (dock = its content box) above the span's first line
    // — stable while typing inside a multi-line $$ block — with the caret
    // line as the fallback when the span's edges are off screen
    // (useCaretAnchored). `caret` marks the typing spot in the preview.
    const previewRect = () => {
      const box = ta.getBoundingClientRect();
      const first = ta.caretCoords(seg.start), last = ta.caretCoords(seg.end), at = ta.caretCoords(cursor);
      return {
        left: first.left, top: first.top, bottom: last.bottom,
        caretTop: at.top, caretBottom: at.bottom,
        dock: { left: box.left, right: box.right },
      };
    };
    const next = {
      tex: ta.value.slice(seg.start, seg.end),
      display: seg.display,
      caret: cursor - seg.start,
      anchor: { ...ta.caretCoords(cursor), getRect: () => ta.caretCoords(cursor) },
      previewAnchor: { ...previewRect(), getRect: previewRect },
      ac: null,
    };
    setMathUi((prev) => {
      const trig = mEnv || m;
      const start = trig ? cursor - trig[0].length : -1;
      if (trig && (typing || prev?.ac?.start === start)) {
        const items = mEnv ? envCompletions(mEnv[1]) : latexCompletions(m[1]);
        if (items.length) next.ac = { start, items };
      }
      return next;
    });
    if (typing) setMathAcIdx(0);
  }

  // Accept an autocomplete entry into the editor `ta` and close the popup.
  function acceptCompletion(ta, c) {
    if (!ta || !mathUi?.ac) return;
    const { start } = mathUi.ac;
    const edit = latexCompletionEdit(ta.value, start, ta.selectionStart, c, mathUi.display);
    ta.view?.dispatch({ ...edit, userEvent: "input.complete" });
    setMathUi(null);
    ta.focus();
    updateMathUi(ta, false);
  }

  // The completion popup's keys, while it is open, in the editor `ta`:
  // ↑ ↓ choose, Tab accepts, Escape closes it. Enter closes it too and keeps
  // its own job (a line break in $$, else the field's Enter): a completion
  // the user never asked for must not eat it. True when the key was taken.
  function acKey(e, ta) {
    if (!mathUi?.ac) return false;
    const n = mathUi.ac.items.length;
    if (e.key === "ArrowDown") { e.preventDefault(); setMathAcIdx((i) => Math.min(i + 1, n - 1)); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); setMathAcIdx((i) => Math.max(i - 1, 0)); return true; }
    if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); acceptCompletion(ta, mathUi.ac.items[mathAcIdx]); return true; }
    if (e.key === "Escape") { e.preventDefault(); setMathUi((u) => (u ? { ...u, ac: null } : null)); return true; }
    if (e.key === "Enter") setMathUi((u) => (u ? { ...u, ac: null } : null));
    return false;
  }

  // Tab inside raw math (the popup closed) hops between argument groups
  // snippet-style — \frac{1|}{} lands in the second {} — and Shift+Tab hops
  // back. Only when there is somewhere to go: else the key is the caller's
  // (a note's indent). True when the key was taken.
  function tabJumpKey(e, ta) {
    if (e.key !== "Tab" || !ta) return false;
    const origin = e.shiftKey ? ta.selectionStart : ta.selectionEnd;
    const jump = !fenceInnerAt(ta.value, origin) && mathTabJump(ta.value, origin, e.shiftKey ? -1 : 1);
    if (!jump) return false;
    e.preventDefault();
    ta.setSelectionRange(jump.anchor, jump.head);
    updateMathUi(ta, false);
    return true;
  }

  return { mathUi, setMathUi, mathAcIdx, setMathAcIdx, updateMathUi, acceptCompletion, acKey, tabJumpKey };
}
