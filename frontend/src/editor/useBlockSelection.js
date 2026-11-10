// Selecting whole notes the way Notion does (docs/dev/ui-design.md,
// "Selecting notes"), on the open page's notes list:
// - a drag that starts in a note selects its text, as before; once it
//   crosses into another note it selects the notes from the one it started
//   in to the one under the pointer, and the text selection goes;
// - a drag from the blank beside, between or below the notes draws a box,
//   and the notes it touches are selected;
// - Shift+click selects from the note being written in (else the
//   selection's anchor, else the focused row) to the one clicked, and
//   Alt+Shift+click (⌘⇧ on a Mac) picks or lets go of one note;
// - Esc in a note's editor selects that note (`selectBlock`, the row's
//   key), Ctrl+A in a note whose text is all selected selects every note
//   (`selectEvery`), and a click on the ⋮⋮ handle selects its note
//   (`handleSelect`), or keeps the several it is one of for its menu;
// - with notes selected, KEYS act on them, and a plain press anywhere but
//   a menu lets them go.
// Mouse only: a finger scrolls and a pen writes. The state is the viewer's
// own (blockSelection.js); what the actions do to the tree is App's (`act`).
import { useEffect, useRef, useState } from "react";
import { chordFromEvent, composing, isTextField } from "../shared/lib/hotkeys.js";
import { lastPointer } from "../shared/lib/pointer.js";
import { visibleBlocks } from "../shared/model/blockModel.js";
import {
  NO_SELECTION, pruneSelection, selectAll, selectIds, selectOne, selectRange, selectionRoots, stepSelection, toggleSelected,
} from "./blockSelection.js";

// A press here does what it always did: a button, a link, a field, the
// handle (its click and its drag are the row's), a page of paper in the
// notes (it is written on), an image, table or diagram (it selects itself).
const INERT = "button, a, input, textarea, select, .dragHandle, .addHandle, .noteSheet, .mdObject";
// A press in a menu keeps the selection: the handle's menu acts on it.
const KEEPS = "[role=\"menu\"], .ctxMenu";
// What keeps its keys while notes are selected: a menu open, or the focus
// on a control or in a dialog (Enter presses a focused button).
const OWN_KEYS = "button, a[href], select, [role=\"menu\"], [role=\"dialog\"], [role=\"option\"]";
// The tree's own boxes around and between the rows: a press right on one is
// a press on blank space, where a drag draws the box.
const BLANK = ["blockList", "listMotion", "sortableBlockWrap", "rowHandles", "blockFold", "blockChildren"];
const DRAG_MIN = 4; // px the pointer goes before a press is a drag
const EDGE = 36; // px from the list's top or bottom edge where a drag scrolls it
const SCROLL_MOST = 24; // px a frame

// The selection's keys (Notion's), as chords (shared/lib/hotkeys.js). They
// are the outliner's own, like Enter and Tab in an editor: listed in
// app/commands.js fixedKeys, not rebindable. Esc is App's, which closes
// the popovers first and lets every selection go (`clear`).
const KEYS = {
  ArrowUp: "up",
  ArrowDown: "down",
  "Shift-ArrowUp": "extendUp",
  "Shift-ArrowDown": "extendDown",
  "Mod-Shift-ArrowUp": "moveUp",
  "Mod-Shift-ArrowDown": "moveDown",
  Enter: "edit",
  Backspace: "delete",
  Delete: "delete",
  Tab: "indent",
  "Shift-Tab": "outdent",
  "Mod-d": "duplicate",
  "Mod-c": "copy",
  "Mod-x": "cut",
  "Mod-a": "all",
  "Mod-/": "menu",
};

const sameSelection = (a, b) => a.anchor === b.anchor && a.head === b.head
  && a.ids.size === b.ids.size && [...a.ids].every((id) => b.ids.has(id));

const rowOf = (list, id) => list?.querySelector(`.blockRowWrap[data-block-id="${CSS.escape(id)}"]`);

