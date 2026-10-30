// What a page of a Zotero-linked workspace shows of the link
// (docs/dev/zotero_sync.md): the card of a page whose PDF Zotero holds and
// Gamma not yet (its `zotero_pdf` property: the size, whether it is on its
// way, "Download now", or why it cannot come), in the attach prompt's
// place while something brings it and above the prompt when nothing can
// (syncLinks.js zoteroPdfCard), and the badge on a block Gamma will not
// write back to Zotero (`zotero_readonly`: a note Gamma cannot rebuild,
// another group member's annotation; on a note page's root, the whole
// page), whose editor warns once before the first edit.
import React from "react";
import { fmtBytes } from "../shared/lib/utils";
import { t } from "../shared/i18n/i18n.js";
import { BookIcon, CloudDownloadIcon } from "../shared/ui/Icons";
import { zoteroPdfCard } from "./syncLinks.js";
import "./zoteroLink.css";

// The card, for the attach popover. `source`: what brings the PDF
// (syncLinks.js zoteroPdfSource: only a live link downloads); `ownCopy`:
// the attach prompt follows, for a PDF that cannot come; `onFetch()` asks
// for the PDF first.
export function ZoteroPdfCard({ pdf, source, ownCopy = false, onFetch }) {
  const [asked, setAsked] = React.useState(false);
  // a download asked for that failed (the mark says so) may be asked for again
  const failedNow = pdf?.state === "unavailable";
  React.useEffect(() => { if (failedNow) setAsked(false); }, [failedNow, pdf?.reason]);
  const card = zoteroPdfCard(pdf, { source, asked });
  if (!card) return null;
  const size = pdf.size ? fmtBytes(pdf.size) : "";
  return (
    <div className="zoteroPdfCard">
      <div className="popoverTitle">{size ? t("PDF in Zotero · {size}", { size }) : t("PDF in Zotero")}</div>
      {pdf.filename ? <div className="popoverHint zoteroPdfName" title={pdf.filename}><BookIcon size={14} /> {pdf.filename}</div> : null}
      <div className="popoverHint">
        {card.coming ? <span className="zoteroPdfState"><span className="pillSpin" aria-hidden="true" />{card.hint}</span> : card.hint}
      </div>
      {card.download ? (
        <button type="button" className="uiBtn sm primary" onClick={() => { setAsked(true); onFetch?.(); }}>
          <CloudDownloadIcon size={14} /> {t("Download now")}
        </button>
      ) : null}
      {card.takesOver || ownCopy ? <div className="popoverHint">{card.note}</div> : null}
    </div>
  );
}

// The words of a read-only block: its badge and its editor's warning.
export function zoteroReadonlyWords(props) {
  if (props?.zotero_readonly === "locked") {
    const author = props.zotero_author || t("Another member");
    return { badge: t("{author}'s annotation: read-only in Zotero", { author }),
      warning: t("{author}'s annotation is read-only in Zotero: what you change here stays in Gamma.", { author }) };
  }
  return { badge: t("Zotero note with citations: edits stay in Gamma"),
    warning: t("This Zotero note has citations or formatting Gamma cannot write back: what you change here stays in Gamma.") };
}

// The badge on the row's corner (beside a merge chip, when one is there),
// or, `onPage`, among the header buttons of a note page that is the note.
export function ZoteroReadonlyBadge({ props, besideChip = false, onPage = false }) {
  const { badge } = zoteroReadonlyWords(props);
  return (
    <span className={`zoteroReadonly${besideChip ? " besideChip" : ""}${onPage ? " onPage" : ""}`} title={badge} aria-label={badge} role="img">
      <BookIcon size={12} />
    </span>
  );
}

// The read-only regions whose editor has warned this session, by root id.
const warned = new Set();
// Whether the editor opening on a block of region `root` should warn now
// (once per region and session).
export function warnOnce(root) {
  if (!root || warned.has(root)) return false;
  warned.add(root);
  return true;
}
