// Sub-pages in the client (library/libraryUtils.js): the pages above a page
// from each page block's `_parentPage`, the path a picker names it by, the
// pages inside one, App's page list (pageBlocksOf: a sub-page filed by its
// top, its path, the count of pages inside), and the pickers that show the
// path and match on it and the top's folders (Ctrl+P and the chat's,
// librarySearch.rankLibraryPages; the @ menu, editor/mentions.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_PAGE_DEPTH, PAGE_PATH_SEP, deleteConfirm, libraryTree, pageAncestors, pageBlocksOf, pageDescendants, pagePathLabel,
  pagePlaceLabel, pagesIntoPage, topPageOf, topmostPages,
} from "../src/library/libraryUtils.js";
import { movePageRows, rankLibraryPages } from "../src/library/librarySearch.js";
import { mentionRows } from "../src/editor/mentions.js";

const page = (id, content, parent = "", extra = {}) => ({ id, content, _parentPage: parent, ...extra });
const byId = (pages) => new Map(pages.map((p) => [p.id, p]));

// Project › Notes › Week 1, a library page beside them, and a sub-page whose
// parent is not among the pages (deleted for good, or not synced here yet).
const PAGES = [
  page("proj", "Project"),
  page("notes", "Notes", "proj"),
  page("week", "Week 1", "notes"),
  page("todo", "", "proj"),
  page("lone", "Reading list"),
  page("orphan", "Orphan", "gone"),
];

test("the pages above a page, top first, as {id, title}", () => {
  const map = byId(PAGES);
  assert.deepEqual(pageAncestors(map, "week"), [{ id: "proj", title: "Project" }, { id: "notes", title: "Notes" }]);
  assert.deepEqual(pageAncestors(map, "notes"), [{ id: "proj", title: "Project" }]);
  assert.deepEqual(pageAncestors(map, "proj"), [], "a library page has none");
  assert.deepEqual(pageAncestors(map, "orphan"), [], "a parent the pages lack ends the walk");
  assert.deepEqual(pageAncestors(map, "nope"), [], "an id that is no page");
  assert.deepEqual(pageAncestors(byId([page("x", "", ""), page("y", "Y", "x")]), "y"), [{ id: "x", title: "Untitled" }]);
});

test("a page is filed by its top: the top's entry, its own for a library page", () => {
  const map = byId(PAGES);
  assert.equal(topPageOf(map, "week"), map.get("proj"));
  assert.equal(topPageOf(map, "proj"), map.get("proj"), "a library page is its own top");
  assert.equal(topPageOf(map, "orphan"), map.get("orphan"), "so is one whose parent the pages lack");
  assert.equal(topPageOf(map, "nope"), undefined, "an id that is no page");
  const loop = byId([page("a", "A", "b"), page("b", "B", "a")]);
  assert.equal(topPageOf(loop, "a"), loop.get("a"), "a page in a cycle reads as a library page");
});

test("a chain that comes back to a page, or runs too deep, means no parent", () => {
  const loop = byId([page("a", "A", "b"), page("b", "B", "a"), page("c", "C", "a")]);
  assert.deepEqual(pageAncestors(loop, "a"), []);
  assert.deepEqual(pageAncestors(loop, "c"), [], "a page under a cycle too");
  // a chain holds at most MAX_PAGE_DEPTH pages, its top included
  const deep = Array.from({ length: MAX_PAGE_DEPTH + 1 }, (_, i) => page(`p${i}`, `P${i}`, i ? `p${i - 1}` : ""));
  const map = byId(deep);
  assert.equal(pageAncestors(map, `p${MAX_PAGE_DEPTH - 1}`).length, MAX_PAGE_DEPTH - 1);
  assert.deepEqual(pageAncestors(map, `p${MAX_PAGE_DEPTH}`), []);
});

test("a sub-page's path reads as its ancestors' titles; the pages inside some, parents first", () => {
  const map = byId(PAGES);
  assert.equal(pagePathLabel(map, "week"), ["Project", "Notes"].join(PAGE_PATH_SEP));
  assert.equal(pagePathLabel(map, "week"), "Project › Notes");
  assert.equal(pagePathLabel(map, "lone"), "");
  assert.deepEqual(pageDescendants(PAGES, ["proj"]), ["notes", "todo", "week"]);
  assert.deepEqual(pageDescendants(PAGES, ["notes"]), ["week"]);
  assert.deepEqual(pageDescendants(PAGES, ["week", "lone"]), []);
  assert.deepEqual(pageDescendants(PAGES, ["proj", "notes"]), ["todo", "week"], "none of the ids given among them");
  assert.deepEqual(pageDescendants(undefined, ["proj"]), []);
  const loop = [page("a", "A", "b"), page("b", "B", "a")];
  assert.deepEqual(pageDescendants(loop, ["a"]), ["b"], "a cycle ends the walk");
});

