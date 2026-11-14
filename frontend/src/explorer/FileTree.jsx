// The Files window's tree (docs/dev/work_folders.md "The Files window";
// docs/dev/ui-design.md "Keyboard focus"): the library's folders, a work
// folder's directories and the files in each open one (explorer/
// fileTree.js treeRows), a WAI-ARIA tree with one row in the tab order. A
// press on a folder opens or closes it, a press on a file selects and opens
// it: one press, for a mouse and a finger alike (a navigator, where the
// home list view selects on a click and opens on a second). A right-click,
// a held finger or the row's "⋯" opens the library's menu for the row.
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { FileGlyph, FolderIcon } from "../shared/ui/Icons";
import { MenuButton } from "../shared/ui/Menus";
import { Chevron } from "../shared/ui/MotionWidgets";
import { menuPress as holdPress, pressAt } from "../shared/ui/press.js";
import { EMPTY_TREE } from "../library/libraryUtils.js";
import { Empty } from "../settings/SettingsKit";
import { PAGE_ROWS } from "./directoryRows.js";
import { focusAfter, keyAction, revealLimits, revealSettled, tabStop, treeRows } from "./fileTree.js";

// `files` the window's state (useFilesWindow.js), `tree` its reads
// ({dirs, reveal}, useFilesTree.js); `libTree`, `workTree`, `folderMeta`,
// `libraryPages` and `lib` App's listing (fileTree.js treeRows). onOpen(id,
// row) opens a file's page; pageMenu(id, name) and folderMenu(id) are App's
// menu openers, `menuPress` its gesture helper (shared/ui/press.js);
// onDragStart(e, id) starts a page's drag, onDrop(e, folderId) takes one on
// a library folder (a directory takes none), `dragOver` / setDragOver(key)
// the drop target shown.
export default function FileTree({ files, tree, libTree, workTree, folderMeta, libraryPages, lib, onOpen, pageMenu, folderMenu,
  menuPress = holdPress, onDragStart, onDrop, dragOver, setDragOver }) {
  const { pageId, nonce } = tree.reveal;
  // how many of a library folder's pages are shown, grown a page at a time,
  // and at a reveal down to the open page
  const [limits, setLimits] = useState({});
  const [limitsAt, setLimitsAt] = useState(0);
  if (limitsAt !== nonce) {
    setLimitsAt(nonce);
    setLimits((l) => revealLimits(libraryPages, l, pageId));
  }
  const rows = useMemo(
    () => treeRows({ libTree, workTree, folderMeta, libraryPages, dirs: tree.dirs, expanded: files.expanded, lib, limits }),
    [libTree, workTree, folderMeta, libraryPages, tree.dirs, files.expanded, lib, limits],
  );
  const stop = tabStop(rows, files.active, files.selected);
  const rowRefs = useRef(new Map());
  const organize = !!lib?.organize;

  const focusRow = (key) => {
    files.setActive(key);
    rowRefs.current.get(key)?.focus();
  };
  const select = (row) => files.select(row.kind === "file" ? row.page : { id: row.id });
  function press(row) {
    if (row.kind === "folder") {
      files.toggleExpanded(row.id);
      select(row);
    } else if (row.kind === "file") {
      select(row);
      onOpen(row.id, row.page);
    } else if (row.kind === "more") {
      if (row.work) tree.dirs.more(row.id);
      else setLimits((l) => ({ ...l, [row.id]: (l[row.id] || PAGE_ROWS) + PAGE_ROWS }));
    } else if (row.failed) {
      tree.dirs.retry(row.id);
    }
  }
  const menuOf = (row) => {
    if (row.kind === "file") return (e) => { select(row); pageMenu(row.id, row.name)(e); };
    if (row.kind === "folder") return (e) => { select(row); folderMenu(row.id)(e); };
    return null;
  };

  function onKeyDown(e) {
    if (e.altKey || e.ctrlKey || e.metaKey || (e.shiftKey && e.key !== "F10")) return;
    if (e.target !== e.currentTarget && e.target.getAttribute("role") !== "treeitem") return;
    const act = keyAction(rows, e.target.dataset.key || stop, e.shiftKey ? "Shift+F10" : e.key);
    if (!act.kind) return;
    e.preventDefault();
    e.stopPropagation();
    const row = rows.find((r) => r.key === act.key);
    if (act.kind === "move" || act.kind === "parent") focusRow(act.key);
    else if (act.kind === "expand" || act.kind === "collapse" || act.kind === "toggle") files.toggleExpanded(row.id);
    else if (act.kind === "open") press(row);
    else if (act.kind === "select") select(row);
    else if (act.kind === "menu") {
      const el = rowRefs.current.get(row.key);
      const box = el.getBoundingClientRect();
      menuOf(row)(pressAt(box.left + 24, box.bottom - 4, el));
    }
  }

  // The open page revealed (useFilesTree.js): its row scrolled into sight
  // once it is drawn, which may be a read or two later, and the keys on it;
  // let go once what can be drawn is and the row is not among it.
  const revealed = useRef(0);
  useLayoutEffect(() => {
    if (revealed.current === nonce) return;
    const row = rows.find((r) => r.kind === "file" && r.id === pageId);
    const el = row && rowRefs.current.get(row.key);
    if (!el) {
      if (revealSettled(rows, tree.dirs, tree.reveal)) revealed.current = nonce;
      return;
    }
    revealed.current = nonce;
    el.scrollIntoView({ block: "nearest" });
    files.setActive(row.key);
  });

  // The keys kept in the tree when the row they were on goes (a "Show
  // more" row read, a note row replaced by its files) and the focus with
  // it: the row now in its place takes both. A blur out of the tree lets
  // go; a row taken away fires none.
  const inTree = useRef(false);
  const drawn = useRef(rows);
  useLayoutEffect(() => {
    const before = drawn.current;
    drawn.current = rows;
    const key = focusAfter(before, rows, files.active);
    if (key && inTree.current && (!document.activeElement || document.activeElement === document.body)) focusRow(key);
  });

  // No folders at all, once the listing is in (EMPTY_TREE until then, when
  // nothing shows, as the library listing does).
  if (!rows.length && libTree !== EMPTY_TREE) {
    return <Empty icon={FolderIcon}>{t("No folders yet. The library's folders and work folders show here.")}</Empty>;
  }
  return (
    <div className="fileTree" role="tree" aria-label={t("Folders and files")} onKeyDown={onKeyDown}
      onFocus={() => { inTree.current = true; }}
      onBlur={(e) => { if (!e.currentTarget.contains(e.relatedTarget)) inTree.current = false; }}>
      {rows.map((row) => {
        const ref = (el) => { if (el) rowRefs.current.set(row.key, el); else rowRefs.current.delete(row.key); };
        const item = {
          ref, role: "treeitem", "aria-level": row.level, tabIndex: row.key === stop ? 0 : -1,
          onFocus: (e) => { if (e.target === e.currentTarget) files.setActive(row.key); },
          "data-kind": row.kind, "data-id": row.id, "data-key": row.key, style: { "--level": row.level },
        };
        if (row.kind === "more") {
          return (
            <button key={row.key} type="button" {...item} className={`fileTreeRow fileTreeMore${row.key === files.active ? " active" : ""}`}
              aria-selected={false} aria-disabled={row.loading || undefined} onClick={() => press(row)}>
              <span className="fileTreeChevron" />
              <span className="fileTreeName">{row.name}</span>
            </button>
          );
        }
        if (row.kind === "note") {
          return (
            <div key={row.key} {...item} className={`fileTreeRow fileTreeNote${row.key === files.active ? " active" : ""}`} aria-selected={false}>
              <span className="fileTreeChevron" />
              <span className="fileTreeName">{row.name}</span>
              {row.failed ? (
                <button type="button" className="uiBtn sm" tabIndex={-1} onClick={() => press(row)}>{t("Try again")}</button>
              ) : null}
            </div>
          );
        }
        const folder = row.kind === "folder";
        const selected = files.selected === row.id;
        const menu = menuOf(row);
        const drop = folder && organize && !row.work ? {
          onDragOver: (e) => { e.preventDefault(); if (dragOver !== `tree:${row.id}`) setDragOver(`tree:${row.id}`); },
          onDragLeave: () => setDragOver(null),
          onDrop: (e) => onDrop(e, row.id),
        } : {};
        const drag = !folder && organize ? { draggable: true, onDragStart: (e) => onDragStart(e, row.id) } : {};
        return (
          <div key={row.key} {...item} aria-expanded={folder ? row.expanded : undefined} aria-selected={selected}
            className={`fileTreeRow rowMenuHost${selected ? " selected" : ""}${row.key === files.active ? " active" : ""}${
              dragOver === `tree:${row.id}` ? " dragOver" : ""}`}
            title={row.name} onClick={() => press(row)} {...menuPress(menu)} {...drop} {...drag}>
            <span className="fileTreeChevron">{folder ? <Chevron open={row.expanded} dir="right" /> : null}</span>
            {folder ? <FolderIcon size={16} /> : <FileGlyph format={row.format} size={16} />}
            <span className="fileTreeName">{row.name}</span>
            {folder && row.count ? <span className="fileTreeCount">{row.count}</span> : null}
            <MenuButton className="fileTreeMenu" open={menu} tabIndex={-1} />
          </div>
        );
      })}
    </div>
  );
}