// `enabled`: an editable page is open. `blocks` and `editing` (the open
// editor's block id) are this render's; `list()` the open page's notes list,
// `tree()` / `view()` the page's blocks and view at event time; `anchorOf()`
// where a Shift+click range starts without a selection; `closeEditor()`,
// `edit(id)` (its editor opened, the caret at its end); `act(action,
// roots)` runs "delete", "cut", "copy", "duplicate", "indent", "outdent",
// "moveUp", "moveDown", "chat" or "move" on the selection's roots and
// returns the ids to select next, or nothing to keep the selection.
export function useBlockSelection({ enabled, blocks, editing, list, tree, view, anchorOf, closeEditor, edit, act }) {
  const [selection, setSelection] = useState(NO_SELECTION);
  const selRef = useRef(selection);
  const opts = useRef(null);
  opts.current = { enabled, list, tree, view, anchorOf, closeEditor, edit, act };
  // Its functions read refs only, so the listeners keep the first ones.
  const api = useRef(null);
  if (!api.current) {
    const set = (next) => {
      if (next === selRef.current || sameSelection(next, selRef.current)) return;
      selRef.current = next;
      setSelection(next);
    };
    const roots = () => selectionRoots(opts.current.tree(), selRef.current.ids);
    const step = (dir, extend) => {
      const o = opts.current;
      const next = stepSelection(o.tree(), o.view(), selRef.current, dir, extend);
      set(next);
      if (next.head) rowOf(o.list(), next.head)?.scrollIntoView({ block: "nearest" });
    };
    // The handle's menu for the selection: a click on its first note's ⋮⋮,
    // where a click of the mouse opens it.
    const openMenu = (id) => {
      const handle = opts.current.list()?.querySelector(`.sortableBlockWrap[data-block-id="${CSS.escape(id)}"] > .rowHandles > .dragHandle`);
      if (!handle) return;
      const r = handle.getBoundingClientRect();
      handle.dispatchEvent(new MouseEvent("click", { bubbles: true, clientX: r.left + r.width / 2, clientY: r.bottom }));
    };
    // An action on the selection, from its keys or the handle's menu.
    const run = (action) => {
      const o = opts.current, cur = selRef.current;
      if (action === "all") return set(selectAll(o.tree(), o.view()));
      if (action === "up" || action === "down") return step(action === "up" ? -1 : 1, false);
      if (action === "extendUp" || action === "extendDown") return step(action === "extendUp" ? -1 : 1, true);
      const ids = roots();
      if (!ids.length) return;
      if (action === "menu") return openMenu(ids[0]);
      if (action === "edit") {
        set(NO_SELECTION);
        o.edit(cur.ids.has(cur.head) ? cur.head : ids[0]);
        return;
      }
      const next = o.act(action, ids);
      if (next) set(selectIds(next));
    };
    api.current = {
      set,
      run,
      clear: () => set(NO_SELECTION),
      // The ⋮⋮ handle's click: its note selected, as in Notion; one of
      // several selected keeps them, and the menu acts on them all.
      handleSelect: (id) => {
        const cur = selRef.current;
        if (cur.ids.has(id) && cur.ids.size > 1) return roots();
        set(selectOne(id));
        return [id];
      },
      // The handle's drag: the selection's roots when its note is one of
      // several selected, else null (it moves alone, the selection let go).
      dragSelection: (id) => {
        const cur = selRef.current;
        if (cur.ids.has(id) && cur.ids.size > 1) return roots();
        set(NO_SELECTION);
        return null;
      },
      selectBlock: (id) => {
        set(selectOne(id));
        opts.current.closeEditor();
      },
      selectEvery: () => {
        const o = opts.current;
        o.closeEditor();
        set(selectAll(o.tree(), o.view()));
      },
    };
  }
  const { set } = api.current;

  useEffect(() => { if (!enabled) set(NO_SELECTION); }, [enabled, set]);
  // Blocks gone from the tree leave it; another page's tree leaves none.
  useEffect(() => { set(pruneSelection(blocks, selRef.current)); }, [blocks, set]);
  // An editor opening (a click, Enter, a new note) ends it.
  useEffect(() => { if (editing) set(NO_SELECTION); }, [editing, set]);

  useEffect(() => {
    // The press being followed: where it started (`x`, `y`, the list's
    // scroll then), where the pointer is (`cx`, `cy`), the note it started
    // in (`startId`, `startEl`; null on blank space), and once it is a drag
    // `mode` "blocks" or "box", with the shown rows and the box's element.
    let press = null;
    let raf = 0;
    const rowsShown = (list) => {
      const o = opts.current;
      const shown = new Set(visibleBlocks(o.tree(), o.view()).map((b) => b.id));
      return [...list.querySelectorAll(".blockRowWrap[data-block-id]")].filter((el) => shown.has(el.dataset.blockId));
    };
    // The row under `y`, or the one above the gap it is in: the last row
    // whose top is above it (the rows stand in document order).
    const rowAt = (rows, y) => {
      let lo = 0, hi = rows.length - 1, hit = 0;
      while (lo <= hi) {
        const mid = (lo + hi) >> 1;
        if (rows[mid].getBoundingClientRect().top <= y) { hit = mid; lo = mid + 1; } else hi = mid - 1;
      }
      return rows[hit]?.dataset.blockId || null;
    };
    const take = (e) => { e.preventDefault(); e.stopPropagation(); };
    const swallow = (e) => { e.preventDefault(); e.stopPropagation(); };

    function begin(mode) {
      press.mode = mode;
      press.rows ??= rowsShown(press.list);
      press.list.classList.add("blockSelecting");
      opts.current.closeEditor();
      if (mode === "box") {
        press.band = document.createElement("div");
        press.band.className = "blockSelectBand";
        document.body.append(press.band);
      }
    }

    function update() {
      const o = opts.current, { list, rows } = press;
      // A read-only row's text the browser had started to select.
      window.getSelection()?.removeAllRanges();
      if (press.mode === "blocks") {
        const over = rowAt(rows, press.cy);
        if (over) api.current.set(selectRange(o.tree(), o.view(), press.startId, over));
        return;
      }
      // The box keeps its start where the press was in the list, scrolled or
      // not; it is drawn inside the list's box.
      const y0 = press.y - (list.scrollTop - press.scroll0);
      const top = Math.min(y0, press.cy), bottom = Math.max(y0, press.cy);
      const left = Math.min(press.x, press.cx), right = Math.max(press.x, press.cx);
      const r = list.getBoundingClientRect();
      const shownTop = Math.max(r.top, top);
      Object.assign(press.band.style, {
        left: `${left}px`, top: `${shownTop}px`, width: `${right - left}px`, height: `${Math.max(0, Math.min(r.bottom, bottom) - shownTop)}px`,
      });
      const hit = rows.filter((el) => {
        const b = el.getBoundingClientRect();
        return b.bottom > top && b.top < bottom && b.right > left && b.left < right;
      });
      api.current.set(selectIds(hit.map((el) => el.dataset.blockId)));
    }

    // One frame of the drag: the list scrolls while the pointer is near or
    // past its top or bottom edge, and the selection follows.
    function frame() {
      raf = 0;
      if (!press?.mode) return;
      const r = press.list.getBoundingClientRect();
      const over = press.cy < r.top + EDGE ? press.cy - (r.top + EDGE) : press.cy > r.bottom - EDGE ? press.cy - (r.bottom - EDGE) : 0;
      if (over) press.list.scrollTop += Math.max(-SCROLL_MOST, Math.min(SCROLL_MOST, over / 2));
      update();
      if (over) raf = requestAnimationFrame(frame);
    }

    function end() {
      cancelAnimationFrame(raf);
      raf = 0;
      press.list.classList.remove("blockSelecting");
      press.band?.remove();
      press = null;
    }

    function onDown(e) {
      if (e.button !== 0) return;
      const o = opts.current, t = e.target;
      const list = o.enabled ? o.list() : null;
      if (!list || !list.contains(t)) {
        if (selRef.current.ids.size && !t.closest?.(KEEPS)) api.current.set(NO_SELECTION);
        return;
      }
      if (t.closest(INERT)) return;
      // A finger or a pen selects nothing here (the handle's tap still
      // does); its press lets the selection go.
      if (lastPointer() !== "mouse") {
        if (selRef.current.ids.size) api.current.set(NO_SELECTION);
        return;
      }
      const row = t.closest(".blockRowWrap");
      const id = row?.dataset.blockId || null;
      const mods = e.ctrlKey || e.metaKey || e.altKey;
      if (id && e.shiftKey && (e.altKey || e.metaKey) && !e.ctrlKey) {
        take(e);
        o.closeEditor();
        api.current.set(toggleSelected(selRef.current, id));
        return;
      }
      if (id && e.shiftKey && !mods) {
        // Within the note being written in, its text selection grows.
        if (o.view().editingId === id) return;
        take(e);
        const anchor = selRef.current.ids.size ? selRef.current.anchor : o.anchorOf();
        o.closeEditor();
        api.current.set(anchor ? selectRange(o.tree(), o.view(), anchor, id) : selectOne(id));
        return;
      }
      // Ctrl and ⌘ are the chat's (a note or its text attached); the rest the browser's.
      if (mods || e.shiftKey) return;
      if (selRef.current.ids.size) api.current.set(NO_SELECTION);
      if (!id && !BLANK.some((c) => t.classList?.contains(c))) return;
      press = { x: e.clientX, y: e.clientY, cx: e.clientX, cy: e.clientY, startId: id, startEl: row, mode: null, list, scroll0: list.scrollTop, rows: null, band: null };
    }

    function onMove(e) {
      if (!press) return;
      if (!(e.buttons & 1)) { end(); return; }
      press.cx = e.clientX;
      press.cy = e.clientY;
      if (!press.mode) {
        if (Math.hypot(press.cx - press.x, press.cy - press.y) < DRAG_MIN) return;
        if (!press.startId) begin("box");
        else {
          // Still over the note it started in: its text is being selected.
          const r = press.startEl.getBoundingClientRect();
          if (press.cy >= r.top && press.cy < r.bottom) return;
          begin("blocks");
        }
      }
      if (!raf) raf = requestAnimationFrame(frame);
    }

    function onUp() {
      if (!press) return;
      const dragged = !!press.mode;
      end();
      // The click the release makes is no click on what it landed on.
      if (!dragged) return;
      window.addEventListener("click", swallow, true);
      setTimeout(() => window.removeEventListener("click", swallow, true), 0);
    }

    // Ahead of every other key handler, and only while notes are selected
    // and nothing that takes typing (an editor, the chat, a field), a menu
    // or a focused control has the keys.
    function onKey(e) {
      if (!selRef.current.ids.size || !opts.current.enabled || composing(e)) return;
      const active = document.activeElement;
      if (isTextField(active) || active?.closest?.(OWN_KEYS) || document.querySelector(KEEPS)) return;
      const action = KEYS[chordFromEvent(e)];
      if (!action) return;
      e.preventDefault();
      e.stopPropagation();
      api.current.run(action);
    }

    document.addEventListener("mousedown", onDown, true);
    document.addEventListener("mousemove", onMove, true);
    document.addEventListener("mouseup", onUp, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      if (press) end();
      document.removeEventListener("mousedown", onDown, true);
      document.removeEventListener("mousemove", onMove, true);
      document.removeEventListener("mouseup", onUp, true);
      window.removeEventListener("keydown", onKey, true);
    };
  }, []);

  const { run, clear, handleSelect, dragSelection, selectBlock, selectEvery } = api.current;
  return { ids: selection.ids, run, clear, handleSelect, dragSelection, selectBlock, selectEvery };
}
