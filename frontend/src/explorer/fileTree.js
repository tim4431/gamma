// The Files window's tree as rows (docs/dev/work_folders.md "The Files
// window"): the library's folders in their order, a work folder's
// directories by name, then each open folder's files, and what the keys do
// on them (a WAI-ARIA tree with one row in the tab order). Part of the
// window's lazy chunk: App imports nothing from here. Pure:
// tests/fileTree.test.mjs.
import { pageDocument } from "../documents/pageDocument.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { NONE, PAGE_ROWS, findStep } from "./directoryRows.js";
import { workOf } from "./workFolders.js";
import { fileNameOf } from "./workPaths.js";

const byName = (a, b) => a.localeCompare(b, undefined, { numeric: true });
const byTitle = (a, b) => byName(a.content || "", b.content || "");

// The rows the tree draws, top to bottom: [{key, kind ("folder", "file",
// "more" or "note"), id (a page's, or the folder's a "more" or "note" row
// is in), level (1 at the top), name, expanded, work (W's id, "" outside a
// work folder), count, format, pinned, parent (the folder
// row's key, "" at the top), page (a file's row), loading, failed}].
// `libTree` the listing's tree (libraryUtils.libraryTree), `workTree`
// treeOf it, `folderMeta` App's rollup (a folder's count: its pages, or in
// a work folder the files below it), `libraryPages` the pages the library
// lists (pageBlocksOf rows), `dirs` the directories' reads
// (useDirectoryRows: of(id)), `expanded` the open folders' ids, `lib` the
// library's access (its `contains` keeps a folder share's tree to the
// share), `limits` how many of a library folder's pages are shown (folder
// id → n, PAGE_ROWS by default). A library folder lists its subfolders in
// the tree's order, then the pages filed in it by title (a page in two
// folders under each); a work folder and its directories list their
// directories by name, then the files the server read, in its order. A
// "more" row follows a list cut short, counting what a press on it adds
// (a page of PAGE_ROWS at most), a "note" row says a directory is
// being read, could not be read or holds nothing. Pages filed in no folder
// and labels have no place in the tree.
export function treeRows({ libTree, workTree, folderMeta = {}, libraryPages = [], dirs, expanded, lib, limits = {} }) {
  const open = expanded instanceof Set ? expanded : new Set(expanded || []);
  const filed = new Map();
  for (const b of libraryPages) {
    for (const f of b._folders || []) {
      if (!filed.has(f)) filed.set(f, []);
      filed.get(f).push(b);
    }
  }
  const rows = [];
  const note = (id, level, parent, work, kind, name) => rows.push({ key: `note:${id}`, kind: "note", id, level, name, expanded: false,
    work, count: 0, format: "", pinned: false, parent, page: null, loading: kind === "reading", failed: kind === "failed" });
  const more = (id, level, parent, work, n, loading) => rows.push({ key: `more:${id}`, kind: "more", id, level,
    name: tn("Show {n} more file", "Show {n} more files", n), expanded: false, work, count: n, format: "", pinned: false,
    parent, page: null, loading, failed: false });
  const file = (folderId, level, parent, work, page, name, format, pinned) => rows.push({ key: `file:${folderId}:${page.id}`,
    kind: "file", id: page.id, level, name, expanded: false, work, count: 0, format, pinned, parent, page, loading: false, failed: false });
  function walk(id, level, parent) {
    const folder = libTree.folders.get(id);
    if (!folder) return;
    const work = workOf(workTree, id);
    const isOpen = open.has(id);
    const key = `folder:${id}`;
    rows.push({ key, kind: "folder", id, level, name: folder.name, expanded: isOpen, work, count: folderMeta[id]?.count || 0,
      format: "", pinned: !!folder.pinned, parent, page: null, loading: false, failed: false });
    if (!isOpen) return;
    const children = work
      ? folder.children.filter((c) => libTree.folders.has(c)).sort((a, b) => byName(libTree.folders.get(a).name, libTree.folders.get(b).name))
      : folder.children;
    for (const child of children) walk(child, level + 1, key);
    const inner = level + 1;
    if (work) {
      const read = dirs?.of(id) || NONE;
      for (const r of read.rows) {
        file(id, inner, key, work, r, fileNameOf(r) || t("Untitled"), pageDocument(r)?.format || "", !!r.properties?.pinned);
      }
      if (read.failed) note(id, inner, key, work, "failed", t("Could not read this folder."));
      else if (!read.loaded) note(id, inner, key, work, "reading", t("Reading…"));
      else if (read.next != null) more(id, inner, key, work, Math.min(PAGE_ROWS, Math.max(0, read.total - read.rows.length)), read.loading);
      else if (!read.rows.length && !children.length) note(id, inner, key, work, "none", t("No tracked files here."));
      return;
    }
    const pages = [...(filed.get(id) || [])].sort(byTitle);
    const limit = limits[id] || PAGE_ROWS;
    for (const b of pages.slice(0, limit)) file(id, inner, key, "", b, b.content, b._doc?.format || "", !!b._pinned);
    if (pages.length > limit) more(id, inner, key, "", Math.min(PAGE_ROWS, pages.length - limit), false);
    else if (!pages.length && !children.length) note(id, inner, key, "", "empty", t("No pages in this folder."));
  }
  const top = lib?.contains ? libTree.top.filter(lib.contains) : libTree.top;
  for (const id of top) walk(id, 1, "");
  return rows;
}

