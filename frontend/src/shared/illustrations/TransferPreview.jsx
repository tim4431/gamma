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

// `card`: a card as the annotated PDF prints a highlight's notes
// (backend/gamma/pdf_cards.py), in the column beside the page or on it.
function NoteSample({ card = false }) {
  return <div className={`transferNote${card ? " transferNoteCard" : ""}`} data-preview="notes">
    <strong>{t("Your note")}</strong>
    <span>{t("A connection worth coming back to.")}</span>
  </div>;
}

// A comment thread's card, as the viewer's margin draws one.
function CommentSample() {
  return <div className="transferCommentCard" data-preview="comments">
    <div className="transferCommentHead"><span className="transferAvatar" aria-hidden="true">A</span>
      <strong>{t("Alex")}</strong></div>
    <span>{t("Same effect as in the figure?")}</span>
  </div>;
}

// The original paper with cards beside it stands beside a column: the
// column goes just before the highlight, so its cards stand level with it.
// Notes on the page are a card in the paper itself.
function Paper({ original = false, highlights, notes, comments = false, notesAt = "beside" }) {
  const notesBeside = original && notes && notesAt !== "page";
  const beside = notesBeside || (original && comments);
  const paper = <div className="transferPaper" data-preview={original ? "original" : undefined}>
    <div className="transferPaperTitle">{t("Patterns in nature")}</div>
    <div className="transferPaperByline">{t("A. Rivera · 2026")}</div>
    {original ? <><PaperLines /><div className="transferPaperFigure" aria-hidden="true"><i /><i /><i /></div></> : null}
    {beside ? <div className="transferMargin">
      {notesBeside ? <NoteSample card /> : null}
      {comments ? <CommentSample /> : null}
    </div> : null}
    {highlights ? <HighlightSample /> : null}
    {notes && !notesBeside ? <NoteSample card={original} /> : null}
    {original ? <PaperLines /> : null}
    <span className="transferPageNumber">1</span>
  </div>;
  return beside ? <div className="transferSpread">{paper}</div> : paper;
}

export function ExportPreview({ format, highlights, notes, bundle, comments, notesAt }) {
  const files = format !== "pdf" && format !== "notespdf";
  const metadataOnly = format === "zotero" && !bundle;
  return <figure className="transferPreview" aria-label={t("Export preview")}>
    <figcaption>{t("Example")} {format === "pdf" ? t("PDF page") : files ? "export" : t("page layout")}</figcaption>
    <Paper original={format === "pdf"} highlights={highlights} notes={notes} comments={comments} notesAt={notesAt} />
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
      <FileTextIcon size={16} /><div><strong>{t("Highlights and notes in Gamma")}</strong>
        <span>{strip ? t("Original annotations removed from the PDF") : t("Originals kept in the PDF, hidden in the viewer")}</span></div>
    </div> : null}
  </figure>;
}
