// The "/" command menu of a CodeMirror editor (BlockCmEditor): a note row's
// and a comment's field. A "/" typed at the start of a word opens it at the
// "/", and the letters after it are the query; it never opens inside math or
// a code fence. Its rows are the catalog's (editor/slashCommands.js
// filterSlashCommands) that this editor can run, drawn by SlashMenu.jsx's
// SlashMenuPopup with `popup` as its props.
//
// editorRef: the editor's textarea-like facade. can: the abilities a
// command may need ({newPage, sheet, image, embed}); one this editor lacks
// leaves the command out. setText(newVal, selStart, selEnd): puts a
// command's text in the editor and the caret or selection after it.
// refPicker: the editor's useRefPicker, which "Link to note" and "Embed
// note" open. actions(value, start, cursor): the rest of a command's ctx,
// the editor's own ({insertSheet, newPage, pickImage}). onRun(c, value,
// start): before a command runs.
import { useState } from "react";
import { findMathAtCursor } from "./LatexEditor";
import { fenceInnerAt } from "./fences.js";
import { filterSlashCommands } from "./slashCommands.js";

export function useSlashMenu({ editorRef, can, setText, refPicker, actions, onRun }) {
  // { start, query, items, anchor }. Opened only by TYPING the slash (caret
  // moves just keep or close it), suppressed inside math.
  const [slashMenu, setSlashMenu] = useState(null);
  const [slashIdx, setSlashIdx] = useState(0);

  // "/" trigger: a slash starting a word, with the query typed so far after
  // it. Recomputed on edits (typing=true, may open) and caret moves
  // (typing=false, only keeps an already-open menu on the same trigger).
  function update(ta, typing) {
    const cursor = ta.selectionStart;
    if (cursor !== ta.selectionEnd) { setSlashMenu(null); return; }
    const m = ta.value.slice(0, cursor).match(/(?:^|\s)\/([a-zA-Z0-9-]*)$/);
    if (!m || findMathAtCursor(ta.value, cursor) || fenceInnerAt(ta.value, cursor)) { setSlashMenu(null); return; }
    const start = cursor - m[1].length - 1;
    const items = filterSlashCommands(m[1], can);
    if (!items.length) { setSlashMenu(null); return; }
    const anchor = ta.caretCoords(start);
    setSlashMenu((prev) => {
      if (!typing && prev?.start !== start) return null;
      return { start, query: m[1], items, anchor };
    });
    if (typing) setSlashIdx(0);
  }

  // A picked command replaces the "/query" through its ctx
  // (slashCommands.js).
  function run(c) {
    const ta = editorRef.current;
    if (!ta || !slashMenu) return;
    const start = slashMenu.start;
    const value = ta.value;
    const cursor = ta.selectionStart;
    setSlashMenu(null);
    onRun?.(c, value, start);
    c.run({
      value,
      start,
      cursor,
      setText,
      openRefPopup: () => {
        requestAnimationFrame(() => refPicker.open(ta.caretCoords(Math.max(0, ta.selectionStart - 2))));
      },
      ...actions?.(value, start, cursor),
    });
  }

  // The menu's keys while it shows: ↑ ↓ choose, Enter or Tab run the
  // command, Escape closes it. True when the key was taken.
  function keyDown(e) {
    if (!slashMenu) return false;
    const n = slashMenu.items.length;
    if (e.key === "ArrowDown") { e.preventDefault(); setSlashIdx((i) => Math.min(i + 1, n - 1)); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); setSlashIdx((i) => Math.max(i - 1, 0)); return true; }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); run(slashMenu.items[slashIdx]); return true; }
    if (e.key === "Escape") { e.preventDefault(); setSlashMenu(null); return true; }
    return false;
  }

  const close = () => setSlashMenu(null);
  // SlashMenuPopup's props while the menu is open, else null.
  const popup = slashMenu ? {
    items: slashMenu.items, selected: slashIdx, anchor: slashMenu.anchor, onPick: run, grouped: !slashMenu.query, commands: true,
  } : null;
  return { popup, update, keyDown, close, run };
}
