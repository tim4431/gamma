import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { allItemIds, filterImportPages, selectItems, buildImportTree, rowItemIds, treeItemIds, treeSize, resultPages, importSummary,
  firstRow, importLinks, orderLikeSource, pageKey, reviewRefused } from "../src/transfers/importReview.js";

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

test("the library column shows a sub-page under the page it goes inside, not loose in its top's folder", () => {
  const pages = [
    { id: "top", title: "Top", folders: [["Notion"]], selection_ids: ["Top.md"], parent: "" },
    { id: "mid", title: "Mid", folders: [["Notion"]], selection_ids: ["Top/Mid.md"], parent: "top" },
    { id: "deep", title: "Deep", folders: [["Notion"]], selection_ids: ["Top/Mid/Deep.md"], parent: "mid" },
    { id: "row", title: "Row", folders: [["Notion"]], selection_ids: ["Top/Row.md"], parent: "top" },
    // its parent is a page already in the library, not in this list
    { id: "orphan", title: "Orphan", folders: [["Notion"]], selection_ids: ["Old/Orphan.md"], parent: "existing" },
    // a loop the server would cut: both stay where their folders put them
    { id: "a", title: "A", folders: [], selection_ids: ["A.md"], parent: "b" },
    { id: "b", title: "B", folders: [], selection_ids: ["B.md"], parent: "a" },
  ];
  const tree = buildImportTree(pages, true);
  const notion = tree.folders.get("Notion");
  assert.deepEqual(notion.files.map(file => file.name), ["Top", "Orphan"]);
  const [top] = notion.files;
  assert.deepEqual(top.inside.files.map(file => file.name), ["Mid", "Row"]);
  assert.deepEqual(top.inside.files[0].inside.files.map(file => file.name), ["Deep"]);
  assert.equal(top.inside.files[1].inside, undefined);
  assert.deepEqual(tree.files.map(file => file.name), ["A", "B"]);
  // the folder's box selects the pages inside its pages too
  assert.deepEqual(treeItemIds(notion), ["Top.md", "Top/Mid.md", "Top/Mid/Deep.md", "Top/Row.md", "Old/Orphan.md"]);
  // and a page's box the pages inside it, which without it would be filed by their directory
  assert.deepEqual(rowItemIds(top), ["Top.md", "Top/Mid.md", "Top/Mid/Deep.md", "Top/Row.md"]);
  assert.deepEqual(rowItemIds(top.inside.files[0]), ["Top/Mid.md", "Top/Mid/Deep.md"]);
  assert.deepEqual(rowItemIds(top.inside.files[1]), ["Top/Row.md"]);
  assert.equal(treeSize(tree), 7);
  // where ImportTree shows a sub-page: under its top's row, and the places down to it
  assert.deepEqual(firstRow(tree, new Set(["Top/Mid/Deep.md"]), pageKey), { key: "Top/Mid/Deep.md", path: ["Notion"], index: 0, sub: [0, 0] });
  assert.deepEqual(firstRow(tree, new Set(["Top/Row.md"]), pageKey), { key: "Top/Row.md", path: ["Notion"], index: 0, sub: [1] });
  assert.deepEqual(firstRow(tree, new Set(["Old/Orphan.md"]), pageKey), { key: "Old/Orphan.md", path: ["Notion"], index: 1 });
  // a parent the filter leaves out leaves its pages where their folders put them
  const shown = buildImportTree(pages.filter(page => page.id !== "top"), true);
  assert.deepEqual(shown.folders.get("Notion").files.map(file => file.name), ["Mid", "Row", "Orphan"]);
  // the archive's own column is untouched
  assert.equal(buildImportTree([{ path: "Top.md", size: 1, parent: "x" }]).files[0].inside, undefined);
});

