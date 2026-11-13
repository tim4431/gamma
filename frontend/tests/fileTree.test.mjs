// The Files window's tree as rows (explorer/fileTree.js): the order, the
// lazy rows of a work folder's directories, the "more" and "note" rows, a
// page filed in two folders, a folder share's clamp, the keys and the one
// row in the tab order; and the folders the reveal opens
// (explorer/workPaths.js revealPath).
import assert from "node:assert/strict";
import { test } from "node:test";
import { NONE } from "../src/explorer/directoryRows.js";
import { focusAfter, keyAction, revealLimits, revealSettled, tabStop, treeRows } from "../src/explorer/fileTree.js";
import { revealPath } from "../src/explorer/workPaths.js";
import { treeOf } from "../src/explorer/workFolders.js";
import { libraryTree } from "../src/library/libraryUtils.js";

// [id, parent, name, mark]: the listing's nested folders, as the app reads them.
function treeFrom(rows) {
  const blocks = new Map(rows.map(([id, , name, mark]) => [id, { id, content: name, position: "",
    properties: mark ? { vault: { pull: true } } : {}, children: [] }]));
  const top = [];
  for (const [id, parent] of rows) (parent ? blocks.get(parent).children : top).push(blocks.get(id));
  return libraryTree({ folders: { children: top } });
}
const LIB = treeFrom([
  ["shelf", "", "Shelf", false],
  ["deep", "shelf", "Deep", false],
  ["W", "", "Paper", true],
  ["sec", "W", "sections", false],
  ["fig", "W", "figures", false],
  ["d10", "W", "dir 10", false],
  ["d9", "W", "dir 9", false],
  ["raw", "fig", "raw", false],
  ["empty", "", "Empty", false],
]);
const WORK = treeOf(LIB);
const page = (id, title, folders, more = {}) => ({ id, _pageId: id, content: title, _folders: folders, _doc: null, _pinned: "", ...more });
const PAGES = [page("p2", "Paper 10", ["shelf"]), page("p1", "Paper 9", ["shelf", "deep"]), page("loose", "Loose", [])];
const fileRow = (id, name, format = "pdf") => ({ id, content: name.replace(/\.[^.]+$/, ""),
  properties: { work_folder: "W", file_name: name, folders: ["W"], doc: { format, name, hash: "h", ext: `.${format}` } } });
const dirsOf = (reads) => ({ of: (id) => reads[id] || NONE });
const read = (rows, more = {}) => ({ ...NONE, rows, total: rows.length, loaded: true, count: rows.length, ...more });
const keys = (rows) => rows.map((r) => r.key);
const rowsOf = (expanded, reads = {}, more = {}) => treeRows({ libTree: LIB, workTree: WORK, folderMeta: { shelf: { count: 2 }, W: { count: 7 } },
  libraryPages: PAGES, dirs: dirsOf(reads), expanded, ...more });

test("the top level in the tree's order, closed, each folder with its count", () => {
  const rows = rowsOf([]);
  assert.deepEqual(keys(rows), ["folder:shelf", "folder:W", "folder:empty"]);
  assert.deepEqual(rows.map((r) => [r.name, r.level, r.expanded, r.count, r.work]),
    [["Shelf", 1, false, 2, ""], ["Paper", 1, false, 7, "W"], ["Empty", 1, false, 0, ""]]);
});

test("a library folder: its folders in order, then its pages by title, numbers as numbers; a page in two folders under each", () => {
  const rows = rowsOf(["shelf", "deep"]);
  assert.deepEqual(keys(rows), ["folder:shelf", "folder:deep", "file:deep:p1", "file:shelf:p1", "file:shelf:p2", "folder:W", "folder:empty"]);
  const p1 = rows.find((r) => r.key === "file:shelf:p1");
  assert.equal(p1.parent, "folder:shelf");
  assert.equal(p1.level, 2);
  assert.equal(p1.page, PAGES[1], "a file row carries its page");
  assert.equal(rows.find((r) => r.key === "file:deep:p1").level, 3);
  assert.ok(!rows.some((r) => r.id === "loose"), "a page filed in no folder has no place");
});

