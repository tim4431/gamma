import { t, T } from "../shared/i18n/i18n.js";
// Format capabilities are independent of the dialog. Only editable options
// become switches; fixed values still reach the preview and export handler.
// What the Highlights / Notes / Bundle switches mean per format: a short hint
// under the label, the long explanation on hover. Only editable options need text.
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
    pdf: [t("Printed onto the page in free space"),
      t("Printed onto the page in nearby free space, with a line back to the highlight. Off: they stay in the annotation popups.")],
    notespdf: [t("Typeset under their highlights"),
      t("Your own writing, typeset under the highlight it belongs to — headings, lists, code, math and pasted images included.")],
    markdown: [t("Nested under their highlights"),
      t("Your own writing, nested under the highlight it belongs to.")],
    obsidian: [t("Headings, paragraphs and lists"),
      t("Your own writing as a document: top-level headings and paragraphs, deeper blocks as nested lists, mentions and synced blocks as wikilinks.")],
    zotero: [t("Zotero notes on each item"),
      t("Top-level notes become Zotero notes attached to the item; writing under a highlight travels in its annotation popup.")],
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

// The rows of both dialogs, in order; a format's `category` names its row.
export const CATEGORIES = [T("This paper"), T("Notes"), T("Library")];

const EXPORT_FORMATS = [
  { id: "pdf", label: T("Annotated PDF"), category: "This paper",
    hint: T("Your original paper, with annotations"), editable: ["highlights", "notes"], fixed: { bundle: false } },
  { id: "notespdf", label: "PDF", category: "Notes",
    hint: T("A typeset document of highlights and notes"), editable: ["highlights", "notes"], fixed: { bundle: false } },
  { id: "markdown", label: T("Markdown"), category: "Notes",
    hint: T("Readable notes for any Markdown editor"), editable: ["highlights", "notes", "bundle"] },
  { id: "obsidian", label: T("Obsidian"), category: "Library",
    hint: T("A vault with notes, links and attachments"), editable: ["highlights", "notes", "bundle"] },
  { id: "logseq", label: T("Logseq"), category: "Library",
    hint: T("A graph with native PDF highlights"), editable: ["bundle"], fixed: { highlights: true, notes: true } },
  { id: "zotero", label: T("Zotero"), category: "Library",
    hint: T("Papers, collections and notes for Zotero"), editable: ["highlights", "notes", "bundle"] },
  { id: "gamma", label: T("Gamma"), category: "Library",
    hint: T("A complete copy for another Gamma library"), editable: [], fixed: { highlights: true, notes: true, bundle: true } },
];

// Resolve again using the activated card's ID on double-click. Do not depend
// on React having committed a preceding selection change.
export function resolveExport(opts, { hasPdf, pdfStored, folder } = {}) {
  const formats = EXPORT_FORMATS.filter(({ id }) => id !== "pdf" || (hasPdf && !folder));
  const definition = formats.find(({ id }) => id === opts.format) || formats.find(({ id }) => id === "notespdf");
  const noPdfCopy = definition.id === "pdf" && !pdfStored;
  const values = { highlights: Boolean(opts.highlights), notes: Boolean(opts.notes), bundle: Boolean(opts.bundle), ...definition.fixed };
  if (noPdfCopy) Object.assign(values, { highlights: false, notes: false });
  // Zotero stores highlights inside the bundled PDFs, never in metadata alone.
  const withoutPdfFiles = definition.id === "zotero" && !values.bundle;
  if (withoutPdfFiles) values.highlights = false;
  const controls = (noPdfCopy ? [] : definition.editable).map((key) => ({
    key, hint: EXPORT_SWITCH_TEXT[key][definition.id][0], title: EXPORT_SWITCH_TEXT[key][definition.id][1],
    disabled: key === "highlights" && withoutPdfFiles,
  }));
  return { definition, formats, noPdfCopy, controls, needsReview: controls.some(({ disabled }) => !disabled),
    payload: { format: definition.id, ...values } };
}

export function exportSummary({ payload, noPdfCopy }, folder) {
  const { format, highlights, notes } = payload;
  switch (format) {
    case "gamma":
      return t("A complete copy{folder}: pages, highlights, notes, metadata, AI chats and files. Ready to import into another Gamma library.", { folder: folder ? t(" of the folder") : "" });
    case "logseq":
      return t("Highlights and notes are always included in a Logseq graph. Choose whether to include the PDF and images too.");
    case "obsidian":
      return t("An Obsidian vault: one note per page{folders}, links as [[wikilinks]], labels as tags{callouts}{lists}. Unzip it into a vault, or open it as one.", { folders: folder ? t(", subfolders as folders") : "", callouts: highlights ? t(", highlights as quote callouts") : "", lists: notes ? t(", your notes as headings, paragraphs and lists") : "" });
    case "pdf":
      if (noPdfCopy) return t("This PDF isn't stored on the server, so only the file itself can be exported.");
      if (!highlights && !notes) return t("The PDF file exactly as stored, with nothing added.");
      return t("The PDF with {annotations}{page}.", { annotations: highlights ? t("highlight annotations") : t("no annotations"), page: notes ? t(" and every note printed onto the page") : "" });
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

// `instructions` is the preparation note shown under a selected card.
const IMPORT_SOURCES = [
  { id: "annots", label: T("Annotations in this PDF"), category: "This paper", hint: T("Bring embedded highlights and notes into Gamma"), strip: true, actionLabel: T("Import"),
    instructions: T("Highlights, notes and boxes saved inside this PDF (a Gamma export, SumatraPDF, Acrobat…) become regular blocks. Importing twice adds nothing — each annotation is matched to the block it already made.") },
  { id: "zotero", label: T("Zotero library (.zip)"), category: "Library", hint: T("Papers, collections, tags and notes"), strip: true, actionLabel: T("Choose .zip…") },
  { id: "markdown", label: T("Markdown notes"), category: "Notes", hint: T("Markdown files, Obsidian vaults or Notion exports"), actionLabel: T("Choose file…"),
    instructions: T("A single .md becomes a note page. A .zip of Markdown — a zipped Obsidian vault, Notion's Export → Markdown & CSV (subpages included), a Gamma Markdown export, or any zipped folder of notes — becomes one page per file: folders become folder labels, links between the notes ([[wikilinks]] included) become mentions, ![[block]] embeds synced blocks, tags labels, and images and files come along. Notes already imported are skipped.") },
  { id: "logseq", label: T("Logseq highlights"), category: "This paper", hint: T("A PDF and its .edn, with optional notes"), actionLabel: T("Choose files…"),
    instructions: T("Pick a Logseq .pdf and its .edn (a .md of notes is optional). The paper and its highlights land in your library as a new page.") },
  { id: "gamma", label: T("Gamma export (.zip)"), category: "Library", hint: T("Merge pages, files and chats from Gamma"), actionLabel: T("Choose .zip…"),
    instructions: T("A zip made by another Gamma's Export → Gamma format (a full backup works too). Its pages, files and chats merge into your library — nothing existing is touched, and re-importing the same zip adds nothing. (A single shared page needs no zip: paste its share link into the + menu.)") },
];

export function resolveImport(source, { hasPdf, strip } = {}) {
  const formats = IMPORT_SOURCES.filter(({ id }) => id !== "annots" || hasPdf);
  const definition = formats.find(({ id }) => id === source) || formats.find(({ id }) => id === "zotero");
  return { definition, formats, needsReview: Boolean(definition.strip),
    payload: definition.strip ? { source: definition.id, strip: Boolean(strip) } : { source: definition.id } };
}
