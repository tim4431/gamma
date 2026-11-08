// The [[ link picker's lists (editor/refLists.js): pages by title first,
// blocks as a plain line under their page path, and the hand-typed
// [[title]] rule.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  pageByTitle, pickerCounts, rankRefPages, refBlockPath, refBlockText, refInsertEdit, refQueryAt, typedTitleLink,
} from "../src/editor/refLists.js";
import { libraryTree, pageBlocksOf } from "../src/library/libraryUtils.js";

// App's page list (libraryUtils.pageBlocksOf): chips are {id, name}, and
// the matcher reads the names only.
const page = (id, content, extra = {}) => ({ id, content, _folderChips: [], _labelChips: [], _path: "", _updatedAt: "", ...extra });
const PAGES = [
  page("notes", "Attention — reading notes", { _updatedAt: "2026-09-20T00:00:00Z" }),
  page("paper", "Attention Is All You Need", { _folderChips: [{ id: "f9", name: "ML / Transformers" }], _updatedAt: "2026-09-01T00:00:00Z" }),
  page("rydberg", "Rydberg Atom Arrays", { _labelChips: [{ id: "l7", name: "attention-free" }], _updatedAt: "2026-09-25T00:00:00Z" }),
  page("open", "Attention scratchpad", { _updatedAt: "2026-09-26T00:00:00Z" }),
];

test("pages the query names, title hits first, the open page left out", () => {
  const ids = rankRefPages(PAGES, "Att", "open").map((p) => p.id);
  assert.deepEqual(ids.slice(0, 2).sort(), ["notes", "paper"]);
  assert.ok(!ids.includes("open"), "the open page is no link target");
  assert.equal(ids.indexOf("rydberg"), ids.length - 1, "a label-only hit ranks after the titles");
  assert.equal(rankRefPages(PAGES, "attention is all", "open")[0].id, "paper", "the exact phrase wins");
  assert.equal(rankRefPages(PAGES, "atention is all", "open")[0].id, "paper", "a typo still finds it");
  assert.deepEqual(rankRefPages(PAGES, "zzz", null), []);
});

test("folders and labels match by name, never by id", () => {
  assert.deepEqual(rankRefPages(PAGES, "transformers", "open").map((p) => p.id), ["paper"], "a folder by its name");
  assert.deepEqual(rankRefPages(PAGES, "attention-free", "open").map((p) => p.id), ["rydberg"], "a label by its name");
  assert.deepEqual(rankRefPages(PAGES, "f9", "open"), [], "a folder's id names nothing");
  assert.deepEqual(rankRefPages(PAGES, "l7", "open"), [], "nor a label's");
});

// The listing's rows as App reads them: Project, filed in Physics, with
// Notes inside it, which carries a folder of its own (an import's) and a
// label.
test("a page inside another matches on its path and its top's folders, not its own", () => {
  const tree = libraryTree({
    folders: { children: [{ id: "phys", content: "Physics" }, { id: "stale", content: "Stale" }] },
    labels: { children: [{ id: "wk", content: "weekly" }] },
  });
  const pages = pageBlocksOf([
    { id: "proj", content: "Project", properties: { folders: ["phys"] }, _parentPage: "" },
    { id: "notes", content: "Notes", properties: { folders: ["stale"], labels: ["wk"] }, _parentPage: "proj" },
    { id: "other", content: "Other notes", properties: {}, _parentPage: "" },
  ], tree);
  const ids = (query) => rankRefPages(pages, query, "").map((p) => p.id);
  assert.deepEqual(ids("project"), ["proj", "notes"], "the title, then the page whose path names it");
  assert.deepEqual(ids("physics notes"), ["notes"], "its top's folder and its title");
  assert.deepEqual(ids("stale"), [], "a folder it carries itself counts for nothing");
  assert.deepEqual(ids("weekly"), ["notes"], "its own label");
});

test("before anything is typed, the most recently edited pages", () => {
  assert.deepEqual(rankRefPages(PAGES, "", "open").map((p) => p.id), ["rydberg", "notes", "paper"]);
});

test("pages first, up to five while blocks wait; each list takes the room the other leaves", () => {
  assert.deepEqual(pickerCounts(10, 10), [5, 3]);
  assert.deepEqual(pickerCounts(2, 10), [2, 6]);
  assert.deepEqual(pickerCounts(10, 1), [7, 1]);
  assert.deepEqual(pickerCounts(0, 12), [0, 8]);
  assert.deepEqual(pickerCounts(3, 0), [3, 0]);
});

test("a block reads as one plain line, its [[refs]] as their labels", () => {
  const labelOf = (id) => ({ paper: "Attention Is All You Need", odd: "A [weird] | title" })[id];
  assert.equal(refBlockText("## Main reference: [[paper]] — **reread** §3", labelOf), "Main reference: Attention Is All You Need — reread §3");
  assert.equal(refBlockText("```python\nimport torch\n```", labelOf), "import torch");
  assert.equal(refBlockText("> [!tip] Idea\n> See [[odd]]", labelOf), "Idea See A weird title");
  assert.equal(refBlockText("see [[unknown1]]", labelOf), "see unknown1");
});

test("a block's page path, plain", () => {
  assert.equal(refBlockPath({ ancestors: [{ content: "Attention — reading notes" }, { content: "The **Transformer** drops recurrence" }] }),
    "Attention — reading notes › The Transformer drops recurrence");
  assert.equal(refBlockPath({}), "");
});

test("a hand-typed [[title]] links only the one page with exactly that title", () => {
  assert.equal(pageByTitle(PAGES, "attention is all  you need")?.id, "paper", "case and spacing aside");
  assert.equal(pageByTitle(PAGES, "Attention"), null, "a prefix is not a title");
  assert.equal(pageByTitle([...PAGES, page("dup", "Rydberg Atom Arrays")], "Rydberg Atom Arrays"), null, "two pages share it");
  assert.equal(pageByTitle(PAGES, "  "), null);
});

test("the [[query typed before the caret opens the picker; a closed or broken one does not", () => {
  assert.equal(refQueryAt("see [[Att"), "Att");
  assert.equal(refQueryAt("see [["), "");
  assert.equal(refQueryAt("see [[Att]] and"), null);
  assert.equal(refQueryAt("see [[Att\nnext"), null);
  assert.equal(refQueryAt(""), null);
});

test("a picked row puts [[id]] in place of the typed [[query, the caret after it", () => {
  const value = "see ![[Att and more";
  const edit = refInsertEdit(value, 10, "paper");
  assert.deepEqual(edit, { from: 5, to: 10, insert: "[[paper]]", value: "see ![[paper]] and more", caret: 14 });
  assert.equal(refInsertEdit("no picker here", 5, "paper"), null);
});

test("]] typed after a page's exact title links it; an id or an unknown title stays", () => {
  const value = "read [[attention is all you need]]";
  assert.deepEqual(typedTitleLink(value, value.length, PAGES), {
    from: 5, to: value.length, insert: "[[paper]]", page: PAGES[1],
  });
  assert.equal(typedTitleLink("read [[paper]]", 14, PAGES), null, "an id already");
  assert.equal(typedTitleLink("read [[Nothing like it]]", 24, PAGES), null);
});
