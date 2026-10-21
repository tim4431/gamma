// The "@" menu of a CodeMirror editor (BlockCmEditor): a note row's and a
// comment's field (docs/dev/mentions.md). An "@" that starts a word opens
// it at the "@", with people, dates and page links (editor/mentions.js
// mentionRows), drawn by MentionMenu.jsx's MentionMenuPopup.
//
// editorRef: the editor's textarea-like facade. people, me: who can be
// named. pages, rootId: the page links. canInvite: an "Invite…" row.
// reminders: "Remind" rows (a comment's field has none: the reminder scan
// reads notes only). onPicked(row): after a row went in (a reminder, an
// invite).
import { useState } from "react";
import { findMathAtCursor } from "./LatexEditor";
import { fenceInnerAt } from "./fences.js";
import { datePending, mentionRows, mentionText, mentionTrigger } from "./mentions.js";

export function useMentionMenu({ editorRef, people, me, pages, rootId, canInvite = false, reminders = true, onCacheRef, onPicked }) {
  // { start, query, rows, anchor }, opened by typing an "@" that starts a
  // word; it stays open across spaces while something answers the query.
  const [atMenu, setAtMenu] = useState(null);
  const [atIdx, setAtIdx] = useState(0);

  // "@" trigger (mentions.js mentionTrigger): recomputed on edits (typing,
  // may open) and caret moves (only keeps an open menu on its own "@");
  // closed by a selection, math, a code fence, a [[ being typed, or a query
  // nothing answers — unless it is a date still being typed ("@next" before
  // "fri"), which keeps the menu, hidden with no rows, until it means one.
  function update(ta, typing) {
    const cursor = ta.selectionStart, value = ta.value;
    if (cursor !== ta.selectionEnd || findMathAtCursor(value, cursor) || fenceInnerAt(value, cursor)
      || /\[\[[^\]\n]*$/.test(value.slice(0, cursor))) { setAtMenu(null); return; }
    setAtMenu((prev) => {
      const trigger = mentionTrigger(value, cursor, prev?.start ?? null);
      if (!trigger || (!typing && prev?.start !== trigger.start)) return null;
      const all = mentionRows({ query: trigger.query, people, me, pages, rootId, canInvite });
      const rows = reminders ? all : all.filter((r) => r.kind !== "remind");
      return rows.length || datePending(trigger.query) ? { ...trigger, rows, anchor: ta.caretCoords(trigger.start) } : null;
    });
    if (typing) setAtIdx(0);
  }

  // A picked row replaces the typed "@query" with its mention and a space
  // (mentions.js mentionText); "Invite…" removes it. One editor
  // transaction, change and caret together: a caret set a frame later would
  // land behind the keys typed meanwhile.
  function pick(row) {
    const ta = editorRef.current;
    if (!ta?.view || !atMenu) return;
    const { start } = atMenu;
    const cursor = ta.selectionStart;
    setAtMenu(null);
    const text = mentionText(row, me);
    const insert = text && !/^\s/.test(ta.value.slice(cursor, cursor + 1)) ? `${text} ` : text;
    if (row.kind === "page") onCacheRef?.(row.id, { content: row.title, page_title: row.title });
    ta.view.dispatch({
      changes: { from: start, to: cursor, insert },
      selection: { anchor: start + insert.length },
      userEvent: "input",
    });
    onPicked?.(row);
  }

  // The menu's keys while it has rows: ↑ ↓ choose, Enter or Tab pick,
  // Escape closes it. True when the key was taken.
  function keyDown(e) {
    if (!atMenu?.rows.length) return false;
    const n = atMenu.rows.length;
    if (e.key === "ArrowDown") { e.preventDefault(); setAtIdx((i) => Math.min(i + 1, n - 1)); return true; }
    if (e.key === "ArrowUp") { e.preventDefault(); setAtIdx((i) => Math.max(i - 1, 0)); return true; }
    if (e.key === "Enter" || (e.key === "Tab" && !e.shiftKey)) {
      e.preventDefault();
      pick(atMenu.rows[Math.min(atIdx, n - 1)]);
      return true;
    }
    if (e.key === "Escape") { e.preventDefault(); setAtMenu(null); return true; }
    return false;
  }

  const shown = !!atMenu?.rows.length;
  const selected = atMenu ? Math.min(atIdx, atMenu.rows.length - 1) : 0;
  return { atMenu, shown, selected, update, pick, keyDown, close: () => setAtMenu(null) };
}
