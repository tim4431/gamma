import test from "node:test";
import assert from "node:assert/strict";
import { bibliographyPreviewUrl, bibliographyUrl, exportFormatOf, exportJobBody, exportSummary, hasReviewStep, resolveExport, resolveImport } from "../src/transfers/transferFormats.js";

const allOff = Object.freeze({ highlights: false, notes: false, bundle: false });
const paper = { hasPdf: true, pdfStored: true };

test("fixed export contents override saved switches without changing the saved preferences", () => {
  const gamma = resolveExport({ ...allOff, format: "gamma" }, paper);
  assert.equal(gamma.needsReview, false);
  assert.deepEqual(gamma.payload, { format: "gamma", highlights: true, notes: true, bundle: true });
  const graph = resolveExport({ ...allOff, format: "logseq" }, paper);
  assert.equal(graph.needsReview, true);
  assert.deepEqual(graph.controls.map(({ key }) => key), ["bundle"]);
  assert.deepEqual(graph.payload, { format: "logseq", highlights: true, notes: true, bundle: false });
  assert.deepEqual(resolveExport({ ...allOff, format: "markdown" }, paper).payload, { format: "markdown", ...allOff });
});

test("remote PDFs export directly; note pages fall back to a configurable notes PDF", () => {
  const opts = { format: "pdf", highlights: true, notes: true, bundle: true };
  const remote = resolveExport(opts, { hasPdf: true, pdfStored: false });
  assert.equal(remote.needsReview, false);
  assert.deepEqual(remote.payload, { format: "pdf", ...allOff });
  const notes = resolveExport(opts, { hasPdf: false });
  assert.equal(notes.needsReview, true);
  assert.deepEqual(notes.payload, { format: "notespdf", highlights: true, notes: true, bundle: false });
  assert(!notes.formats.some(({ id }) => id === "pdf"));
});

test("a folder exports its papers' annotated PDFs as one zip, with both switches", () => {
  const opts = { format: "pdf", highlights: true, notes: false, bundle: true };
  const folder = resolveExport(opts, { folder: "Reading" });
  assert.equal(folder.definition.category, "Papers");
  assert.match(folder.definition.hint, /one \.zip/);
  assert.equal(folder.needsReview, true);
  assert.deepEqual(folder.controls.map(({ key }) => key), ["highlights", "notes"]);
  assert.deepEqual(folder.payload, { format: "pdf", highlights: true, notes: false, bundle: false });
  assert.match(exportSummary(folder, "Reading"), /Pages without a PDF are left out/);
});

test("an annotated PDF with only the notes switch still carries its text boxes as annotations", () => {
  const notesOnly = resolveExport({ format: "pdf", highlights: false, notes: true, bundle: false }, paper);
  assert.equal(exportSummary(notesOnly), "The PDF with text box annotations only and every note printed onto the page.");
  const folder = resolveExport({ format: "pdf", highlights: false, notes: true, bundle: false }, { folder: "Reading" });
  assert.match(exportSummary(folder, "Reading"), /with text box annotations only and every note printed onto the page/);
  const both = resolveExport({ format: "pdf", highlights: true, notes: true, bundle: false }, paper);
  assert.equal(exportSummary(both), "The PDF with highlight annotations and every note printed onto the page.");
  const none = resolveExport({ format: "pdf", ...allOff }, paper);
  assert.equal(exportSummary(none), "The PDF file exactly as stored, with nothing added.");
});

test("an export job's body names the server's mode for a page or a folder", () => {
  assert.deepEqual(exportJobBody({ format: "pdf", highlights: true, notes: false, bundle: false }, { folder: "Reading" }),
    { folder: "Reading", mode: "annotated-pdf", pdf: false, highlights: true, notes: false });
  assert.deepEqual(exportJobBody({ format: "logseq", highlights: true, notes: true, bundle: true }, { pageId: "p1" }),
    { page_id: "p1", mode: "logseq-graph", pdf: true, highlights: true, notes: true });
  assert.equal(exportFormatOf("zotero-rdf").id, "zotero");
  assert.equal(exportFormatOf("markdown").mode, "readable");
  assert.equal(exportFormatOf("pptx"), null);
});

