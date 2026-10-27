import { t, T } from "../shared/i18n/i18n.js";
// Format capabilities are independent of the dialog. Only editable options
// become switches; fixed values still reach the preview and export handler.
// What the Highlights / Notes / Comments / Bundle switches mean per format: a
// short hint under the label, the long explanation on hover. Only editable
// options need text.
const EXPORT_SWITCH_TEXT = {
  highlights: {
    pdf: [t("Standard PDF annotations"),
      t("Burned in as standard PDF annotations — they survive in Acrobat, SumatraPDF, browsers.")],
    notespdf: [t("Quoted passages with page numbers"),
      t("Each highlighted passage as a quote with its page number, in the colour you highlighted it.")],
    markdown: [t("Blockquotes with page numbers"),
      t("Each highlighted passage as a blockquote with its page number.")],
    obsidian: [t("Quote callouts linking the PDF page"),
      t("Each highlighted passage as a [!quote] callout whose title opens the bundled PDF at that page.")],
    zotero: [t("Embedded into the exported PDF copies"),
      t("Written into the exported PDF copies as standard annotations — Zotero's own “Include Annotations” convention, its reader picks them up on import.")],
  },
  notes: {
    pdf: [t("Each highlight's writing as a card"),
      t("Each highlight's notes as a card, beside the page or on it as chosen below. Off: they stay in the annotation popups.")],
    notespdf: [t("Typeset under their highlights"),
      t("Your own writing, typeset under the highlight it belongs to — headings, lists, code, math and pasted images included.")],
    markdown: [t("Nested under their highlights"),
      t("Your own writing, nested under the highlight it belongs to.")],
    obsidian: [t("Headings, paragraphs and lists"),
      t("Your own writing as a document: top-level headings and paragraphs, deeper blocks as nested lists, mentions and synced blocks as wikilinks.")],
    zotero: [t("Zotero notes on each item"),
      t("Top-level notes become Zotero notes attached to the item; writing under a highlight travels in its annotation popup.")],
  },
  comments: {
    pdf: [t("Open threads beside each page"),
      t("Each highlight's open comment threads, and those on its notes, as cards in a column beside the page, as the viewer's margin shows them. When the notes are beside the page too, a note's comments sit under it. Resolved threads stay out.")],
  },
  bundle: {
    markdown: [t("Pack the PDF and images into the .zip"),
      t("Pack the PDF and any pasted images into the .zip. Off: they stay as links back to this server.")],
    obsidian: [t("PDF and images into attachments/"),
      t("Put the PDF (named after its page) and any pasted images into the vault's attachments/ folder. Off: they stay as links back to this server.")],
    logseq: [t("Pack the PDF and images into the .zip"),
      t("Pack the PDF and any pasted images into the .zip. Off: they stay as links back to this server.")],
    zotero: [t("Include the PDF files (Zotero's “Export Files”)"),
      t("Pack each paper's PDF into the .zip so Zotero imports the files too. Off: metadata, collections and notes only.")],
  },
};

// Where an annotated PDF's notes go (routers/export.py `notes_at`): the
// choice's [value, label] and what each means. Comment threads always go
// beside the page.
export const NOTES_PLACES = [["beside", T("Beside the page")], ["page", T("On the page")]];
const NOTES_PLACE_TEXT = {
  beside: [t("In a column beside each page"),
    t("In a column beside each page, level with the passages they belong to, as the viewer shows comments.")],
  page: [t("In the page's own free space"),
    t("Printed onto the page in nearby free space, with a line back to the highlight. The page keeps its size.")],
};

// The rows of both dialogs, in order; a format's `category` names its row.
export const CATEGORIES = [T("This paper"), T("Papers"), T("Notes"), T("Library")];

