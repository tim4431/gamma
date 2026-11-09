import test from "node:test";
import assert from "node:assert/strict";
import { bibliographyPreviewUrl, bibliographyUrl, exportFormatOf, exportJobBody, exportSummary, hasReviewStep,
  importSource, notionFiles, offersSubPages, resolveExport, resolveImport } from "../src/transfers/transferFormats.js";

const allOff = Object.freeze({ highlights: false, notes: false, bundle: false });
// The annotated PDF's own options, as every other format's payload has them,
// and the pages inside a page, which these have none of: off.
const noCards = Object.freeze({ comments: false, notesAt: "beside", subPages: false });
const paper = { hasPdf: true, pdfStored: true };

test("fixed export contents override saved switches without changing the saved preferences", () => {
  const gamma = resolveExport({ ...allOff, format: "gamma" }, paper);
  assert.equal(gamma.needsReview, false);
  assert.deepEqual(gamma.payload, { format: "gamma", highlights: true, notes: true, bundle: true, ...noCards });
  const graph = resolveExport({ ...allOff, format: "logseq" }, paper);
  assert.equal(graph.needsReview, true);
  assert.deepEqual(graph.controls.map(({ key }) => key), ["bundle"]);
  assert.deepEqual(graph.payload, { format: "logseq", highlights: true, notes: true, bundle: false, ...noCards });
  assert.deepEqual(resolveExport({ ...allOff, format: "markdown" }, paper).payload, { format: "markdown", ...allOff, ...noCards });
});

test("remote PDFs export directly; note pages fall back to a configurable notes PDF", () => {
  const opts = { format: "pdf", highlights: true, notes: true, bundle: true };
  const remote = resolveExport(opts, { hasPdf: true, pdfStored: false });
  assert.equal(remote.needsReview, false);
  assert.deepEqual(remote.payload, { format: "pdf", ...allOff, ...noCards });
  const notes = resolveExport(opts, { hasPdf: false });
  assert.equal(notes.needsReview, true);
  assert.deepEqual(notes.payload, { format: "notespdf", highlights: true, notes: true, bundle: false, ...noCards });
  assert(!notes.formats.some(({ id }) => id === "pdf"));
});

test("a folder exports its papers' annotated PDFs as one zip, with the same switches", () => {
  const opts = { format: "pdf", highlights: true, notes: false, bundle: true };
  const folder = resolveExport(opts, { folder: "Reading" });
  assert.equal(folder.definition.category, "Papers");
  assert.match(folder.definition.hint, /one \.zip/);
  assert.equal(folder.needsReview, true);
  assert.deepEqual(folder.controls.map(({ key }) => key), ["highlights", "notes", "notesAt", "comments"]);
  assert.deepEqual(folder.payload, { format: "pdf", highlights: true, notes: false, bundle: false, ...noCards });
  assert.match(exportSummary(folder, "Reading"), /Pages without a PDF are left out/);
});

test("an annotated PDF with only the notes switch still carries its text boxes and pictures as annotations", () => {
  const notesOnly = resolveExport({ format: "pdf", highlights: false, notes: true, bundle: false }, paper);
  assert.equal(exportSummary(notesOnly), "The PDF with text box and picture annotations only and your notes in a column beside each page.");
  const folder = resolveExport({ format: "pdf", highlights: false, notes: true, bundle: false }, { folder: "Reading" });
  assert.match(exportSummary(folder, "Reading"), /with text box and picture annotations only and your notes in a column beside each page/);
  const both = resolveExport({ format: "pdf", highlights: true, notes: true, bundle: false }, paper);
  assert.equal(exportSummary(both), "The PDF with highlight annotations and your notes in a column beside each page.");
  const none = resolveExport({ format: "pdf", ...allOff }, paper);
  assert.equal(exportSummary(none), "The PDF file exactly as stored, with nothing added.");
});