test("Zotero's effective highlights follow file bundling while preserving the user's preference", () => {
  const opts = Object.freeze({ format: "zotero", highlights: true, notes: true, bundle: false });
  const metadata = resolveExport(opts, paper);
  assert.equal(metadata.payload.highlights, false);
  assert.equal(metadata.controls.find(({ key }) => key === "highlights").disabled, true);
  assert.equal(metadata.needsReview, true);
  assert.equal(resolveExport({ ...opts, bundle: true }, paper).payload.highlights, true);
  assert.equal(opts.highlights, true);
});

test("imports show options only for embedded annotations and omit inapplicable strip flags", () => {
  for (const source of ["gamma", "markdown", "logseq"]) {
    const result = resolveImport(source, { hasPdf: true, strip: true });
    assert.equal(result.needsReview, false);
    assert.deepEqual(result.payload, { source });
  }
  for (const source of ["annots", "zotero"]) {
    const result = resolveImport(source, { hasPdf: true, strip: true });
    assert.equal(result.needsReview, true);
    assert.deepEqual(result.payload, { source, strip: true });
  }
  assert.equal(resolveImport("annots", { hasPdf: false }).definition.id, "zotero");
});

test("BibTeX is offered where there is something to cite, and reviews itself", () => {
  // No switches, but the step is worth showing: it previews the real entries.
  const page = resolveExport({ format: "bibtex", ...allOff }, { ...paper, hasMeta: true });
  assert.equal(page.definition.id, "bibtex");
  assert.equal(page.needsReview, true);
  assert.deepEqual(page.controls, []);
  assert.equal(hasReviewStep(page.definition), true);
  assert.deepEqual(page.payload, { format: "bibtex", ...allOff });
  // The step renders the summary, so it has to say something for one page too.
  assert.match(exportSummary(page), /citation entry as a \.bib file/);

  // A page with no paper record has no entry to write, so the card is absent.
  const bare = resolveExport({ format: "bibtex", ...allOff }, paper);
  assert(!bare.formats.some(({ id }) => id === "bibtex"));
  assert.notEqual(bare.definition.id, "bibtex");

  // A folder always offers it — its pages are what get cited.
  const folder = resolveExport({ format: "bibtex", ...allOff }, { folder: "Reading" });
  assert.equal(folder.definition.category, "Papers");
  assert.match(folder.definition.hint, /One \.bib/);
  assert.match(exportSummary(folder, "Reading"), /Pages without paper metadata are left out/);
  assert.deepEqual(exportJobBody(folder.payload, { folder: "Reading" }),
    { folder: "Reading", mode: "bibtex", pdf: false, highlights: false, notes: false });
  assert.equal(exportFormatOf("bibtex").id, "bibtex");
});

test("the review is fetched for whichever target the dialog is on", () => {
  assert.equal(bibliographyPreviewUrl("/api", { folder: "f1" }), "/api/bibliography?folder=f1");
  assert.equal(bibliographyPreviewUrl("/api", { pageId: "p 1" }), "/api/bibliography?page_id=p%201");
});

test("the bibliography URL is the same path for a fetch and for a pasted link", () => {
  // No token: the bare export path.
  assert.equal(bibliographyUrl("/api", { folder: "f1" }), "/api/folders/f1/export?mode=bibtex");
  assert.equal(bibliographyUrl("/api", { pageId: "p 1" }), "/api/pages/p%201/export?mode=bibtex");
  // With one: the fixed link Overleaf refreshes from. The token names the
  // workspace, so no ?ws= rides along.
  const shared = bibliographyUrl("https://host/api", { folder: "f1", share: "tok en" });
  assert.equal(shared, "https://host/api/folders/f1/export?mode=bibtex&share=tok%20en");
  assert(!shared.includes("ws="));
});
