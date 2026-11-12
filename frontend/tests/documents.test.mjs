// A page's document (documents/): the format registry by name, the read of
// properties.doc and its predicates, a duplicate's copy, and which document
// pane a page shows. formats.json and document.json hold the backend's twins
// (gamma/documents/formats.py, model.py) to the same answers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { documentView, viewerOf } from "../src/documents/documentView.js";
import {
  BLOCKED_EXTENSIONS, EXTENSIONS, FORMATS, NAMES, extensionOf, formatOf, languageOf, storedExtension,
} from "../src/documents/formats.js";
import {
  copyDocument, documentViewerUrl, fileHash, inPlace, isPdf, ownHash, pageDocument, parseUploadUrl, pdfFileHash,
  pdfProxyUrl, pdfViewerUrl, sourceAddress, storedName, uploadUrl,
} from "../src/documents/pageDocument.js";

const fixture = (name) => JSON.parse(readFileSync(new URL(`../../tests/shared/${name}`, import.meta.url), "utf8"));
const FORMAT_CASES = fixture("formats.json");
const DOCUMENT_CASES = fixture("document.json");

test("a file's format, language and stored extension by its name", () => {
  for (const c of FORMAT_CASES.names) {
    assert.deepEqual(formatOf(c.name), { format: c.format, language: c.language }, c.name);
    assert.equal(storedExtension(c.name), c.ext, c.name);
  }
});

test("the registry names only known formats, and the extensions the server never stores are blocked", () => {
  for (const [ext, row] of Object.entries(EXTENSIONS)) {
    assert.ok(FORMATS.includes(row.format), ext);
    assert.equal(!!row.language, row.format === "text", `${ext}: a language only for text`);
  }
  for (const [name, [format]] of Object.entries(NAMES)) assert.ok(FORMATS.includes(format), name);
  for (const c of FORMAT_CASES.media) {
    if (c.media === null && /^\.[a-z0-9]{1,12}$/.test(c.ext)) assert.ok(BLOCKED_EXTENSIONS.has(c.ext), c.ext);
  }
  assert.equal(extensionOf("Paper.PDF"), ".pdf");
  assert.equal(extensionOf(".gitignore"), "", "a leading dot is no extension");
  assert.equal(extensionOf("run.exe"), ".exe", "no blocked check");
  assert.equal(languageOf("Makefile", ""), "makefile");
  assert.equal(languageOf("main.tex", ".tex"), "latex");
  assert.equal(languageOf("fig.png", ".png"), null, "no language outside text");
});

test("pageDocument reads what a block carries, the backend's page_document", () => {
  for (const c of DOCUMENT_CASES.cases) {
    assert.deepEqual(pageDocument({ properties: c.properties }), c.document, c.note);
  }
  assert.equal(pageDocument({}), null);
  assert.equal(pageDocument(null), null);
});

test("the predicates: own and shown hash, a PDF and its file, in place, the address", () => {
  for (const c of DOCUMENT_CASES.cases) {
    const doc = pageDocument({ properties: c.properties });
    const view = c.document;
    assert.equal(ownHash(doc), view && !view.copy ? view.hash : "", c.note);
    assert.equal(fileHash(doc), view?.hash || "", c.note);
    assert.equal(isPdf(doc), view?.format === "pdf", c.note);
    assert.equal(inPlace(doc), view?.at === "path", c.note);
    assert.equal(pdfFileHash(doc), view?.format === "pdf" ? view.hash : "", c.note);
    assert.equal(sourceAddress(doc), view?.at === "url" ? view.url : "", c.note);
  }
});

test("a duplicate's document, the client's copyDocument", () => {
  for (const c of DOCUMENT_CASES.copies) {
    assert.deepEqual(copyDocument(pageDocument({ properties: c.properties })), c.copy, c.note);
  }
});

test("stored names and addresses", () => {
  assert.equal(storedName("d1", ".pdf"), "d1.pdf");
  assert.equal(uploadUrl(storedName("t1", ".tex")), "/api/uploads/t1.tex");
  assert.deepEqual(parseUploadUrl("/api/uploads/d1.pdf"), { stem: "d1", ext: ".pdf" });
  assert.deepEqual(parseUploadUrl("/api/uploads/d1.png"), { stem: "d1", ext: ".png" });
  assert.equal(parseUploadUrl("/api/uploads/d1.pdf?x=1"), null);
  assert.equal(parseUploadUrl("https://h.test/api/uploads/d1.pdf"), null);
  assert.equal(parseUploadUrl(`/api/uploads/${"a".repeat(129)}.pdf`), null);
  assert.equal(parseUploadUrl(""), null);
});

test("the viewer's address: a stored file as it is, an address through the proxy", () => {
  const stored = pageDocument({ properties: { doc: { format: "pdf", name: "", hash: "d1", ext: ".pdf", at: "store" } } });
  const remote = pageDocument({ properties: { doc: { format: "pdf", name: "", hash: "", ext: ".pdf", at: "url", url: "https://x.test/a b.pdf" } } });
  const text = pageDocument({ properties: { doc: { format: "text", name: "main.tex", hash: "t1", ext: ".tex", at: "path" } } });
  assert.equal(documentViewerUrl(stored), "/api/uploads/d1.pdf", "a stored copy as it is");
  assert.equal(documentViewerUrl(stored, { share: "s 1" }), "/api/uploads/d1.pdf?share=s%201");
  assert.equal(documentViewerUrl(remote), `/api/pdf?source_url=${encodeURIComponent("https://x.test/a b.pdf")}`, "an address through the proxy");
  assert.equal(documentViewerUrl(remote, { share: "s" }), `/api/pdf?source_url=${encodeURIComponent("https://x.test/a b.pdf")}&share=s`);
  assert.equal(documentViewerUrl(text), "", "no PDF, no PDF viewer");
  assert.equal(documentViewerUrl(null), "");
  assert.equal(pdfViewerUrl("/api/uploads/d1.pdf?x=1", { share: "s" }), "/api/uploads/d1.pdf?x=1&share=s");
  assert.equal(pdfViewerUrl(""), "");
  assert.equal(pdfProxyUrl("https://x.test/a.pdf", { save: true, share: "s" }),
    `/api/pdf?source_url=${encodeURIComponent("https://x.test/a.pdf")}&save=1&share=s`);
});

