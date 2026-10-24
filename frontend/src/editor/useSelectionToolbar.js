// When a note's selection toolbar shows (editor/SelectionToolbar.jsx draws
// it): over selected text, once the selection is made. A selection made
// with the mouse shows it when the button is released, so a drag or a
// double-click's second press never has it jump along; one made with the
// keyboard shows it at once. It steps aside while nothing is selected, on
// Escape (until the selection changes), and while text is typed on an
// on-screen keyboard, where the editing bar (EditBar.jsx) has the tools.
//
// active: the row's editor is open. update(editor): after every edit and
// caret move, with the editor's textarea-like facade. keyDown(e): the row's
// keydown, before its catalog; true when the key was taken. close(): the
// editor blurred. `selection` ({from, to, text}) while the bar shows, else
// null.
import { useEffect, useRef, useState } from "react";
import { touchTyping } from "../shared/lib/pointer.js";

// Whether the mouse's button is down: a selection is still being made. One
// listener for every editor; the one waiting for the release subscribes.
// A press on the toolbar itself is no selection.
let held = false;
const onRelease = new Set();
if (typeof window !== "undefined") {
  window.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button === 0 && !e.target?.closest?.(".selTools")) held = true;
  }, { capture: true, passive: true });
  // After the release's own handlers, so the editor has its final selection.
  const release = () => {
    if (!held) return;
    held = false;
    setTimeout(() => { for (const f of [...onRelease]) f(); });
  };
  for (const type of ["pointerup", "pointercancel", "blur"]) window.addEventListener(type, release, { capture: true, passive: true });
}

export function useSelectionToolbar(active) {
  const [selection, setSelection] = useState(null);
  const editorRef = useRef(null);
  // The selection Escape put the bar away for.
  const dismissed = useRef(null);
  // The release callback while one is subscribed.
  const waiting = useRef(null);

  const stopWaiting = () => {
    if (waiting.current) onRelease.delete(waiting.current);
    waiting.current = null;
  };
  useEffect(() => {
    if (active) return undefined;
    stopWaiting();
    setSelection(null);
    return undefined;
  }, [active]);
  useEffect(() => stopWaiting, []);

  function update(ta) {
    editorRef.current = ta;
    const from = ta.selectionStart, to = ta.selectionEnd, text = ta.value;
    const same = (s) => s && s.from === from && s.to === to && s.text === text;
    if (from !== to && held && !waiting.current) {
      waiting.current = () => {
        stopWaiting();
        if (editorRef.current) update(editorRef.current);
      };
      onRelease.add(waiting.current);
    }
    if (from === to || held || touchTyping() || same(dismissed.current)) {
      setSelection(null);
      return;
    }
    dismissed.current = null;
    setSelection((prev) => (same(prev) ? prev : { from, to, text }));
  }

  function keyDown(e) {
    if (!selection || e.key !== "Escape") return false;
    e.preventDefault();
    dismissed.current = selection;
    setSelection(null);
    return true;
  }

  function close() {
    editorRef.current = null;
    dismissed.current = null;
    stopWaiting();
    setSelection(null);
  }

  return { selection: active ? selection : null, update, keyDown, close };
}
