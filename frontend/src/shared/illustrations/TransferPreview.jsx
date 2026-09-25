import React from "react";
import { FileTextIcon, PaperclipIcon } from "../ui/Icons";
import { t } from "../../shared/i18n/i18n.js";

// Illustrative layouts, not a render of the user's document. These elements
// follow the same effective options sent to the import/export handlers.
function PaperLines() {
  return <div className="transferPaperLines" aria-hidden="true"><i /><i /><i /><i /></div>;
}

function HighlightSample() {
  return <blockquote className="transferQuote" data-preview="highlights">
    <small>{t("PAGE 3")}</small>
    <mark>{t("A useful passage from your paper.")}</mark>
  </blockquote>;
}

function NoteSample({ annotation = false }) {
  return <div className={`transferNote${annotation ? " transferMarginNote" : ""}`} data-preview="notes">
    <strong>{t("Your note")}</strong>
    <span>{t("A connection worth coming back to.")}</span>
  </div>;
}

function Paper({ original = false, highlights, notes }) {
  return <div className="transferPaper" data-preview={original ? "original" : undefined}>
    <div className="transferPaperTitle">{t("Patterns in nature")}</div>
    <div className="transferPaperByline">{t("A. Rivera · 2026")}</div>
    {original ? <><PaperLines /><div className="transferPaperFigure" aria-hidden="true"><i /><i /><i /></div></> : null}
    {highlights ? <HighlightSample /> : null}
    {notes ? <NoteSample annotation={original} /> : null}
    {original ? <PaperLines /> : null}
    <span className="transferPageNumber">1</span>
  </div>;
}

export function ExportPreview({ format, highlights, notes, bundle }) {
  const files = format !== "pdf" && format !== "notespdf";
  const metadataOnly = format === "zotero" && !bundle;
  return <figure className="transferPreview" aria-label={t("Export preview")}>
    <figcaption>{t("Example")} {format === "pdf" ? t("PDF page") : files ? "export" : t("page layout")}</figcaption>
    <Paper original={format === "pdf"} highlights={highlights} notes={notes} />
    {format === "gamma" ? <div className="transferPreviewExtra">{t("Metadata, page structure and AI chats included")}</div> : null}
    {files ? <div className="transferFiles" data-preview={bundle ? "bundled-files" : "linked-files"}>
      <PaperclipIcon size={14} />
      <div><strong>{bundle ? t("Files included") : metadataOnly ? t("PDF files not included") : t("Files stay as links")}</strong>
        <span>{bundle ? t("PDFs and images travel with the export") : metadataOnly ? t("Metadata, collections and notes only") : t("Links point back to this server")}</span></div>
    </div> : null}
  </figure>;
}

export function ImportPreview({ annotations, strip }) {
  return <figure className="transferPreview" aria-label={t("Import preview")}>
    <figcaption>{annotations ? t("PDF after import") : t("In your library")}</figcaption>
    {annotations ? <Paper original highlights={!strip} notes={false} /> : <div className="transferPaper">
      <div className="transferPaperTitle">{t("Your imported pages")}</div>
      <div className="transferPaperByline">{t("Ready to read and organize")}</div>
      <PaperLines /><HighlightSample /><NoteSample />
      <span className="transferPageNumber">1</span>
    </div>}
    {annotations ? <div className="transferFiles" data-preview="imported-annotations">
      <FileTextIcon size={15} /><div><strong>{t("Highlights and notes in Gamma")}</strong>
        <span>{strip ? t("Original annotations removed from the PDF") : t("Originals kept in the PDF, hidden in the viewer")}</span></div>
    </div> : null}
  </figure>;
}
