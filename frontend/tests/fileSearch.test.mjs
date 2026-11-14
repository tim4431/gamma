// Quick open's Files group, the pure half (explorer/fileSearch.js): the
// server's file rows as palette rows, a file a page row lists left out, and
// the server's code-point spans cut into marked parts.
import assert from "node:assert/strict";
import { test } from "node:test";
import { fileParts, fileRows, placeParts, spanRanges } from "../src/explorer/fileSearch.js";

const hit = (id, path = `${id}.tex`, more = {}) => ({
  id, content: id, path, properties: { work_folder: "W", file_name: path.split("/").at(-1) }, match: { name: [], path: [] }, ...more,
});
const pageRow = (id) => ({ key: id, page: { id, content: id, properties: {} }, section: "pages" });

test("file rows: the server's order, a file some page row lists left out", () => {
  const hits = [hit("main-pdf", "main.pdf"), hit("main-tex", "main.tex"), hit("tab", "sections/tab.tex")];
  const rows = fileRows(hits, [pageRow("main-pdf"), pageRow("other"), { key: "action:search", action: "search" }]);
  assert.deepEqual(rows.map((r) => r.key), ["file:main-tex", "file:tab"]);
  assert.ok(rows.every((r) => r.section === "files"));
  assert.equal(rows[0].file, hits[1], "the hit itself, as the server sent it");
  assert.deepEqual(fileRows([], [pageRow("a")]), []);
  assert.deepEqual(fileRows(hits, []).map((r) => r.file.id), ["main-pdf", "main-tex", "tab"], "no pages: every hit");
});

test("spans: the marked parts, by code points", () => {
  assert.deepEqual(fileParts("main.tex", [[0, 4]]), [{ text: "main", mark: true }, { text: ".tex", mark: false }]);
  assert.deepEqual(fileParts("sections", [[0, 8]]), [{ text: "sections", mark: true }]);
  assert.deepEqual(fileParts("a fig 1.pdf", [[2, 5], [6, 7]]),
    [{ text: "a ", mark: false }, { text: "fig", mark: true }, { text: " ", mark: false },
      { text: "1", mark: true }, { text: ".pdf", mark: false }]);
});

test("spans: none or empty leave the text whole, ones past the end cut", () => {
  assert.deepEqual(fileParts("main.tex", []), [{ text: "main.tex", mark: false }]);
  assert.deepEqual(fileParts("main.tex", undefined), [{ text: "main.tex", mark: false }]);
  assert.deepEqual(fileParts("main.tex", [[3, 3]]), [{ text: "main.tex", mark: false }], "an empty span marks nothing");
  assert.deepEqual(fileParts("abc", [[1, 99]]), [{ text: "a", mark: false }, { text: "bc", mark: true }]);
  assert.deepEqual(fileParts("abc", [[5, 9]]), [{ text: "abc", mark: false }]);
  assert.deepEqual(spanRanges("abc", [[5, 9], [1, 2]]), [[1, 2]]);
});

test("spans: an astral name counts each character once, as the server does", () => {
  // "𝔉ig1.pdf": the first letter is two UTF-16 units, one code point.
  assert.deepEqual(spanRanges("𝔉ig1.pdf", [[0, 4]]), [[0, 5]]);
  assert.deepEqual(fileParts("𝔉ig1.pdf", [[0, 4]]), [{ text: "𝔉ig1", mark: true }, { text: ".pdf", mark: false }]);
  assert.deepEqual(fileParts("图1😀.pdf", [[1, 3]]),
    [{ text: "图", mark: false }, { text: "1😀", mark: true }, { text: ".pdf", mark: false }]);
});

test("place line: W, then the directory at the server's spans, its \"/\" drawn as \" / \"", () => {
  const text = (parts) => parts.map((p) => p.text).join("");
  assert.deepEqual(placeParts("Paper", "sections", [[0, 8]]), [{ text: "Paper / ", mark: false }, { text: "sections", mark: true }]);
  assert.equal(text(placeParts("Paper", "figures/raw", [])), "Paper / figures / raw");
  assert.deepEqual(placeParts("Paper", "figures/raw", [[8, 11]]).filter((p) => p.mark).map((p) => p.text), ["raw"]);
  assert.deepEqual(placeParts("Paper", "", []), [{ text: "Paper", mark: false }], "a file at W's top");
  assert.equal(text(placeParts("", "a/b", [])), "a / b", "no W the client knows");
});