test("an annotated PDF's notes go beside the page or on it, and its comment threads beside it", () => {
  const onPage = resolveExport({ format: "pdf", highlights: true, notes: true, notesAt: "page", comments: false }, paper);
  assert.deepEqual(onPage.payload, { format: "pdf", highlights: true, notes: true, bundle: false, comments: false, notesAt: "page", subPages: false });
  const place = onPage.controls.find(({ key }) => key === "notesAt");
  assert.deepEqual(place.choice.map(([value]) => value), ["beside", "page"]);
  assert.equal(place.disabled, false);
  assert.equal(place.hint, "In the page's own free space");
  assert.equal(exportSummary(onPage), "The PDF with highlight annotations and every note printed onto the page.");
  // the place waits on the Notes switch; Comments stand on their own
  const commentsOnly = resolveExport({ format: "pdf", highlights: false, notes: false, comments: true }, paper);
  assert.equal(commentsOnly.controls.find(({ key }) => key === "notesAt").disabled, true);
  assert.equal(commentsOnly.payload.comments, true);
  assert.equal(exportSummary(commentsOnly), "The PDF with text box and picture annotations only and the comment threads in a column beside each page.");
  assert.equal(exportSummary(resolveExport({ format: "pdf", highlights: true, notes: true, comments: true }, paper)),
    "The PDF with highlight annotations and your notes and comment threads in a column beside each page.");
  assert.equal(exportSummary(resolveExport({ format: "pdf", highlights: true, notes: true, notesAt: "page", comments: true }, paper)),
    "The PDF with highlight annotations and your notes printed onto the page, the comment threads beside it.");
  // another format has neither, whatever was saved
  const markdown = resolveExport({ format: "markdown", highlights: true, notes: true, comments: true, notesAt: "page" }, paper);
  assert.equal(markdown.payload.comments, false);
  assert.equal(markdown.payload.notesAt, "beside");
  // a stored file needs no review even with comments saved on
  assert.equal(resolveExport({ format: "pdf", comments: true }, { hasPdf: true, pdfStored: false }).payload.comments, false);
});