test("an empty library folder says so", () => {
  const rows = rowsOf(["empty"]);
  const note = rows.find((r) => r.kind === "note");
  assert.equal(note.key, "note:empty");
  assert.equal(note.name, "No pages in this folder.");
});

test("a library folder of many pages shows a page of them and a more row, grown by its limit", () => {
  const many = Array.from({ length: 450 }, (_, i) => page(`m${i}`, `Note ${i}`, ["empty"]));
  const at = (limits) => treeRows({ libTree: LIB, workTree: WORK, libraryPages: many, dirs: dirsOf({}), expanded: ["empty"], limits });
  let rows = at({});
  assert.equal(rows.filter((r) => r.kind === "file").length, 200);
  assert.equal(rows.at(-1).kind, "more");
  assert.equal(rows.at(-1).count, 200);
  assert.equal(rows.at(-1).name, "Show 200 more files");
  rows = at({ empty: 400 });
  assert.equal(rows.at(-1).count, 50, "what a press adds: the rest");
  rows = at({ empty: 600 });
  assert.equal(rows.filter((r) => r.kind === "file").length, 450);
  assert.notEqual(rows.at(-1).kind, "more");
  const files = rows.filter((r) => r.kind === "file");
  assert.deepEqual(files.slice(0, 3).map((r) => r.name), ["Note 0", "Note 1", "Note 2"], "numbers sort as numbers");
  assert.equal(files.at(-1).name, "Note 449");
});

test("a work folder: its directories by name, numbers as numbers, then the files read, in the server's order", () => {
  const rows = rowsOf(["W"], { W: read([fileRow("f2", "main.tex", "text"), fileRow("f1", "main.pdf")]) });
  assert.deepEqual(rows.map((r) => r.name), ["Shelf", "Paper", "dir 9", "dir 10", "figures", "sections", "main.tex", "main.pdf", "Empty"]);
  const main = rows.find((r) => r.id === "f2");
  assert.deepEqual([main.kind, main.level, main.format, main.work, main.parent], ["file", 2, "text", "W", "folder:W"]);
  assert.equal(rows.find((r) => r.id === "f1").format, "pdf");
});

test("a directory not read yet says it is being read; nothing is drawn of a closed one", () => {
  let rows = rowsOf(["W"]);
  const note = rows.find((r) => r.kind === "note");
  assert.deepEqual([note.key, note.name, note.loading, note.work], ["note:W", "Reading…", true, "W"]);
  rows = rowsOf(["W"], { W: { ...NONE, loading: true } });
  assert.equal(rows.find((r) => r.kind === "note").name, "Reading…");
  rows = rowsOf(["fig"], { fig: read([fileRow("x", "x.pdf")]) });
  assert.ok(!rows.some((r) => r.id === "x"), "a directory under a closed folder is not drawn");
});

test("a failed read says so with its retry, a cut list has a more row, an empty directory says it holds nothing", () => {
  let rows = rowsOf(["W"], { W: { ...NONE, failed: true } });
  let note = rows.find((r) => r.kind === "note");
  assert.deepEqual([note.name, note.failed], ["Could not read this folder.", true]);
  const part = Array.from({ length: 200 }, (_, i) => fileRow(`f${i}`, `f${i}.pdf`));
  rows = rowsOf(["W"], { W: read(part, { total: 10_000, next: 200 }) });
  const more = rows.find((r) => r.kind === "more");
  assert.deepEqual([more.key, more.count, more.work, more.name], ["more:W", 200, "W", "Show 200 more files"]);
  rows = rowsOf(["W"], { W: read(part, { total: 250, next: 200, loading: true }) });
  assert.deepEqual([rows.find((r) => r.kind === "more").count, rows.find((r) => r.kind === "more").loading], [50, true]);
  rows = rowsOf(["W", "raw", "fig"], { W: read([]), fig: read([]), raw: read([]) });
  note = rows.filter((r) => r.kind === "note");
  assert.deepEqual(note.map((r) => [r.id, r.name]), [["raw", "No tracked files here."]],
    "a directory with directories in it lists them and says nothing");
});

test("a folder share keeps the tree to its folder", () => {
  const lib = { contains: (id) => id === "W" || WORK.work.get(id) === "W" };
  assert.deepEqual(keys(rowsOf([], {}, { lib })), ["folder:W"]);
});

