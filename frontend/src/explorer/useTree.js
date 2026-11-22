// The keys and the focus of a tree of rows (docs/dev/ui-design.md "Keyboard
// focus"), the Files window's (FileTree.jsx), a synced folder's folder view
// (FolderTree.jsx) and the folder browser's (FolderBrowser.jsx) alike: one row in the tab order, what a key does
// (fileTree.js keyAction), and the keys and the focus kept when the row
// they were on goes (focusAfter).
import { useLayoutEffect, useRef } from "react";
import { pressAt } from "../shared/ui/press.js";
import { focusAfter, keyAction, tabStop } from "./fileTree.js";

// `rows` the rows drawn (fileTree.js), `active` the key of the row the
// keys are on and setActive(key), `selectedId` the selected row's id (the
// tab stop without an active row); act(kind, row) does keyAction's
// "toggle" (also for "expand" and "collapse"), "open" and "select" on a
// row, and menuOf(row) is the row's menu opener (an event in, null for
// none), which Shift+F10 and the menu key open at the row. {treeProps (the
// role="tree" element's), rowProps(row) (TreeRow's rowRef, tabbable,
// active, onActive), focusRow(key)}.
export function useTree({ rows, active, setActive, selectedId, act, menuOf }) {
  const stop = tabStop(rows, active, selectedId);
  const rowRefs = useRef(new Map());
  const focusRow = (key) => {
    setActive(key);
    rowRefs.current.get(key)?.focus();
  };

  function onKeyDown(e) {
    if (e.altKey || e.ctrlKey || e.metaKey || (e.shiftKey && e.key !== "F10")) return;
    if (e.target !== e.currentTarget && e.target.getAttribute("role") !== "treeitem") return;
    const step = keyAction(rows, e.target.dataset.key || stop, e.shiftKey ? "Shift+F10" : e.key);
    if (!step.kind) return;
    e.preventDefault();
    e.stopPropagation();
    const row = rows.find((r) => r.key === step.key);
    if (step.kind === "move" || step.kind === "parent") focusRow(step.key);
    else if (step.kind === "expand" || step.kind === "collapse") act("toggle", row);
    else if (step.kind === "menu") {
      const el = rowRefs.current.get(row.key);
      const box = el.getBoundingClientRect();
      menuOf(row)?.(pressAt(box.left + 24, box.bottom - 4, el));
    } else act(step.kind, row);
  }

  // The keys kept in the tree when the row they were on goes (a "Show
  // more" row read, a note row replaced by its files) and the focus with
  // it: the row now in its place takes both. A blur out of the tree lets
  // go; a row taken away fires none.
  const inTree = useRef(false);
  const drawn = useRef(rows);
  useLayoutEffect(() => {
    const before = drawn.current;
    drawn.current = rows;
    const key = focusAfter(before, rows, active);
    if (key && inTree.current && (!document.activeElement || document.activeElement === document.body)) focusRow(key);
  });

  const treeProps = {
    role: "tree", onKeyDown,
    onFocus: () => { inTree.current = true; },
    onBlur: (e) => { if (!e.currentTarget.contains(e.relatedTarget)) inTree.current = false; },
  };
  const rowProps = (row) => ({
    rowRef: (el) => { if (el) rowRefs.current.set(row.key, el); else rowRefs.current.delete(row.key); },
    tabbable: row.key === stop, active: row.key === active, onActive: setActive,
  });
  return { treeProps, rowProps, rowRefs, focusRow };
}