test("an export job's body names the server's mode for a page or a folder", () => {
  assert.deepEqual(exportJobBody({ format: "pdf", highlights: true, notes: false, bundle: false }, { folder: "Reading" }),
    { folder: "Reading", mode: "annotated-pdf", pdf: false, highlights: true, notes: false, comments: false, notes_at: "beside" });
  assert.deepEqual(exportJobBody({ format: "pdf", highlights: false, notes: true, comments: true, notesAt: "page" }, { pageId: "p1" }),
    { page_id: "p1", sub_pages: false, mode: "annotated-pdf", pdf: false, highlights: false, notes: true, comments: true, notes_at: "page" });
  assert.deepEqual(exportJobBody({ format: "logseq", highlights: true, notes: true, bundle: true }, { pageId: "p1" }),
    { page_id: "p1", sub_pages: false, mode: "logseq-graph", pdf: true, highlights: true, notes: true });
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

test("imports of embedded annotations carry the strip preference, and only Zotero reviews", () => {
  for (const source of ["gamma", "markdown", "notion", "logseq", "notability", "goodnotes"]) {
    const result = resolveImport(source, { hasPdf: true, strip: true });
    assert.equal(result.needsReview, false);
    assert.deepEqual(result.payload, { source });
  }
  for (const [source, review] of [["annots", false], ["zotero", true]]) {
    for (const strip of [true, false]) {
      const result = resolveImport(source, { hasPdf: true, strip });
      assert.equal(result.needsReview, review);
      assert.deepEqual(result.payload, { source, strip });
    }
  }
  assert.equal(resolveImport("annots", { hasPdf: false }).definition.id, "zotero");
});

test("a .zip already chosen is offered only the sources that read an archive", () => {
  const archive = resolveImport("markdown", { hasPdf: true, archive: true });
  assert.deepEqual(archive.formats.map(({ id }) => id), ["zotero", "markdown", "notability", "goodnotes", "notion", "gamma"]);
  assert.equal(archive.definition.id, "markdown");
  assert.equal(resolveImport("logseq", { archive: true }).definition.id, "zotero", "a source that reads no archive falls back");
});

test("BibTeX is offered where there is something to cite, and reviews itself", () => {
  // No switches, but the step is worth showing: it previews the real entries.
  const page = resolveExport({ format: "bibtex", ...allOff }, { ...paper, hasMeta: true });
  assert.equal(page.definition.id, "bibtex");
  assert.equal(page.needsReview, true);
  assert.deepEqual(page.controls, []);
  assert.equal(hasReviewStep(page.definition), true);
  assert.deepEqual(page.payload, { format: "bibtex", ...allOff, ...noCards });
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
  // with the pages inside it, as the file would hold them; a folder's always bring theirs
  assert.equal(bibliographyPreviewUrl("/api", { pageId: "p 1", subPages: true }), "/api/bibliography?page_id=p%201&sub_pages=1");
  assert.equal(bibliographyPreviewUrl("/api", { folder: "f1", subPages: true }), "/api/bibliography?folder=f1");
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

test("a Notability note has no switches: it exports straight from the first step, a folder's as one .zip", () => {
  const note = resolveExport({ format: "notability", highlights: true, notes: false, bundle: false }, paper);
  assert.equal(note.definition.category, "Notes");
  assert.equal(note.needsReview, false);
  assert.deepEqual(note.payload, { format: "notability", highlights: false, notes: true, bundle: true, ...noCards });
  assert.match(exportSummary(note), /brings the page back as it was/);
  assert.equal(resolveExport({ format: "notability" }, { hasPdf: false }).definition.id, "notability");
  const folder = resolveExport({ format: "notability" }, { folder: "Reading" });
  assert.match(folder.definition.hint, /one \.zip/);
  assert.deepEqual(exportJobBody(folder.payload, { folder: "Reading" }),
    { folder: "Reading", mode: "notability", pdf: true, highlights: false, notes: true });
  assert.equal(exportFormatOf("notability").label, "Notability");
});

test("Notion's zip reviews its two switches and always bundles, and its text claims nothing of what Notion keeps", () => {
  const zip = resolveExport({ format: "notion", highlights: true, notes: true, bundle: false }, paper);
  assert.equal(zip.definition.category, "Notes");
  assert.equal(zip.needsReview, true);
  assert.deepEqual(zip.controls.map(({ key }) => key), ["highlights", "notes"]);
  assert.deepEqual(zip.controls.map(({ hint }) => hint), ["Quotes with page numbers", "Your notes as paragraphs and lists"]);
  assert.deepEqual(zip.payload, { format: "notion", highlights: true, notes: true, bundle: true, ...noCards });
  assert.equal(resolveExport({ format: "notion" }, { hasPdf: false }).definition.id, "notion");
  assert.deepEqual(exportJobBody(zip.payload, { pageId: "p1" }),
    { page_id: "p1", sub_pages: false, mode: "notion", pdf: true, highlights: true, notes: true });
  assert.equal(exportFormatOf("notion").label, "Notion");

  const rest = "PDFs, recordings and other files stay out. What Notion keeps of the rest is up to its importer.";
  assert.equal(exportSummary(zip), `A .zip for Notion's importer: this page as Markdown with quoted highlights and your notes, images and handwriting included. ${rest}`);
  assert.match(exportSummary(resolveExport({ format: "notion", highlights: true, notes: true }, { folder: "Reading" }), "Reading"),
    /^A \.zip for Notion's importer: every page in the folder as Markdown with quoted highlights and your notes, images and handwriting included\./);
});

test("Notion's card names the images each switch leaves in the zip", () => {
  const rest = "PDFs, recordings and other files stay out. What Notion keeps of the rest is up to its importer.";
  const card = (highlights, notes) => {
    const zip = resolveExport({ format: "notion", highlights, notes }, paper);
    const { title, hint } = notionFiles(zip.payload);
    return [exportSummary(zip), title, hint];
  };
  assert.deepEqual(card(true, true),
    [`A .zip for Notion's importer: this page as Markdown with quoted highlights and your notes, images and handwriting included. ${rest}`,
      "Images and handwriting included", "PDFs, recordings and other files stay out"]);
  assert.deepEqual(card(false, true),
    [`A .zip for Notion's importer: this page as Markdown with no quotes and your notes, images included, handwriting left out. ${rest}`,
      "Images included", "Handwriting, PDFs, recordings and other files stay out"]);
  assert.deepEqual(card(true, false),
    [`A .zip for Notion's importer: this page as Markdown with quoted highlights, handwriting included, images left out. ${rest}`,
      "Handwriting included", "Images, PDFs, recordings and other files stay out"]);
  assert.deepEqual(card(false, false),
    [`A .zip for Notion's importer: this page as Markdown with the title and metadata only — both switches are off. ${rest}`,
      "No images or handwriting", "The .zip holds the Markdown alone"]);
});

test("each import source says what its picker takes, the note apps several files", () => {
  assert.equal(importSource("notability").multiple, true);
  assert.equal(importSource("goodnotes").accept, ".goodnotes,.zip,application/zip");
  assert.equal(importSource("markdown").multiple, undefined);
  assert.equal(importSource("markdown").accept, ".md,.markdown,.zip,text/markdown,application/zip");
  for (const id of ["zotero", "notion", "gamma"]) assert.equal(importSource(id).accept, ".zip,application/zip");
});

test("a page with pages inside it offers Include pages inside on every format's review, off by default", () => {
  const nested = { ...paper, hasMeta: true, subPages: 3 };
  for (const format of ["pdf", "bibtex", "notespdf", "markdown", "notability", "notion", "obsidian", "logseq", "zotero", "gamma"]) {
    const result = resolveExport({ format, highlights: true, notes: true, bundle: true }, nested);
    assert.equal(result.definition.id, format);
    const control = result.controls.find(({ key }) => key === "subPages");
    assert(control, `${format} has the switch`);
    assert.equal(control.hint, "Adds the 3 pages inside it, at any depth");
    assert.equal(result.payload.subPages, false);
    // the formats that export straight from step one get the review step for it
    assert.equal(result.needsReview, true);
    assert.equal(resolveExport({ format, subPages: true }, nested).payload.subPages, true);
  }
  assert.equal(resolveExport({ format: "markdown" }, { ...paper, subPages: 1 }).controls.at(-1).hint, "Adds the 1 page inside it");
  // without pages inside, or for a folder, there is no switch and a saved one stays off
  for (const context of [paper, { folder: "Reading", subPages: 3 }]) {
    const gamma = resolveExport({ format: "gamma", subPages: true }, context);
    assert.equal(gamma.needsReview, false);
    assert.equal(gamma.payload.subPages, false);
    assert(!resolveExport({ format: "markdown", subPages: true }, context).controls.some(({ key }) => key === "subPages"));
  }
  // a PDF only its original file can be exports that file straight away
  const remote = resolveExport({ format: "pdf", subPages: true }, { hasPdf: true, pdfStored: false, subPages: 3 });
  assert.equal(remote.needsReview, false);
  assert.equal(remote.payload.subPages, false);
  // the job step's breadcrumb agrees
  assert.equal(offersSubPages({ folder: null, subPages: 2 }), true);
  assert.equal(offersSubPages({ folder: "Reading", subPages: 2 }), false);
  assert.equal(offersSubPages({ folder: null, subPages: 0 }), false);
});

test("the pages inside travel in the job's body and the summary", () => {
  const on = resolveExport({ format: "notability", subPages: true }, { ...paper, subPages: 2 });
  const body = exportJobBody(on.payload, { pageId: "p1" });
  assert.deepEqual(body, { page_id: "p1", sub_pages: true, mode: "notability", pdf: true, highlights: false, notes: true });
  assert.match(exportSummary(on), /^This page as a Notability note: .* The 2 pages inside it are exported with it\.$/);
  // a page share's view counts only the pages right inside: the words name no number
  const direct = resolveExport({ format: "notability", subPages: true }, { ...paper, subPages: 1, subPagesDirect: true });
  assert.equal(direct.controls.at(-1).hint, "Adds the pages inside it, at any depth");
  assert.match(exportSummary(direct), / The pages inside it are exported with it\.$/);
  // a bibliography says which of them it cites rather than that all of them come
  const bib = resolveExport({ format: "bibtex", subPages: true }, { ...paper, hasMeta: true, subPages: 3 });
  assert.equal(exportSummary(bib), "One .bib file with the citation entry of this paper and of every page inside it. Clashing citation keys get a, b, … and a key you pinned on a page keeps its exact spelling. Pages without paper metadata are left out.");
  assert.equal(exportSummary(resolveExport({ format: "bibtex" }, { ...paper, hasMeta: true, subPages: 3 })),
    "This paper's citation entry as a .bib file, under its citation key.");
  const off = resolveExport({ format: "notability", subPages: false }, { ...paper, subPages: 2 });
  assert.doesNotMatch(exportSummary(off), /inside it/);
  assert.equal(exportJobBody(off.payload, { pageId: "p1" }).sub_pages, false);
  // a folder's body names no such flag: its pages always bring theirs
  assert.equal("sub_pages" in exportJobBody(on.payload, { folder: "Reading" }), false);
  // the bibliography link refreshes the same export
  assert.equal(bibliographyUrl("/api", { pageId: "p1", share: "tok", subPages: true }),
    "/api/pages/p1/export?mode=bibtex&sub_pages=1&share=tok");
  assert.equal(bibliographyUrl("/api", { folder: "f1", subPages: true }), "/api/folders/f1/export?mode=bibtex");
});

test("a page's annotated PDF says it writes the translation the viewer shows; nothing else changes", () => {
  const translated = { pages: 3, language: "中文（简体）" };
  const all = resolveExport({ format: "pdf", highlights: true, notes: true, comments: false }, { ...paper, translated });
  assert.equal(exportSummary(all), "The PDF with highlight annotations and your notes in a column beside each page."
    + " The translation into 中文（简体） replaces the original text on 3 pages. To export the original, show it first with the translate button.");
  const one = resolveExport({ format: "pdf", highlights: false, notes: false, comments: false }, { ...paper, translated: { ...translated, pages: 1 } });
  assert.equal(exportSummary(one), "The PDF as stored, with nothing else added. The translation into 中文（简体） replaces the original text on 1 page."
    + " To export the original, show it first with the translate button.",
    "with every switch off it is no longer the file exactly as stored");
  // the switches, the payload and the job's body are those of the same export untranslated
  const plain = resolveExport({ format: "pdf", highlights: true, notes: true, comments: false }, paper);
  assert.deepEqual(all.payload, plain.payload);
  assert.deepEqual(all.controls, plain.controls);
  assert.equal(all.needsReview, plain.needsReview);
  assert.deepEqual(exportJobBody(all.payload, { pageId: "p1" }), exportJobBody(plain.payload, { pageId: "p1" }));
  // the pages inside come after it
  const inside = resolveExport({ format: "pdf", highlights: true, notes: false, subPages: true }, { ...paper, subPages: 2, translated });
  assert.match(exportSummary(inside), /show it first with the translate button\. The 2 pages inside it are exported with it\.$/);
  // not for another format, a folder, a PDF the server does not store, or no page translated
  for (const [opts, context] of [
    [{ format: "notespdf", highlights: true, notes: true }, { ...paper, translated }],
    [{ format: "pdf", highlights: true, notes: false }, { folder: "Reading", translated }],
    [{ format: "pdf", highlights: true, notes: false }, { hasPdf: true, pdfStored: false, translated }],
    [{ format: "pdf", highlights: false, notes: false }, { ...paper, translated: { ...translated, pages: 0 } }],
    [{ format: "pdf", highlights: false, notes: false }, { ...paper, translated: null }],
  ]) {
    const resolved = resolveExport(opts, context);
    assert.equal(resolved.translated, null);
    assert.doesNotMatch(exportSummary(resolved, context.folder), /translation/);
  }
  assert.equal(exportSummary(resolveExport({ format: "pdf", ...allOff }, { ...paper, translated: null })),
    "The PDF file exactly as stored, with nothing added.");
});

test("while the pages a paper keeps are read in, the summary names no number of pages", () => {
  const counting = { pages: 2, language: "中文（简体）", counting: true };
  const resolved = resolveExport({ format: "pdf", highlights: true, notes: false, comments: false }, { ...paper, translated: counting });
  assert.equal(exportSummary(resolved), "The PDF with highlight annotations."
    + " The translation into 中文（简体） replaces the original text on the pages it covers, which are being counted."
    + " To export the original, show it first with the translate button.");
  assert.doesNotMatch(exportSummary(resolved), /\b2 pages\b/);
});

test("a folder's annotated PDF says how many of its papers go translated, and names no number while they are read", () => {
  const folder = "Reading";
  const translated = { papers: 2, language: "中文（简体）" };
  const on = resolveExport({ format: "pdf", highlights: true, notes: false, comments: false }, { folder, translated });
  assert.equal(exportSummary(on, folder), "Every paper's PDF in one .zip, with highlight annotations, its subfolders as folders."
    + " Pages without a PDF are left out. The translation into 中文（简体） replaces the original text of 2 papers,"
    + " which this browser left showing it. To export a paper's original, show it first with the translate button.");
  const off = resolveExport({ format: "pdf", ...allOff }, { folder, translated: { ...translated, papers: 1 } });
  assert.match(exportSummary(off, folder), /^Every paper's PDF in one \.zip with nothing else added, .* replaces the original text of 1 paper,/);
  const counting = resolveExport({ format: "pdf", highlights: true, notes: false }, { folder, translated: { ...translated, counting: true } });
  assert.match(exportSummary(counting, folder), /of the papers this browser left showing it, which are being counted\./);
  assert.doesNotMatch(exportSummary(counting, folder), /\b2 papers\b/);
  // none, or a page's count: nothing said, and the folder's file as stored
  for (const t of [null, { ...translated, papers: 0 }, { pages: 3, language: "x" }]) {
    const resolved = resolveExport({ format: "pdf", ...allOff }, { folder, translated: t });
    assert.equal(resolved.translated, null);
    assert.match(exportSummary(resolved, folder), /exactly as stored/);
  }
});
