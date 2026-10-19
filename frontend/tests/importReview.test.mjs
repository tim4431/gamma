import test from "node:test";
import assert from "node:assert/strict";
import { allItemIds, filterImportPages, selectItems, buildImportTree, treeItemIds, treeSize, resultPages, importSummary } from "../src/transfers/importReview.js";

test("filters preserve hidden selections; folder selection deduplicates items in multiple locations", () => {
  const pages = [
    { title: "Ready", folders: [["A"], ["B", "C/D"]], selection_ids: ["a", "alias"], missing: false },
    { title: "Missing", folders: [["B", "C/D"]], selection_ids: ["b"], missing: true, warnings: [{ reason: "Missing PDF" }] },
  ];
  const selected = new Set(allItemIds(pages));
  assert.equal(filterImportPages(pages, "missing", selected).length, 1);
  assert.deepEqual([...selected], ["a", "alias", "b"]);
  const next = selectItems(selected, ["b"], false);
  assert.deepEqual(filterImportPages(pages, "selected", next).map(p => p.title), ["Ready"]);
  const tree = buildImportTree(pages, true);
  assert.deepEqual(treeItemIds(tree), ["a", "alias", "b"]);
  // A folder is a list of names: one holding a "/" stays one folder.
  assert.deepEqual([...tree.folders.keys()], ["A", "B"]);
  assert.deepEqual([...tree.folders.get("B").folders.keys()], ["C/D"]);
  assert.deepEqual(treeItemIds(tree.folders.get("B")), ["a", "alias", "b"]);
  assert.equal(resultPages({ pages: [{ id: "same", title: "First", created: true }, { id: "same", title: "First", created: false }] }).length, 1);
});

test("a tree's size counts the files in all its folders (ImportTree starts a big one closed)", () => {
  const entries = [
    { path: "vault/a.md", size: 1 }, { path: "vault/sub/b.md", size: 1 }, { path: "vault/sub/c.png", size: 1 },
    { path: "vault/empty", directory: true }, { path: "top.md", size: 1 },
  ];
  const tree = buildImportTree(entries);
  assert.equal(treeSize(tree), 4);
  assert.equal(treeSize(tree.folders.get("vault")), 3);
  assert.equal(treeSize(tree.folders.get("vault").folders.get("empty")), 0);
});

test("the summary counts the attachments a Zotero import put on its pages", () => {
  assert.equal(importSummary({ pages_created: 1, notes_imported: 2, attachments_imported: 3 }),
    "1 new page · 2 notes · 3 attachments");
});