test("Delete and the moves keep to the topmost pages picked: one inside another picked stays with it", () => {
  assert.deepEqual(topmostPages(PAGES, ["week", "proj"]), ["proj"], "a page two levels inside a picked one");
  assert.deepEqual(topmostPages(PAGES, ["notes", "week", "lone"]), ["notes", "lone"]);
  assert.deepEqual(topmostPages(PAGES, ["notes", "todo"]), ["notes", "todo"], "siblings are both topmost");
  assert.deepEqual(topmostPages(PAGES, ["orphan", "week"]), ["orphan", "week"], "a parent the pages lack is no picked page");
  assert.deepEqual(topmostPages(undefined, ["a"]), ["a"]);
  const loop = [page("a", "A", "b"), page("b", "B", "a")];
  assert.deepEqual(topmostPages(loop, ["a"]), ["a"], "a cycle ends the walk");
});

test("pages dropped on a page go inside it, the topmost of them, never into themselves or a page inside them", () => {
  // Project › Notes › Week 1 and Project › (untitled); Reading list beside them
  assert.deepEqual(pagesIntoPage(PAGES, ["lone"], "notes"), { refused: "", moving: ["lone"] });
  assert.deepEqual(pagesIntoPage(PAGES, ["week"], "lone"), { refused: "", moving: ["week"] }, "a page inside another goes too");
  assert.deepEqual(pagesIntoPage(PAGES, ["notes", "week", "lone"], "todo"), { refused: "", moving: ["notes", "lone"] },
    "a page inside a dropped one goes along inside it");
  assert.deepEqual(pagesIntoPage(PAGES, ["week", "lone"], "notes"), { refused: "", moving: ["lone"] }, "one right inside it already stays");
  assert.deepEqual(pagesIntoPage(PAGES, ["week"], "notes"), { refused: "", moving: [] }, "nothing to move: the status says so");
  assert.deepEqual(pagesIntoPage(PAGES, ["notes"], "notes"), { refused: "self", moving: [] }, "let go where it began");
  assert.deepEqual(pagesIntoPage(PAGES, ["lone", "notes"], "notes"), { refused: "self", moving: [] }, "on one of a selection");
  assert.deepEqual(pagesIntoPage(PAGES, ["proj"], "week"), { refused: "inside", moving: [] }, "never into a page inside it");
  assert.deepEqual(pagesIntoPage(PAGES, ["lone", "notes"], "week"), { refused: "inside", moving: [] }, "nor a selection into one inside one of it");
  assert.deepEqual(pagesIntoPage(PAGES, ["proj", "week"], "week"), { refused: "inside", moving: [] },
    "a picked page inside another picked one is no target either");
  assert.deepEqual(pagesIntoPage(PAGES, ["orphan"], "lone"), { refused: "", moving: ["orphan"] });
  assert.deepEqual(pagesIntoPage(undefined, ["a"], "b"), { refused: "", moving: ["a"] }, "pages the listing lacks: the server checks");
});

test("where a page is, its own title last: how Recently deleted names a sub-page's parent", () => {
  const map = byId(PAGES);
  assert.equal(pagePlaceLabel(map, "notes"), "Project › Notes");
  assert.equal(pagePlaceLabel(map, "proj"), "Project", "a library page is its own title");
  assert.equal(pagePlaceLabel(map, "todo"), "Project › Untitled");
});

test("the move dialogs match a title, then a path, and keep the listing's order on ties", () => {
  const map = byId(PAGES);
  const rows = PAGES.map((p) => ({ ...p, _path: pagePathLabel(map, p.id) }));
  const ids = (query, limit) => movePageRows(rows, query, limit).map((p) => p.id);
  assert.deepEqual(ids(""), PAGES.map((p) => p.id), "no query lists every page in order");
  assert.deepEqual(ids("", 2), ["proj", "notes"]);
  assert.equal(ids("project")[0], "proj", "the title hit first");
  assert.deepEqual(ids("project").slice(1).sort(), ["notes", "todo", "week"], "then the pages whose path names it");
  assert.deepEqual(ids("notes"), ["notes", "week"]);
  assert.deepEqual(ids("week"), ["week"]);
  // two sub-pages of one name sort by where they are
  const twins = [page("p1", "Alpha"), page("p2", "Beta"), page("n1", "Notes", "p1"), page("n2", "Notes", "p2")];
  const tmap = byId(twins);
  const trows = twins.map((p) => ({ ...p, _path: pagePathLabel(tmap, p.id) }));
  assert.deepEqual(movePageRows(trows, "notes beta").map((p) => p.id), ["n2"], "a term on the path picks one");
  assert.deepEqual(movePageRows(trows, "rdng").map((p) => p.id), [], "nothing matched");
});

