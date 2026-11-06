// Sub-pages in the client (library/libraryUtils.js): the pages above a page
// from each page block's `_parentPage`, the path a picker names it by, the
// pages inside one, and the pickers that show the path (Ctrl+P and the
// chat's, librarySearch.rankLibraryPages; the @ menu, editor/mentions.js).
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_PAGE_DEPTH, PAGE_PATH_SEP, deleteConfirm, libraryTree, pageAncestors, pageDescendants, pagePathLabel, topPageOf,
} from "../src/library/libraryUtils.js";
import { rankLibraryPages } from "../src/library/librarySearch.js";
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

test("the @ menu's page rows: a sub-page under its path, any other page under its folder", () => {
  const pages = [
    page("proj", "Project", "", { _updatedAt: "2026-10-01", _folderChips: [{ id: "f", name: "Physics" }] }),
    page("notes", "Notes", "proj", { _updatedAt: "2026-10-02" }),
  ];
  const rows = mentionRows({ query: "", pages, now: new Date(2026, 9, 9, 15) }).filter((r) => r.kind === "page");
  assert.deepEqual(rows.map((r) => [r.id, r.path]), [["notes", "Project"], ["proj", "Physics"]]);
});