// `mode`: the server's name for the format (POST /api/jobs/export,
// routers/export.py). A folder exports the annotated PDFs of all its papers,
// so there that format is "Papers", with its own hint.
const EXPORT_FORMATS = [
  { id: "pdf", mode: "annotated-pdf", label: T("Annotated PDF"), category: "This paper",
    hint: T("Your original paper, with annotations"), editable: ["highlights", "notes", "notesAt", "comments"],
    fixed: { bundle: false },
    folder: { category: "Papers", hint: T("Each paper's PDF with its annotations, in one .zip") } },
  { id: "bibtex", mode: "bibtex", label: "BibTeX", category: "This paper",
    hint: T("A citation entry for LaTeX"), editable: [], fixed: { highlights: false, notes: false, bundle: false },
    review: true,  // its step shows the real entries, so it has one without any switch
    folder: { category: "Papers", hint: T("One .bib with every paper's entry") } },
  { id: "notespdf", mode: "notes-pdf", label: "PDF", category: "Notes",
    hint: T("A typeset document of highlights and notes"), editable: ["highlights", "notes"], fixed: { bundle: false } },
  { id: "markdown", mode: "readable", label: T("Markdown"), category: "Notes",
    hint: T("Readable notes for any Markdown editor"), editable: ["highlights", "notes", "bundle"] },
  // A note holds everything a page has but its highlights, so it has no switches.
  { id: "notability", mode: "notability", label: "Notability", category: "Notes",
    hint: T("A .note with the handwriting, to open in Notability"), editable: [],
    fixed: { highlights: false, notes: true, bundle: true },
    folder: { hint: T("Each page as a .note, in one .zip") } },
  { id: "obsidian", mode: "obsidian", label: T("Obsidian"), category: "Library",
    hint: T("A vault with notes, links and attachments"), editable: ["highlights", "notes", "bundle"] },
  { id: "logseq", mode: "logseq-graph", label: T("Logseq"), category: "Library",
    hint: T("A graph with native PDF highlights"), editable: ["bundle"], fixed: { highlights: true, notes: true } },
  { id: "zotero", mode: "zotero-rdf", label: T("Zotero"), category: "Library",
    hint: T("Papers, collections and notes for Zotero"), editable: ["highlights", "notes", "bundle"] },
  { id: "gamma", mode: "gamma", label: T("Gamma"), category: "Library",
    hint: T("A complete copy for another Gamma library"), editable: [], fixed: { highlights: true, notes: true, bundle: true } },
];

// The bibliography endpoint for a page or a folder (its id; routers/export.py
// ?mode=bibtex). `base` is `<origin>/api`: the link someone pastes into
// Overleaf. With `share` it carries a share token, which names the workspace
// and so needs neither a session nor a ?ws=.
export function bibliographyUrl(base, { pageId = "", folder = "", share = "" } = {}) {
  const token = share ? `&share=${encodeURIComponent(share)}` : "";
  return folder
    ? `${base}/folders/${encodeURIComponent(folder)}/export?mode=bibtex${token}`
    : `${base}/pages/${encodeURIComponent(pageId)}/export?mode=bibtex${token}`;
}

// The review the dialog reads before it downloads anything
// (routers/export.py GET /api/bibliography): the same entries, keys and
// left-out pages the file will have.
export function bibliographyPreviewUrl(base, { pageId = "", folder = "" } = {}) {
  return folder
    ? `${base}/bibliography?folder=${encodeURIComponent(folder)}`
    : `${base}/bibliography?page_id=${encodeURIComponent(pageId)}`;
}

// The format a server export mode is (an export job's params name the mode).
export function exportFormatOf(mode) {
  return EXPORT_FORMATS.find((format) => format.mode === mode || format.id === mode) || null;
}

// What an export job is asked to do: the dialog's payload for a page or a
// folder, in the server's terms (routers/export.py ExportJob).
export function exportJobBody(payload, { pageId = "", folder = "" } = {}) {
  const format = exportFormatOf(payload.format);
  const body = { ...(folder ? { folder } : { page_id: pageId }), mode: format?.mode || "readable",
    pdf: Boolean(payload.bundle), highlights: Boolean(payload.highlights), notes: Boolean(payload.notes) };
  // The annotated PDF's own options go with it alone.
  return format?.editable.includes("comments")
    ? { ...body, comments: Boolean(payload.comments), notes_at: payload.notesAt === "page" ? "page" : "beside" }
    : body;
}

// Whether a format's export goes through a review step: one with a switch to
// flip, or one whose own step is worth seeing (BibTeX shows the real entries).
// Both the setup and the job step read this, so their breadcrumbs agree.
export function hasReviewStep(format, controls) {
  return Boolean(format?.review) || (controls || format?.editable || []).length > 0;
}

