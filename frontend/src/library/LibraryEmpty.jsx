import { useRef } from "react";
import { t } from "../shared/i18n/i18n.js";
import { DownloadIcon, FilePlusIcon, LinkIcon, UploadIcon } from "../shared/ui/Icons.jsx";

// "Start your library": what a library with nothing of the user's in it yet
// (isFreshLibrary — no pages, or only the seeded Welcome page) shows above
// its listing. Four ways in, each an existing handler: a paper by link (the
// Add popover), uploads, a blank note page, the Import dialog; plus the first
// tour. It goes away with the first page of the user's own.
export default function LibraryEmpty({ onOpenLink, onUpload, onNewPage, onImport, onTour }) {
  const fileRef = useRef(null);
  const cards = [
    { id: "link", icon: LinkIcon, primary: true, onClick: onOpenLink,
      title: t("Open a paper from a link"),
      text: t("Paste an arXiv link, a DOI or any PDF address — Gamma finds the PDF.") },
    { id: "upload", icon: UploadIcon, onClick: () => fileRef.current?.click(),
      title: t("Upload PDFs"),
      text: t("Choose files, or drop them anywhere on this page.") },
    { id: "page", icon: FilePlusIcon, onClick: onNewPage,
      title: t("Write a note page"),
      text: t("A blank outline for ideas, meeting notes or a reading list.") },
    { id: "import", icon: DownloadIcon, onClick: onImport,
      title: t("Import a library"),
      text: t("From Zotero, Obsidian, Logseq, Notion or a Markdown folder.") },
  ];
  return (
    <section className="libEmpty" data-guide="home.empty" aria-labelledby="libEmptyTitle">
      <div className="libEmptyHead">
        <img className="libEmptyMark" src="/media/icons/favicon.svg" alt="" width="40" height="40" />
        <div>
          <h2 className="libEmptyTitle" id="libEmptyTitle">{t("Start your library")}</h2>
          <p className="libEmptyLead">{t("Add a paper to read, highlight and take notes on — or bring the library you already have.")}</p>
        </div>
      </div>
      <div className="libEmptyCards">
        {cards.map(({ id, icon: Icon, primary, onClick, title, text }) => (
          <button key={id} type="button" className={`libEmptyCard ${primary ? "primary" : ""}`} data-action={id} onClick={onClick}>
            <span className="libEmptyCardIcon"><Icon size={20} /></span>
            <span className="libEmptyCardBody">
              <span className="libEmptyCardTitle">{title}</span>
              <span className="libEmptyCardText">{text}</span>
            </span>
          </button>
        ))}
      </div>
      <input
        ref={fileRef}
        type="file"
        accept=".pdf,.md,.markdown,application/pdf,text/markdown"
        multiple
        hidden
        onChange={(e) => { const files = Array.from(e.target.files || []); e.target.value = ""; if (files.length) onUpload(files); }}
      />
      <div className="libEmptyFoot">
        {onTour ? (
          <button type="button" className="libEmptyTour" onClick={onTour}>
            {t("New here?")} <strong>{t("Take the 2-minute tour")}</strong>
          </button>
        ) : <span />}
        <span className="libEmptyTip">{t("Tip: drag PDFs from your desktop straight in")}</span>
      </div>
    </section>
  );
}
