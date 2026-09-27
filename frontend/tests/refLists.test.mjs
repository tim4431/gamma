// The [[ link picker's lists (editor/refLists.js): pages by title first,
// blocks as a plain line under their page path, and the hand-typed
// [[title]] rule.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pageByTitle, pickerCounts, rankRefPages, refBlockPath, refBlockText } from "../src/editor/refLists.js";

const page = (id, content, extra = {}) => ({ id, content, _folders: [], _labels: [], _updatedAt: "", ...extra });
const PAGES = [
  page("notes", "Attention — reading notes", { _updatedAt: "2026-09-20T00:00:00Z" }),
  page("paper", "Attention Is All You Need", { _folders: ["ML/Transformers"], _updatedAt: "2026-09-01T00:00:00Z" }),
  page("rydberg", "Rydberg Atom Arrays", { _labels: ["attention-free"], _updatedAt: "2026-09-25T00:00:00Z" }),
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