test("the reveal opens the folders from the top down to the page's first folder", () => {
  assert.deepEqual(revealPath(LIB, { _folders: ["raw", "shelf"] }), ["W", "fig", "raw"]);
  assert.deepEqual(revealPath(LIB, { properties: { folders: ["deep"] } }), ["shelf", "deep"]);
  assert.deepEqual(revealPath(LIB, { _folders: [] }), []);
  assert.deepEqual(revealPath(LIB, { _folders: ["gone"] }), []);
  assert.deepEqual(revealPath(LIB, null), []);
});

test("the keys: moves, open and close, the parent, open, select and the menu", () => {
  const rows = rowsOf(["W", "fig"], { W: read([fileRow("main", "main.tex", "text")]), fig: read([]) });
  // Shelf, Paper, dir 9, dir 10, figures, raw, sections, main.tex, Empty
  const key = (name) => rows.find((r) => r.name === name).key;
  const act = (from, k) => keyAction(rows, from, k);
  assert.deepEqual(act(key("Shelf"), "ArrowDown"), { kind: "move", key: key("Paper") });
  assert.deepEqual(act(key("Shelf"), "ArrowUp"), { kind: null, key: "" }, "no wrap at the top");
  assert.deepEqual(act(key("Empty"), "ArrowDown"), { kind: null, key: "" }, "no wrap at the bottom");
  assert.deepEqual(act(key("main.tex"), "ArrowUp"), { kind: "move", key: key("sections") });
  assert.deepEqual(act(key("raw"), "Home"), { kind: "move", key: key("Shelf") });
  assert.deepEqual(act(key("raw"), "End"), { kind: "move", key: key("Empty") });
  assert.deepEqual(act(key("Shelf"), "ArrowRight"), { kind: "expand", key: key("Shelf") }, "a closed folder opens");
  assert.deepEqual(act(key("Paper"), "ArrowRight"), { kind: "move", key: key("dir 9") }, "an open one: into it");
  assert.deepEqual(act(key("raw"), "ArrowRight"), { kind: "expand", key: key("raw") });
  assert.deepEqual(act(key("main.tex"), "ArrowRight"), { kind: null, key: "" }, "a file: nothing");
  assert.deepEqual(act(key("figures"), "ArrowLeft"), { kind: "collapse", key: key("figures") });
  assert.deepEqual(act(key("raw"), "ArrowLeft"), { kind: "parent", key: key("figures") });
  assert.deepEqual(act(key("main.tex"), "ArrowLeft"), { kind: "parent", key: key("Paper") });
  assert.deepEqual(act(key("Shelf"), "ArrowLeft"), { kind: null, key: "" }, "the top has no parent");
  assert.deepEqual(act(key("main.tex"), "Enter"), { kind: "open", key: key("main.tex") });
  assert.deepEqual(act(key("Paper"), "Enter"), { kind: "toggle", key: key("Paper") });
  assert.deepEqual(act(key("main.tex"), " "), { kind: "select", key: key("main.tex") });
  assert.deepEqual(act(key("main.tex"), "Shift+F10"), { kind: "menu", key: key("main.tex") });
  assert.deepEqual(act(key("Paper"), "ContextMenu"), { kind: "menu", key: key("Paper") });
  assert.deepEqual(act(key("Paper"), "a"), { kind: null, key: "" }, "no typeahead");
  assert.deepEqual(act("", "ArrowDown"), { kind: "move", key: key("Shelf") }, "from nowhere: the first");
  assert.deepEqual(act("gone", "ArrowUp"), { kind: "move", key: key("Empty") });
  assert.deepEqual(keyAction([], "", "ArrowDown"), { kind: null, key: "" });
});

test("the keys on a more row and a note: Enter reads, no menu, no select", () => {
  const part = Array.from({ length: 2 }, (_, i) => fileRow(`f${i}`, `f${i}.pdf`));
  const rows = rowsOf(["W"], { W: read(part, { total: 300, next: 2 }) });
  assert.deepEqual(keyAction(rows, "more:W", "Enter"), { kind: "open", key: "more:W" });
  assert.deepEqual(keyAction(rows, "more:W", " "), { kind: null, key: "" });
  assert.deepEqual(keyAction(rows, "more:W", "ContextMenu"), { kind: null, key: "" });
  assert.deepEqual(keyAction(rows, "more:W", "ArrowLeft"), { kind: "parent", key: "folder:W" });
});

