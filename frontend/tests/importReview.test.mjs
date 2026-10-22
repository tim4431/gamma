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

test("the summary counts what a Notability import brought, one of a kind in the singular", () => {
  const warning = { title: "Lecture 3", reason: "Recording 1 has no readable length: left out", selection_id: "Lecture 3.note" };
  assert.equal(importSummary({ pages_created: 2, pages_skipped: 1, pdfs_stored: 2, strokes_imported: 41, text_boxes_imported: 3,
    pictures_imported: 2, recordings_imported: 2, skipped: [{ title: "Lecture 4", reason: "its PDF is over the limit" }], warnings: [warning] }),
  "2 new pages · 1 already in library · 2 files stored · 41 strokes · 3 text boxes · 2 pictures · 2 recordings · 2 warnings");
  assert.equal(importSummary({ pages_created: 1, pdfs_stored: 1, strokes_imported: 1, text_boxes_imported: 1, pictures_imported: 1,
    recordings_imported: 1, warnings: [warning] }),
  "1 new page · 1 file stored · 1 stroke · 1 text box · 1 picture · 1 recording · 1 warning");
  // what a source does not report, or reports as 0, is left out; the new pages never are
  assert.equal(importSummary({ pages_created: 0, pages_skipped: 3, strokes_imported: 0 }), "0 new pages · 3 already in library");
});