test("a delete's confirmation counts the pages inside and quotes one page by its title", () => {
  const restore = "You can restore them from there for 30 days.";
  assert.deepEqual(deleteConfirm(PAGES, ["lone"]), {
    title: "Delete page",
    message: "Move \"Reading list\" to Recently deleted? You can restore it from there for 30 days.",
    inside: [],
  });
  assert.equal(deleteConfirm(PAGES, ["notes"]).message, `Move "Notes" and the 1 page inside it to Recently deleted? ${restore}`);
  const proj = deleteConfirm(PAGES, ["proj"]);
  assert.equal(proj.message, `Move "Project" and the 3 pages inside it to Recently deleted? ${restore}`);
  assert.deepEqual(proj.inside, ["notes", "todo", "week"]);
  assert.equal(deleteConfirm(PAGES, ["notes"], "Notes, renamed").message,
    `Move "Notes, renamed" and the 1 page inside it to Recently deleted? ${restore}`, "the open page's own title wins");
  assert.match(deleteConfirm(PAGES, ["missing"]).message, /^Move "Untitled" to/);

  const two = deleteConfirm(PAGES, ["lone", "orphan"]);
  assert.equal(two.title, "Delete 2 pages");
  assert.equal(two.message, `Move these 2 pages to Recently deleted? ${restore}`);
  assert.equal(deleteConfirm(PAGES, ["notes", "lone"]).message,
    `Move these 2 pages and the 1 page inside them to Recently deleted? ${restore}`);
  assert.equal(deleteConfirm(PAGES, ["proj", "notes", "lone"]).message,
    `Move these 3 pages and the 2 pages inside them to Recently deleted? ${restore}`, "a page selected is not counted again inside");
});

test("Ctrl+P and the chat's pickers: a sub-page's row carries its path, and the path matches", () => {
  const tree = libraryTree();
  const pages = PAGES.map((p, i) => ({ ...p, updated_at: `2026-01-0${i + 1}` }));
  const rows = rankLibraryPages(pages, tree, "");
  const pathOf = (id) => rows.find((r) => r.page.id === id).path;
  assert.equal(pathOf("week"), "Project › Notes");
  assert.equal(pathOf("lone"), "");
  const hits = rankLibraryPages(pages, tree, "project");
  assert.equal(hits[0].page.id, "proj", "the title hit first");
  assert.deepEqual(hits.slice(1).map((r) => r.page.id).sort(), ["notes", "todo", "week"], "then the pages inside it");
});

// Project filed in Physics; Week 1, two pages down, carries a folder of its
// own (an import's, or a merged copy's) and a label.
const TREE = libraryTree({
  folders: { children: [{ id: "phys", content: "Physics" }, { id: "stale", content: "Stale" }] },
  labels: { children: [{ id: "wk", content: "weekly" }] },
});
const LISTED = PAGES.map((p) => ({
  ...p, preview: `${p.id} first note`,
  properties: { proj: { folders: ["phys"] }, week: { folders: ["stale"], labels: ["wk"] } }[p.id] || {},
}));

test("Ctrl+P and the chat's pickers match a sub-page on its top's folders, never its own", () => {
  const ids = (query) => rankLibraryPages(LISTED, TREE, query).map((r) => r.page.id);
  assert.deepEqual(ids("physics week"), ["week"], "its top's folder and its title");
  assert.deepEqual(ids("stale"), [], "a folder it carries itself counts for nothing");
  assert.deepEqual(ids("weekly"), ["week"], "its own label");
  assert.deepEqual(ids("physics").sort(), ["notes", "proj", "todo", "week"], "the top by its own folder, the pages inside by the top's");
});

test("App's page list: a sub-page filed by its top, with its path, its labels and its preview; a page counts the pages right inside it", () => {
  const blocks = new Map(pageBlocksOf(LISTED, TREE).map((b) => [b.id, b]));
  const week = blocks.get("week");
  assert.deepEqual(week._folderChips, [{ id: "phys", name: "Physics" }], "its top's folders, not its own");
  assert.deepEqual(week._folders, ["phys"]);
  assert.deepEqual(week._labelChips, [{ id: "wk", name: "weekly" }], "its own labels");
  assert.equal(week._path, "Project › Notes");
  assert.equal(week._preview, "week first note");
  assert.equal(blocks.get("proj")._path, "", "a library page has no path");
  assert.equal(blocks.get("todo").content, "Untitled");
  assert.deepEqual([...blocks.values()].map((b) => [b.id, b._subPages]),
    [["proj", 2], ["notes", 1], ["week", 0], ["todo", 0], ["lone", 0], ["orphan", 0]],
    "the pages right inside, not below; a parent the pages lack holds none");
  assert.equal(blocks.get("orphan")._path, "", "a page whose parent the pages lack reads as a library page");
});

test("the @ menu's page rows: a sub-page under its path, any other page under its folder", () => {
  const pages = pageBlocksOf([
    page("proj", "Project", "", { updated_at: "2026-10-01", properties: { folders: ["phys"] } }),
    page("notes", "Notes", "proj", { updated_at: "2026-10-02" }),
  ], TREE);
  const rows = mentionRows({ query: "", pages, now: new Date(2026, 9, 9, 15) }).filter((r) => r.kind === "page");
  assert.deepEqual(rows.map((r) => [r.id, r.path]), [["notes", "Project"], ["proj", "Physics"]]);
});