test("one row in the tab order: the active one, else the selected, else the first", () => {
  const rows = rowsOf(["shelf"]);
  assert.equal(tabStop(rows, "folder:W", "p1"), "folder:W");
  assert.equal(tabStop(rows, "gone", "p1"), "file:shelf:p1", "the selected page's first row");
  assert.equal(tabStop(rows, "", "W"), "folder:W", "a selected folder");
  assert.equal(tabStop(rows, "", "nothing"), "folder:shelf");
  assert.equal(tabStop(rows, "", ""), "folder:shelf");
  assert.equal(tabStop([], "x", "y"), "");
});

test("the reveal of a library page past the first 200 by title shows its folder down to it", () => {
  const many = Array.from({ length: 300 }, (_, i) => page(`m${i}`, `Note ${String(i).padStart(3, "0")}`,
    i === 230 ? ["empty", "shelf"] : ["empty"]));
  const limits = revealLimits(many, {}, "m230");
  assert.deepEqual(limits, { empty: 400 }, "its place, the 231st, a page of 200 at a time; under Shelf it is the first");
  const rows = treeRows({ libTree: LIB, workTree: WORK, libraryPages: many, dirs: dirsOf({}), expanded: ["empty"], limits });
  assert.ok(rows.some((r) => r.key === "file:empty:m230"));
  assert.equal(revealLimits(many, limits, "m230"), limits, "nothing grows: the same object");
  assert.equal(revealLimits(many, { empty: 600 }, "m230").empty, 600, "a folder shown further stays so");
  assert.equal(revealLimits(many, limits, "m5"), limits);
  assert.equal(revealLimits(many, limits, ""), limits);
});

test("a reveal is let go once what can be drawn is", () => {
  const part = Array.from({ length: 2 }, (_, i) => fileRow(`f${i}`, `f${i}.pdf`));
  const settled = (reads, reveal, expanded = ["W"]) => revealSettled(rowsOf(expanded, reads), dirsOf(reads), reveal);
  assert.equal(settled({}, { pageId: "x", dir: "W" }), false, "its directory not read yet");
  assert.equal(settled({ W: read(part, { total: 300, next: 2 }) }, { pageId: "x", dir: "W" }), false, "still paging");
  assert.equal(settled({ W: read(part, { loading: true }) }, { pageId: "x", dir: "W" }), false, "a read in flight");
  assert.equal(settled({ W: read(part) }, { pageId: "x", dir: "W" }), true, "read to the end without it");
  assert.equal(settled({ W: read(part, { failed: true }) }, { pageId: "x", dir: "W" }), true, "a failed read");
  assert.equal(settled({}, { pageId: "p1", dir: "shelf" }, ["shelf"]), true, "a library folder draws at once");
  assert.equal(settled({}, { pageId: "x", dir: "gone" }), true, "a folder the tree does not draw");
  assert.equal(settled({}, { pageId: "x", dir: "W" }, []), true, "a folder not open");
});

test("the keys move to the row now where the one they were on went", () => {
  const part = Array.from({ length: 2 }, (_, i) => fileRow(`f${i}`, `f${i}.pdf`));
  const before = rowsOf(["W"], { W: read(part, { total: 4, next: 2 }) });
  const after = rowsOf(["W"], { W: read([...part, fileRow("f2", "f2.pdf"), fileRow("f3", "f3.pdf")]) });
  assert.equal(focusAfter(before, after, "more:W"), "file:W:f2", "the first file the read added");
  assert.equal(focusAfter(rowsOf(["W"], {}), rowsOf(["W"], { W: read(part) }), "note:W"), "file:W:f0",
    "the first file the note waited for");
  assert.equal(focusAfter(before, before, "more:W"), "", "still drawn");
  assert.equal(focusAfter(after, after, "gone"), "", "not drawn before either");
  assert.equal(focusAfter(before, [], "more:W"), "");
  assert.equal(focusAfter(before, after.slice(0, 2), "more:W"), after[1].key, "a shorter list: its last");
});