// Resolve again using the activated card's ID on double-click. Do not depend
// on React having committed a preceding selection change.
// `hasMeta`: the open page has a paper record, so there is something to cite.
export function resolveExport(opts, { hasPdf, pdfStored, folder, hasMeta } = {}) {
  const formats = EXPORT_FORMATS
    .filter(({ id }) => (id !== "pdf" || hasPdf || folder) && (id !== "bibtex" || hasMeta || folder))
    .map((format) => (folder && format.folder ? { ...format, ...format.folder } : format));
  const definition = formats.find(({ id }) => id === opts.format) || formats.find(({ id }) => id === "notespdf");
  const noPdfCopy = definition.id === "pdf" && !folder && !pdfStored;
  // Comments and the notes' place are the annotated PDF's own options.
  const own = (key) => definition.editable.includes(key);
  const values = { highlights: Boolean(opts.highlights), notes: Boolean(opts.notes), bundle: Boolean(opts.bundle),
    comments: own("comments") && Boolean(opts.comments), notesAt: own("notesAt") && opts.notesAt === "page" ? "page" : "beside",
    ...definition.fixed };
  if (noPdfCopy) Object.assign(values, { highlights: false, notes: false, comments: false });
  // Zotero stores highlights inside the bundled PDFs, never in metadata alone.
  const withoutPdfFiles = definition.id === "zotero" && !values.bundle;
  if (withoutPdfFiles) values.highlights = false;
  // A switch, or for the notes' place a choice, which says where the notes
  // go and waits on the Notes switch.
  const controls = (noPdfCopy ? [] : definition.editable).map((key) => (key === "notesAt"
    ? { key, choice: NOTES_PLACES, hint: NOTES_PLACE_TEXT[values.notesAt][0], title: NOTES_PLACE_TEXT[values.notesAt][1],
      disabled: !values.notes }
    : { key, hint: EXPORT_SWITCH_TEXT[key][definition.id][0], title: EXPORT_SWITCH_TEXT[key][definition.id][1],
      disabled: key === "highlights" && withoutPdfFiles }));
  return { definition, formats, noPdfCopy, controls,
    needsReview: hasReviewStep(definition, controls.filter(({ disabled }) => !disabled)),
    payload: { format: definition.id, ...values } };
}

// What an annotated PDF's cards add, for its summary: the notes beside the
// page or on it, the comment threads beside it.
function cardsSummary({ notes, comments, notesAt }) {
  if (notes && comments) {
    return notesAt === "page" ? t(" and your notes printed onto the page, the comment threads beside it")
      : t(" and your notes and comment threads in a column beside each page");
  }
  if (notes) return notesAt === "page" ? t(" and every note printed onto the page") : t(" and your notes in a column beside each page");
  return comments ? t(" and the comment threads in a column beside each page") : "";
}

export function exportSummary({ payload, noPdfCopy }, folder) {
  const { format, highlights, notes, comments } = payload;
  switch (format) {
    case "bibtex":
      return folder
        ? t("One .bib file with the citation entry of every paper in the folder. Clashing citation keys get a, b, … and a key you pinned on a page keeps its exact spelling. Pages without paper metadata are left out.")
        : t("This paper's citation entry as a .bib file, under its citation key.");
    case "gamma":
      return t("A complete copy{folder}: pages, highlights, notes, metadata, AI chats and files. Ready to import into another Gamma library.", { folder: folder ? t(" of the folder") : "" });
    case "notability":
      return folder
        ? t("Every page in the folder as a Notability note, in one .zip, its subfolders as folders: each page's PDF or sheets of paper, its handwriting, shapes, text boxes, pictures, notes and recordings. Highlights stay behind: Notability keeps none on a PDF's text.")
        : t("This page as a Notability note: its PDF or sheets of paper, its handwriting (pressure and tilt included), shapes, text boxes, pictures, notes and recordings. Highlights stay behind: Notability keeps none on a PDF's text. Importing the note into Gamma brings the page back as it was.");
    case "logseq":
      return t("Highlights and notes are always included in a Logseq graph. Choose whether to include the PDF and images too.");
    case "obsidian":
      return t("An Obsidian vault: one note per page{folders}, links as [[wikilinks]], labels as tags{callouts}{lists}. Unzip it into a vault, or open it as one.", { folders: folder ? t(", subfolders as folders") : "", callouts: highlights ? t(", highlights as quote callouts") : "", lists: notes ? t(", your notes as headings, paragraphs and lists") : "" });
    case "pdf":
      // Text boxes are written as annotations with any switch (they are the
      // user's own writing on the page); highlights and ink only with the
      // first (routers/export.py).
      if (folder) {
        return highlights || notes || comments
          ? t("Every paper's PDF in one .zip, with {annotations}{page}, its subfolders as folders. Pages without a PDF are left out.", { annotations: highlights ? t("highlight annotations") : t("text box annotations only"), page: cardsSummary(payload) })
          : t("Every paper's PDF in one .zip, exactly as stored, its subfolders as folders. Pages without a PDF are left out.");
      }
      if (noPdfCopy) return t("This PDF isn't stored on the server, so only the file itself can be exported.");
      if (!highlights && !notes && !comments) return t("The PDF file exactly as stored, with nothing added.");
      return t("The PDF with {annotations}{page}.", { annotations: highlights ? t("highlight annotations") : t("text box annotations only"), page: cardsSummary(payload) });
    case "notespdf":
      if (!highlights && !notes) return t("A new PDF with the title and metadata only — both switches are off.");
      return t("A new PDF of {page} — title, {quotes}{typeset}.", { page: folder ? t("every page in the folder") : t("this page"), quotes: highlights ? t("quoted highlights") : t("no quotes"), typeset: notes ? t(" and your notes, typeset") : "" });
    case "markdown":
      return highlights || notes
        ? t("Markdown with {quotes}{notes}.", { quotes: highlights ? t("quoted highlights") : t("no quotes"), notes: notes ? t(" and your notes") : "" })
        : t("Markdown with the title and metadata only — both switches are off.");
    default:
      return "";
  }
}

