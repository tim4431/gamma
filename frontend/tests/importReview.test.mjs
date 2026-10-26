import test from "node:test";
import assert from "node:assert/strict";
import { allItemIds, filterImportPages, selectItems, buildImportTree, treeItemIds, treeSize, resultPages, importSummary,
  firstRow, importLinks, orderLikeSource, pageKey } from "../src/transfers/importReview.js";

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

test("the library column follows the source column's order, and each side finds its match on the other", () => {
  const entries = [
    { path: "Export/files/1000/b.pdf", size: 1 }, { path: "Export/files/999/a.pdf", size: 1 },
    { path: "Export/files/999/a.html", size: 1 }, { path: "Export/library.rdf", size: 1 }, { path: "Export/files/", directory: true },
  ];
  const pages = [
    { title: "B", folders: [["ML"]], selection_ids: ["zotero:0"], source_paths: ["Export/files/1000/b.pdf"] },
    { title: "No file", folders: [], selection_ids: ["zotero:1"], source_paths: [] },
    { title: "A", folders: [["ML"], []], selection_ids: ["zotero:2"],
      source_paths: ["Export/files/999/a.pdf", "Export/files/999/a.html", "not/listed.pdf"] },
  ];
  const source = buildImportTree(entries);
  // numbers by value (files/999 before files/1000); a page with no listed file last
  assert.deepEqual(orderLikeSource(pages, source).map(page => page.title), ["A", "B", "No file"]);
  const links = importLinks(entries, pages);
  assert.deepEqual([...links.toFiles.get("zotero:2")], ["Export/files/999/a.pdf", "Export/files/999/a.html"]);
  assert.deepEqual([...links.toPages.get("Export/files/999/a.html")], ["zotero:2"]);
  assert.equal(links.toFiles.has("zotero:1"), false);
  assert.equal(links.toPages.has("Export/library.rdf"), false);
  // where ImportTree shows a row first: each folder's folders before its own files
  const destination = buildImportTree(orderLikeSource(pages, source), true);
  assert.deepEqual(firstRow(destination, new Set(["zotero:2"]), pageKey), { key: "zotero:2", path: ["ML"], index: 0 });
  assert.deepEqual(firstRow(destination, new Set(["zotero:0"]), pageKey), { key: "zotero:0", path: ["ML"], index: 1 });
  assert.deepEqual(firstRow(source, new Set(["Export/files/1000/b.pdf"]), file => file.path),
    { key: "Export/files/1000/b.pdf", path: ["Export", "files", "1000"], index: 0 });
  assert.equal(firstRow(source, new Set(["nowhere"]), file => file.path), null);
});

test("a report's rows are keyed by page and keep the files of every item merged into them", () => {
  const [page] = resultPages({ pages: [{ id: "same", kind: "pdf", created: true, source_paths: ["a.pdf"] },
    { id: "same", kind: "pdf", created: false, source_paths: ["b.pdf", "a.pdf"] }] });
  assert.deepEqual(page.source_paths, ["a.pdf", "b.pdf"]);
  assert.equal(pageKey(page), "pdf:same");
  assert.equal(pageKey({ id: "x", selection_ids: ["page:x"] }), "page:x");
});
