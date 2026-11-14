// A page's document (documents/): the format registry by name, the read of
// properties.doc and its predicates, a duplicate's copy, and which document
// pane a page shows. formats.json and document.json hold the backend's twins
// (gamma/documents/formats.py, model.py) to the same answers.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { documentView, viewerOf } from "../src/documents/documentView.js";
import {
  BLOCKED_EXTENSIONS, EXTENSIONS, FORMATS, IMAGE_MEDIA_TYPES, NAMES, extensionOf, formatOf, languageOf, storedExtension,
} from "../src/documents/formats.js";
import {
  copyDocument, documentFileUrl, documentViewerUrl, fileHash, inPlace, isListed, isPdf, ownHash, pageDocument, parseUploadUrl,
  pdfFileHash, pdfProxyUrl, pdfViewerUrl, sourceAddress, storedName, uploadUrl,
} from "../src/documents/pageDocument.js";
import { FORMAT_VIEWER, VIEWERS } from "../src/documents/viewers.js";

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
  const pictures = Object.fromEntries(FORMAT_CASES.media.filter((c) => c.media?.startsWith("image/")).map((c) => [c.ext, c.media]));
  assert.deepEqual(IMAGE_MEDIA_TYPES, pictures, "the image rows' media types are the server's");
  for (const [ext, row] of Object.entries(EXTENSIONS)) assert.equal(row.format === "image", Object.hasOwn(IMAGE_MEDIA_TYPES, ext), ext);
  assert.equal(extensionOf("Paper.PDF"), ".pdf");
  assert.equal(extensionOf(".gitignore"), "", "a leading dot is no extension");
  assert.equal(extensionOf("run.exe"), ".exe", "no blocked check");
  assert.equal(languageOf("Makefile", ""), "makefile");
  assert.equal(languageOf("main.tex", ".tex"), "latex");
  assert.equal(languageOf("fig.png", ".png"), null, "no language outside text");
  assert.equal(languageOf("README.md", ".md"), "markdown", "a Markdown file read as text");
  assert.equal(languageOf("notes.markdown", ".markdown"), "markdown");
  assert.deepEqual(formatOf("README.md"), { format: "markdown", language: null }, "its format stays markdown");
});

test("pageDocument reads what a block carries, the backend's page_document", () => {
  for (const c of DOCUMENT_CASES.cases) {
    assert.deepEqual(pageDocument({ properties: c.properties }), c.document, c.note);
  }
  assert.equal(pageDocument({}), null);
  assert.equal(pageDocument(null), null);
});