// `limits` (treeRows') with each library folder the page `pageId` is filed
// in shown down to it, a page of PAGE_ROWS at a time: the reveal of a page
// past the first 200 by title. The same object when nothing grows.
export function revealLimits(libraryPages, limits, pageId) {
  const page = pageId ? libraryPages.find((b) => b.id === pageId) : null;
  let next = limits;
  for (const folder of page?._folders || []) {
    const at = libraryPages.filter((b) => (b._folders || []).includes(folder)).sort(byTitle).findIndex((b) => b.id === pageId);
    const want = Math.ceil((at + 1) / PAGE_ROWS) * PAGE_ROWS;
    if (want > (next[folder] || PAGE_ROWS)) next = { ...next, [folder]: want };
  }
  return next;
}

// Whether the reveal (`reveal`: {pageId, dir}, useFilesTree.js) has drawn
// what it can: its folder `dir` is not drawn open among `rows`, or, in a
// work folder, `dirs` (useDirectoryRows) are done paging for the page. A
// reveal whose row is not drawn by then is let go, rather than scrolling
// to the page whenever a later read draws it.
export function revealSettled(rows, dirs, reveal) {
  const folder = rows.find((r) => r.kind === "folder" && r.id === reveal.dir);
  if (!folder?.expanded || !folder.work) return true;
  const read = dirs?.of(reveal.dir) || NONE;
  return read.loaded && !read.loading && findStep(read, reveal.pageId) == null;
}

// The row to move the keys to when the row they were on (`activeKey`) is
// no longer drawn: a "Show more" row read, a note row replaced by the
// files it waited for. The row now where it was in `before` (the rows
// drawn last), the last when the list got shorter; "" while it is drawn,
// or was not drawn before either.
export function focusAfter(before, rows, activeKey) {
  if (!activeKey || !rows.length || rows.some((r) => r.key === activeKey)) return "";
  const at = before.findIndex((r) => r.key === activeKey);
  return at < 0 ? "" : rows[Math.min(at, rows.length - 1)].key;
}

const NOTHING = { kind: null, key: "" };

// What a key pressed on the tree does from the row `activeKey` ({kind,
// key}: the row it acts on or moves to; kind null for nothing): ArrowDown
// and ArrowUp move a row (no wrap), Home and End to the first and last;
// ArrowRight opens a closed folder and moves into an open one; ArrowLeft
// closes an open folder, else moves to the folder the row is in; Enter
// opens a file (a "more" row reads more, a failed read's note reads again)
// and opens or closes a folder; Space selects; Shift+F10 and the menu key
// open a row's menu. `key` is the event's key, "Shift+F10" for that chord.
export function keyAction(rows, activeKey, key) {
  if (!rows.length) return NOTHING;
  const at = rows.findIndex((r) => r.key === activeKey);
  const first = { kind: "move", key: rows[0].key };
  const last = { kind: "move", key: rows.at(-1).key };
  if (key === "Home") return first;
  if (key === "End") return last;
  if (at < 0) return key === "ArrowDown" ? first : key === "ArrowUp" ? last : NOTHING;
  const row = rows[at];
  switch (key) {
    case "ArrowDown": return at + 1 < rows.length ? { kind: "move", key: rows[at + 1].key } : NOTHING;
    case "ArrowUp": return at > 0 ? { kind: "move", key: rows[at - 1].key } : NOTHING;
    case "ArrowRight":
      if (row.kind !== "folder") return NOTHING;
      if (!row.expanded) return { kind: "expand", key: row.key };
      return rows[at + 1]?.parent === row.key ? { kind: "move", key: rows[at + 1].key } : NOTHING;
    case "ArrowLeft":
      if (row.kind === "folder" && row.expanded) return { kind: "collapse", key: row.key };
      return row.parent ? { kind: "parent", key: row.parent } : NOTHING;
    case "Enter": return { kind: row.kind === "folder" ? "toggle" : "open", key: row.key };
    case " ": return row.kind === "folder" || row.kind === "file" ? { kind: "select", key: row.key } : NOTHING;
    case "Shift+F10":
    case "ContextMenu": return row.kind === "folder" || row.kind === "file" ? { kind: "menu", key: row.key } : NOTHING;
    default: return NOTHING;
  }
}

// The one row in the tab order: the row the keys are on when it is drawn,
// else the selected row (`selectedId`, a page's or a folder's id), else the
// first; "" for no rows.
export function tabStop(rows, activeKey, selectedId) {
  if (!rows.length) return "";
  if (activeKey && rows.some((r) => r.key === activeKey)) return activeKey;
  const chosen = selectedId ? rows.find((r) => r.id === selectedId && (r.kind === "file" || r.kind === "folder")) : null;
  return chosen ? chosen.key : rows[0].key;
}