test("a sub-page ticked without its parent shows where the import files it then, by its directory", () => {
  const pages = [
    { id: "top", title: "Top", folders: [["Notion"]], selection_ids: ["Top.md"], parent: "", action: "create" },
    { id: "mid", title: "Mid", folders: [["Notion"]], folders_alone: [["Notion", "Top"]], selection_ids: ["Top/Mid.md"],
      parent: "top", action: "create" },
    { id: "deep", title: "Deep", folders: [["Notion"]], folders_alone: [["Notion", "Top", "Mid"]],
      selection_ids: ["Top/Mid/Deep.md"], parent: "mid", action: "create" },
    // inside a page imported before: it is inside that page whatever is ticked
    { id: "old", title: "Old", folders: [["Notion"]], selection_ids: ["Old.md"], parent: "", action: "skip" },
    { id: "late", title: "Late", folders: [["Notion"]], folders_alone: [["Notion", "Old"]], selection_ids: ["Old/Late.md"],
      parent: "old", action: "create" },
  ];
  const names = node => node.files.map(file => file.name);
  // everything ticked, nothing ticked, and after the import: nested as the report says
  for (const selected of [new Set(pages.flatMap(page => page.selection_ids)), new Set(), null]) {
    const notion = buildImportTree(pages, true, selected).folders.get("Notion");
    assert.deepEqual(names(notion), ["Top", "Old"]);
    assert.equal(notion.folders.size, 0);
  }
  // Top unticked, Mid and Deep ticked: Mid goes to Top's directory, Deep stays inside it
  const tree = buildImportTree(pages, true, new Set(["Top/Mid.md", "Top/Mid/Deep.md", "Old/Late.md"]));
  const notion = tree.folders.get("Notion");
  assert.deepEqual(names(notion), ["Top", "Old"]);
  assert.equal(notion.files[0].inside, undefined);
  assert.deepEqual(names(notion.folders.get("Top")), ["Mid"]);
  assert.deepEqual(names(notion.folders.get("Top").files[0].inside), ["Deep"]);
  assert.deepEqual(names(notion.files[1].inside), ["Late"]);
  // Top and Mid unticked, Deep ticked: Deep alone in Mid's directory; Mid, unticked, stays inside Top
  const deep = buildImportTree(pages, true, new Set(["Top/Mid/Deep.md"])).folders.get("Notion");
  assert.deepEqual(names(deep.files[0].inside), ["Mid"]);
  assert.deepEqual(names(deep.folders.get("Top").folders.get("Mid")), ["Deep"]);
  // the Selected filter hides the unticked Top; Mid still goes to Top's directory
  const picked = new Set(["Top/Mid.md", "Top/Mid/Deep.md"]);
  const filtered = buildImportTree(filterImportPages(pages, "selected", picked), true, picked, pages);
  assert.deepEqual(names(filtered.folders.get("Notion")), []);
  assert.deepEqual(names(filtered.folders.get("Notion").folders.get("Top")), ["Mid"]);
  assert.deepEqual(names(filtered.folders.get("Notion").folders.get("Top").files[0].inside), ["Deep"]);
});

// ImportTree drawn on the server's renderer: the component bundled with
// React by esbuild (vite's), so the test needs no browser.
async function renderTree(props) {
  const { build } = await import("esbuild");
  const root = fileURLToPath(new URL("..", import.meta.url));
  const entry = [
    'import React from "react";',
    'import { renderToStaticMarkup } from "react-dom/server";',
    'import ImportTree from "./src/transfers/ImportTree.jsx";',
    "export const render = (props) => renderToStaticMarkup(React.createElement(ImportTree, props));",
  ].join("\n");
  const out = await build({ stdin: { contents: entry, resolveDir: root, loader: "js" }, bundle: true, write: false,
    format: "esm", platform: "node", jsx: "automatic", loader: { ".css": "empty" }, logLevel: "silent",
    // React's CommonJS asks for node's own modules
    banner: { js: 'import { createRequire } from "node:module"; const require = createRequire(import.meta.url);' } });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "import-tree-"));
  const file = path.join(dir, "tree.mjs");
  fs.writeFileSync(file, out.outputFiles[0].text);
  try {
    return (await import(pathToFileURL(file).href)).render(props);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}
const boxes = (html) => [...html.matchAll(/<input[^>]*>/g)].map((m) => m[0]);

test("the import review draws as before when the lazy tree's props are not given", async () => {
  const tree = buildImportTree([
    { title: "A", folders: [["ML"]], selection_ids: ["a"] },
    { title: "B", folders: [["ML"]], selection_ids: ["b"] },
    { title: "Empty", folders: [["ML", "Sub"]], selection_ids: [] },
  ], true);
  const html = await renderTree({ node: tree, library: true, selected: new Set(["a"]), onSelect: () => {} });
  // a box for each of the two folders and the three pages, none disabled
  assert.equal(boxes(html).length, 5);
  assert.ok(boxes(html).every((box) => !/disabled/.test(box)));
  assert.ok(/<details open="">/.test(html));
  assert.ok(!html.includes("Reading…"));
  assert.ok(html.includes(">Page<"));
});

test("a tree read a directory at a time keeps an unread folder closed and asks each box of the caller", async () => {
  const folder = { path: "build/", walked: false, reading: true, folders: new Map(), files: [], hint: "git ignores it" };
  const node = {
    folders: new Map([["build", folder]]),
    files: [
      { path: "main.pdf", name: "main.pdf", size: 2048, hint: "git ignores it", chip: "PDF" },
      { path: ".env", name: ".env", size: 1, never: "dot" },
    ],
  };
  const asked = [];
  const html = await renderTree({ node, onSelect: () => {}, onOpenFolder: () => {},
    boxOf: (n) => { asked.push(n.path); return { checked: n.path === "main.pdf", disabled: !!n.never }; } });
  assert.deepEqual(asked, ["build/", "main.pdf", ".env"]);
  assert.ok(/<details>/.test(html), "an unread folder starts closed");
  assert.ok(html.includes("Reading…") && !html.includes("Empty folder"));
  assert.ok(html.includes("2 KB · git ignores it") && html.includes(">PDF<"));
  const [, pdf, dot] = boxes(html);
  assert.ok(/checked/.test(pdf) && !/disabled/.test(pdf));
  assert.ok(/disabled/.test(dot) && !/checked/.test(dot));
});

test("a review's job that failed on the file is refused; one a restart or a bug cut short may be tried again", () => {
  assert.equal(reviewRefused("could not read the Zotero export: File is not a zip file"), true);
  assert.equal(reviewRefused("not a Gamma export"), true);
  assert.equal(reviewRefused("interrupted: the server restarted while this ran — start it again"), false);
  assert.equal(reviewRefused("unexpected error (KeyError: 'pages')"), false);
});