test("the predicates: own and shown hash, a PDF and its file, in place, listed, the address", () => {
  for (const c of DOCUMENT_CASES.cases) {
    const doc = pageDocument({ properties: c.properties });
    const view = c.document;
    assert.equal(ownHash(doc), view && !view.copy ? view.hash : "", c.note);
    assert.equal(fileHash(doc), view?.hash || "", c.note);
    assert.equal(isPdf(doc), view?.format === "pdf", c.note);
    assert.equal(inPlace(doc), view?.at === "path", c.note);
    assert.equal(isListed(doc), view?.at === "listed", c.note);
    assert.equal(pdfFileHash(doc), view?.format === "pdf" ? view.hash : "", c.note);
    assert.equal(sourceAddress(doc), view?.at === "url" ? view.url : "", c.note);
    if (view?.at === "listed") assert.equal(view.url, "", "a listed file has no address to read");
  }
  assert.ok(DOCUMENT_CASES.cases.some((c) => c.document?.at === "listed"), "the fixture holds a listed file");
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

test("the file's address: a stored file or one read in place through the host's assetUrl, with the share; none for a listed file", () => {
  const text = pageDocument({ properties: { doc: { format: "text", name: "main.tex", hash: "t1", ext: ".tex", at: "path" } } });
  const png = pageDocument({ properties: { doc: { format: "image", name: "fig.png", hash: "i1", ext: ".png", at: "store" } } });
  const listed = pageDocument({ properties: { doc: { format: "binary", name: "w.ckpt", hash: "", ext: ".ckpt", at: "listed", size: 9 } } });
  const remotePdf = pageDocument({ properties: { doc: { format: "pdf", name: "", hash: "", ext: ".pdf", at: "url", url: "https://x.test/p.pdf" } } });
  const remotePng = pageDocument({ properties: { doc: { format: "image", name: "", hash: "", ext: ".png", at: "url", url: "https://x.test/p.png" } } });
  const asset = (url) => `${url}?ws=w1`;
  assert.equal(documentFileUrl(text), "/api/uploads/t1.tex");
  assert.equal(documentFileUrl(text, { asset }), "/api/uploads/t1.tex?ws=w1", "the host's assetUrl tags the workspace");
  assert.equal(documentFileUrl(png, { asset, share: "s 1" }), "/api/uploads/i1.png?ws=w1&share=s%201");
  assert.equal(documentFileUrl(png, { share: "s" }), "/api/uploads/i1.png?share=s");
  assert.equal(documentFileUrl(png, { asset: (url) => `${url}?share=s`, share: "s" }), "/api/uploads/i1.png?share=s", "the share once");
  assert.equal(documentFileUrl(listed), "", "no bytes here");
  assert.equal(documentFileUrl(remotePdf, { share: "s" }), `/api/pdf?source_url=${encodeURIComponent("https://x.test/p.pdf")}&share=s`,
    "a PDF at an address goes through the proxy");
  assert.equal(documentFileUrl(remotePng), "", "no file here for any other document at an address");
  assert.equal(documentFileUrl(null), "");
  assert.equal(isListed(listed), true);
  assert.equal(isListed(text), false);
  assert.equal(isListed(null), false);
});

// What the open page shows, by the rule (doc the page's document, url the
// PDF viewer's input, App's pdfUrl), each answer an App gate reads:
function expected({ doc, url, sheets, notebook: on, hidden, open }) {
  const pane = doc ? (doc.at === "listed" ? "file" : FORMAT_VIEWER[doc.format] ?? null) : null;  // the document's pane (markdown: none)
  const notebookAvailable = !pane && sheets > 0;         // sheets, and no document pane to draw on
  const notebook = notebookAvailable && on;              // switched on where it is available
  const closed = !notebook && hidden;                    // the notebook view cannot be closed
  const viewer = pane === "pdf" ? (url ? "pdf" : null)   // the PDF pane waits for its address
    : pane || (url ? "pdf" : notebook ? "notebook" : null);  // any other pane from the document alone; a url alone is a PDF
  const shown = !!viewer && !closed;                     // something mounts and is not closed
  const inks = viewer === "pdf" || viewer === "notebook";
  return {
    notebookAvailable, notebook, closed, shown, viewer,
    spec: viewer ? VIEWERS[viewer] : null,
    center: closed || (open && !pane && !notebook),      // nothing beside the notes
    takesInk: (shown && inks) || sheets > 0,             // a pen draws on the PDF or the notebook, or on a sheet
    zoomAvailable: viewer === "pdf" && !hidden && !notebook,  // the PDF's zoom (the ink zoom window), while it shows
    translation: viewer === "pdf" && !closed,            // a translation of the PDF that shows
    pdfRow: !!doc || notebookAvailable,                  // the toolbar's document row
  };
}

const PDF = { format: "pdf", hash: "d1", url: "/api/uploads/d1.pdf", at: "store" };
const TEXT = { format: "text", hash: "t1", url: "/api/uploads/t1.tex", at: "path" };
const IMAGE = { format: "image", hash: "i1", url: "/api/uploads/i1.png", at: "store" };
const AUDIO = { format: "audio", hash: "a1", url: "/api/uploads/a1.wav", at: "path" };
const VIDEO = { format: "video", hash: "v1", url: "/api/uploads/v1.mp4", at: "path" };
const BINARY = { format: "binary", hash: "b1", url: "/api/uploads/b1.bin", at: "path" };
const LISTED = { format: "binary", hash: "", url: "", at: "listed", size: 9 };
const MARKDOWN = { format: "markdown", hash: "m1", url: "/api/uploads/m1.md", at: "store" };
const grid = [];
for (const doc of [null, PDF, TEXT, IMAGE, AUDIO, VIDEO, BINARY, LISTED, MARKDOWN]) {
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
    "viewer": (v, e) => [v.viewer, e.viewer],
    "spec": (v, e) => [v.spec, e.spec],
    "notebookAvailable": (v, e) => [v.notebookAvailable, e.notebookAvailable],
    "notebook": (v, e) => [v.notebook, e.notebook],
    "hidden": (v, e) => [v.hidden, e.closed],
    "center": (v, e) => [v.center, e.center],
    "shown": (v, e) => [v.shown, e.shown],
    "takesInk is shown with a viewer that takes ink, or sheets": (v, e, c) => [(v.shown && !!v.spec?.ink) || c.sheets > 0, e.takesInk],
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
  // the panes that mount from the document alone: shown without a url, the notes beside them
  for (const doc of [TEXT, IMAGE, AUDIO, VIDEO, BINARY, LISTED]) {
    const v = documentView({ doc });
    assert.equal(v.shown, true, doc.format);
    assert.equal(v.center, false, doc.format);
    assert.equal(documentView({ doc, hidden: true }).center, true, "closed: the notes take the middle");
  }
});

test("documentView names the viewer that mounts: the PDF once it has its address, any other pane from the document, else the notebook", () => {
  assert.equal(viewerOf("pdf"), "pdf");
  assert.deepEqual(["text", "image", "audio", "video", "binary"].map((f) => viewerOf(f)), ["text", "image", "media", "media", "file"]);
  assert.equal(viewerOf("markdown"), null, "its notes are the file");
  assert.equal(viewerOf(undefined), null);
  assert.equal(viewerOf("epub"), null);
  assert.equal(viewerOf("text", LISTED), "file", "a listed file gets the file pane whatever its format");
  assert.equal(viewerOf("pdf", PDF), "pdf");
  assert.equal(documentView({ doc: PDF, url: PDF.url }).viewer, "pdf");
  assert.equal(documentView({ doc: PDF, url: "" }).viewer, null, "no input yet: nothing mounts");
  assert.equal(documentView({ url: "/api/uploads/d1.pdf" }).viewer, "pdf", "an address alone is a PDF");
  assert.equal(documentView({ doc: TEXT }).viewer, "text", "a text file mounts from its document");
  assert.equal(documentView({ doc: TEXT }).spec, VIEWERS.text);
  assert.equal(documentView({ doc: IMAGE, url: "/api/uploads/d1.pdf" }).viewer, "image", "a stale PDF address changes nothing");
  assert.equal(documentView({ doc: LISTED }).viewer, "file");
  assert.equal(documentView({ doc: MARKDOWN, sheets: 2, notebook: true }).viewer, "notebook", "a Markdown document has no pane");
  assert.equal(documentView({ sheets: 2, notebook: true }).viewer, "notebook");
  assert.equal(documentView({ sheets: 2, notebook: true }).spec, VIEWERS.notebook);
  assert.equal(documentView({ sheets: 2 }).viewer, null, "its notebook view is off");
  assert.equal(documentView({ doc: PDF, sheets: 2, notebook: true }).viewer, null, "a page with a PDF has no notebook view");
  assert.equal(documentView({ doc: IMAGE, sheets: 2, notebook: true }).viewer, "image", "nor has a page with a picture");
  assert.deepEqual(documentView(), { viewer: null, spec: null, notebook: false, notebookAvailable: false, hidden: false,
    shown: false, center: true });
});

// A group's page (workspace/PagePane.jsx): the PDF shows unless it is closed
// or the page is docked, any other pane from the document alone the same
// way; the notebook unless docked.
test("documentView in PagePane: the PDF shows unless closed, another pane from its document, the notebook when on, a docked page none", () => {
  for (const c of grid) {
    if (c.url && !isPdf(c.doc)) continue; // PagePane's url is its PDF document's
    for (const dock of [false, true]) {
      const pane = c.doc ? (c.doc.at === "listed" ? "file" : FORMAT_VIEWER[c.doc.format] ?? null) : null;
      const sheets = pane ? 0 : c.sheets;
      const view = documentView({ doc: c.doc, url: c.url, sheets, notebook: c.notebook, hidden: c.hidden, dock });
      const showPdf = !!c.url && !c.hidden && !dock;
      const showPane = !!pane && pane !== "pdf" && !c.hidden && !dock;
      const showBook = !c.url && !pane && c.notebook && sheets > 0 && !dock;
      assert.equal(view.shown, showPdf || showPane || showBook, JSON.stringify({ ...c, dock }));
      if (view.shown) assert.equal(view.viewer, showPdf ? "pdf" : showPane ? pane : "notebook", JSON.stringify({ ...c, dock }));
      if (dock) assert.equal(view.center, true, "the dock is the notes");
    }
  }
});