// `instructions` is the preparation note shown under a selected card;
// `folderLabel` the button that picks the export's folder instead, first
// where the browser can pick folders (folderZip.js), the .zip then a link.
const IMPORT_SOURCES = [
  { id: "annots", label: T("Annotations in this PDF"), category: "This paper", hint: T("Bring embedded highlights and notes into Gamma"), strip: true, actionLabel: T("Import"),
    instructions: T("Highlights, notes and boxes saved inside this PDF (a Gamma export, SumatraPDF, Acrobat…) become regular blocks. Importing twice adds nothing — each annotation is matched to the block it already made.") },
  { id: "zotero", label: T("Zotero library"), category: "Library", hint: T("Papers, collections, tags and notes"), strip: true, archive: true, actionLabel: T("Choose .zip…"), folderLabel: T("Choose folder…") },
  { id: "markdown", label: T("Markdown notes"), category: "Notes", hint: T("Markdown files, Obsidian vaults or Notion exports"), archive: true, actionLabel: T("Choose file…"),
    instructions: T("A single .md becomes a note page. A .zip of Markdown — a zipped Obsidian vault, Notion's Export → Markdown & CSV (subpages included), a Gamma Markdown export, or any zipped folder of notes — becomes one page per file: folders become folders, links between the notes ([[wikilinks]] included) become mentions, ![[block]] embeds synced blocks, tags labels, and images and files come along. Notes already imported are skipped.") },
  { id: "notability", label: T("Notability notes"), category: "Notes", hint: T("Handwriting, PDFs, typed text and recordings"), archive: true, actionLabel: T("Choose files…"),
    instructions: T("Pick one or more .note or .ntb files, or a .zip of them: Notability's exports with File Format “Note” or “NTB” (in a note, the options icon → Export Options; in the library, press and hold a note → Export Options). Each note becomes a page: a PDF note keeps its PDF, a paper note becomes a notebook, and its handwriting, typed text and pictures come along, and from a “Note” export its recordings. Notes already imported are skipped.") },
  { id: "goodnotes", label: T("GoodNotes notebooks"), category: "Notes", hint: T("Handwriting, papers, PDFs, typed text and recordings"), archive: true, actionLabel: T("Choose files…"),
    instructions: T("Pick one or more .goodnotes files, or a .zip of them: GoodNotes' own format, which its iPad, iPhone and Mac apps export (a document's menu → Export → GoodNotes; a folder exports as one .zip). Each notebook becomes a page: one on GoodNotes' papers becomes a notebook of sheets, one on a PDF keeps the PDF, and its handwriting, shapes, typed text, pictures and recordings come along. GoodNotes on Android, Windows and the web exports PDF only: import that as a paper, then Annotations in this PDF. Notebooks already imported are skipped.") },
  { id: "logseq", label: T("Logseq highlights"), category: "This paper", hint: T("A PDF and its .edn, with optional notes"), actionLabel: T("Choose files…"),
    instructions: T("Pick a Logseq .pdf and its .edn (a .md of notes is optional). The paper and its highlights land in your library as a new page.") },
  { id: "gamma", label: T("Gamma export (.zip)"), category: "Library", hint: T("Merge pages, files and chats from Gamma"), archive: true, actionLabel: T("Choose .zip…"),
    instructions: T("A zip made by another Gamma's Export → Gamma format (a full backup works too). Its pages, files and chats merge into your library — nothing existing is touched, and re-importing the same zip adds nothing. (A single shared page needs no zip: paste its share link into the + menu.)") },
];

// `archive`: a .zip is already chosen (one the iPad app was given), so only
// the sources that read one (`archive`) are offered.
export function resolveImport(source, { hasPdf, strip, archive } = {}) {
  const formats = IMPORT_SOURCES.filter((format) => (format.id !== "annots" || hasPdf) && (!archive || format.archive));
  const definition = formats.find(({ id }) => id === source) || formats.find(({ id }) => id === "zotero");
  return { definition, formats, needsReview: Boolean(definition.strip),
    payload: definition.strip ? { source: definition.id, strip: Boolean(strip) } : { source: definition.id } };
}
