// The Files window's tree (docs/dev/work_folders.md "The Files window";
// docs/dev/ui-design.md "Trees"): the library's folders, a synced folder's
// directories and the files in each open one (explorer/fileTree.js
// treeRows), a WAI-ARIA tree with one row in the tab order (useTree.js),
// each row a TreeRow. A press on a folder opens or closes it, a press on a
// file selects and opens it: one press, for a mouse and a finger alike (a
// navigator, where the folder view, FolderTree.jsx, selects on a click and
// opens on a second). A right-click, a held finger or the row's "⋯" opens the
// library's menu for the row. A synced folder's own row wears the synced
// folder's glyph, its arrows in its sync's tone, and says the computer that
// keeps it and how its sync stands (a StateTag).
import { useLayoutEffect, useMemo, useRef, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { FolderIcon, SyncedFolderIcon } from "../shared/ui/Icons";
import { StateTag } from "../shared/ui/StateTag";
import { menuPress as holdPress } from "../shared/ui/press.js";
import { EMPTY_TREE } from "../library/libraryUtils.js";
import { Empty } from "../settings/SettingsKit";
import { PAGE_ROWS } from "./directoryRows.js";
import { revealLimits, revealSettled, treeRows } from "./fileTree.js";
import TreeRow from "./TreeRow.jsx";
import { useTree } from "./useTree.js";

// `files` the window's state (useFilesWindow.js), `tree` its reads
// ({dirs, reveal}, useFilesTree.js); `libTree`, `workTree`, `folderMeta`,
// `libraryPages` and `lib` App's listing (fileTree.js treeRows). onOpen(id,
// row) opens a file's page; pageMenu(id, name) and folderMenu(id) are App's
// menu openers, `menuPress` its gesture helper (shared/ui/press.js);
// onDragStart(e, id) starts a page's drag, onDrop(e, folderId) takes one on
// a library folder (a directory takes none), `dragOver` / setDragOver(key)
// the drop target shown; keeperOf(id) a synced folder's keeper (a
// StateTag's state with its glyph's `badge`, or null:
// useWorkFolderActions.jsx).
export default function FileTree({ files, tree, libTree, workTree, folderMeta, libraryPages, lib, onOpen, pageMenu, folderMenu,
  menuPress = holdPress, onDragStart, onDrop, dragOver, setDragOver, keeperOf }) {
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
  const organize = !!lib?.organize;

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
  const keys = useTree({
    rows, active: files.active, setActive: files.setActive, selectedId: files.selected, menuOf,
    act: (kind, row) => {
      if (kind === "toggle") files.toggleExpanded(row.id);
      else if (kind === "open") press(row);
      else if (kind === "select") select(row);
    },
  });

  // The open page revealed (useFilesTree.js): its row scrolled into sight
  // once it is drawn, which may be a read or two later, and the keys on it;
  // let go once what can be drawn is and the row is not among it.
  const revealed = useRef(0);
  useLayoutEffect(() => {
    if (revealed.current === nonce) return;
    const row = rows.find((r) => r.kind === "file" && r.id === pageId);
    const el = row && keys.rowRefs.current.get(row.key);
    if (!el) {
      if (revealSettled(rows, tree.dirs, tree.reveal)) revealed.current = nonce;
      return;
    }
    revealed.current = nonce;
    el.scrollIntoView({ block: "nearest" });
    files.setActive(row.key);
  });

  // No folders at all, once the listing is in (EMPTY_TREE until then, when
  // nothing shows, as the library listing does).
  if (!rows.length && libTree !== EMPTY_TREE) {
    return <Empty icon={FolderIcon}>{t("No folders yet. The library's folders and synced folders show here.")}</Empty>;
  }
  return (
    <div className="fileTree" aria-label={t("Folders and files")} {...keys.treeProps}>
      {rows.map((row) => {
        const own = keys.rowProps(row);
        if (row.kind === "more" || row.kind === "note") return <TreeRow key={row.key} row={row} {...own} onPress={press} />;
        const folder = row.kind === "folder";
        const menu = menuOf(row);
        const keeper = row.root ? keeperOf?.(row.id) : null;
        const drop = folder && organize && !row.work ? {
          onDragOver: (e) => { e.preventDefault(); if (dragOver !== `tree:${row.id}`) setDragOver(`tree:${row.id}`); },
          onDragLeave: () => setDragOver(null),
          onDrop: (e) => onDrop(e, row.id),
        } : {};
        const drag = !folder && organize ? { draggable: true, onDragStart: (e) => onDragStart(e, row.id) } : {};
        return (
          <TreeRow key={row.key} row={row} {...own} selected={files.selected === row.id} menu={menu}
            className={dragOver === `tree:${row.id}` ? "dragOver" : ""} title={row.name} onClick={() => press(row)}
            icon={row.root ? <SyncedFolderIcon size={16} badge={keeper?.badge || ""} /> : null}
            after={keeper ? <span className="fileTreeKeeper"><StateTag state={keeper} className="keeperTag" /></span>
              : folder && row.count ? <span className="fileTreeCount">{row.count}</span> : null}
            {...menuPress(menu)} {...drop} {...drag} />
        );
      })}
    </div>
  );
}