// What the open page shows, by the rule (doc the page's document, url the
// viewer's input, App's pdfUrl), each answer an App gate reads:
function expected({ doc, url, sheets, notebook: on, hidden, open }) {
  const notebookAvailable = !doc && sheets > 0;         // sheets, and no document pane to draw on
  const notebook = notebookAvailable && on;             // switched on where it is available
  const closed = !notebook && hidden;                   // the notebook view cannot be closed
  const shown = !!(url || notebook) && !closed;         // something mounts and is not closed
  return {
    notebookAvailable, notebook, closed, shown,
    center: closed || (open && !doc && !notebook),      // nothing beside the notes
    takesInk: shown || sheets > 0,                      // a pen draws on what shows, or on a sheet
    zoomAvailable: !!url && !hidden && !notebook,       // the PDF's zoom, while it shows
    translation: !!url && !closed,                      // a translation of the PDF that shows
    pdfRow: !!doc || notebookAvailable,                 // the toolbar's document row
  };
}

const PDF = { format: "pdf", hash: "d1", url: "/api/uploads/d1.pdf" };
const grid = [];
for (const doc of [null, PDF]) {
  for (const url of ["", "/api/uploads/d1.pdf"]) {
    for (const sheets of [0, 2]) {
      for (const notebook of [false, true]) {
        for (const hidden of [false, true]) {
          for (const open of [false, true]) grid.push({ doc, url, sheets, notebook, hidden, open });
        }
      }
    }
  }
}

test("documentView answers each of App's gates by the rule, row by row", () => {
  const rows = {
    "notebookAvailable": (v, e) => [v.notebookAvailable, e.notebookAvailable],
    "notebook": (v, e) => [v.notebook, e.notebook],
    "hidden": (v, e) => [v.hidden, e.closed],
    "center": (v, e) => [v.center, e.center],
    "shown": (v, e) => [v.shown, e.shown],
    "takesInk is shown or sheets": (v, e, c) => [v.shown || c.sheets > 0, e.takesInk],
    "zoomAvailable": (v, e, c) => [v.viewer === "pdf" && !v.notebook && !c.hidden, e.zoomAvailable],
    "the translation gate": (v, e) => [v.viewer === "pdf" && !v.hidden, e.translation],
    "pdfRow": (v, e, c) => [!!c.doc || v.notebookAvailable, e.pdfRow],
  };
  for (const [row, pick] of Object.entries(rows)) {
    for (const c of grid) {
      const [got, want] = pick(documentView(c), expected(c), c);
      assert.equal(got, want, `${row}: ${JSON.stringify(c)}`);
    }
  }
});

test("documentView names the viewer that mounts: the PDF first, else the notebook", () => {
  assert.equal(viewerOf("pdf"), "pdf");
  assert.equal(viewerOf("markdown"), null, "its notes are the file");
  assert.equal(viewerOf(undefined), null);
  assert.equal(documentView({ doc: PDF, url: PDF.url }).viewer, "pdf");
  assert.equal(documentView({ doc: PDF, url: "" }).viewer, null, "no input yet: nothing mounts");
  assert.equal(documentView({ url: "/api/uploads/d1.pdf" }).viewer, "pdf", "an address alone is a PDF");
  assert.equal(documentView({ sheets: 2, notebook: true }).viewer, "notebook");
  assert.equal(documentView({ sheets: 2 }).viewer, null, "its notebook view is off");
  assert.equal(documentView({ doc: PDF, sheets: 2, notebook: true }).viewer, null, "a page with a PDF has no notebook view");
  assert.deepEqual(documentView(), { viewer: null, notebook: false, notebookAvailable: false, hidden: false,
    shown: false, center: true });
});

// A group's page (workspace/PagePane.jsx): the PDF shows unless it is closed
// or the page is docked; the notebook unless docked.
test("documentView in PagePane: the PDF shows unless closed, the notebook when on, a docked page none", () => {
  for (const c of grid) {
    if (c.url && !c.doc) continue; // PagePane's url is its document's
    for (const dock of [false, true]) {
      const sheets = c.doc ? 0 : c.sheets;
      const view = documentView({ doc: c.doc, url: c.url, sheets, notebook: c.notebook, hidden: c.hidden, dock });
      const showPdf = !!c.url && !c.hidden && !dock;
      const showBook = !c.url && c.notebook && sheets > 0 && !dock;
      assert.equal(view.shown, showPdf || showBook, JSON.stringify({ ...c, dock }));
      if (view.shown) assert.equal(view.viewer, showPdf ? "pdf" : "notebook", JSON.stringify({ ...c, dock }));
      if (dock) assert.equal(view.center, true, "the dock is the notes");
    }
  }
});
