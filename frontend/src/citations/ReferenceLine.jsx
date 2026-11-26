// The metadata popover's Reference (docs/dev/citations.md): under the
// table of fields, full width, the record as the default style cites it,
// in a CopyBox as the share popover's Reference, the style's short name on
// its label line. It renders the server's own CSL item of the saved record
// (the bibliography answer's `csl`, gamma/citations.py csl_item), the item
// every copy is made from, so it shows what a copy gives; an edit shows
// once it is saved. A page with a stored entry and no record shows its
// title as the style cites it (the server cites it by title alone); a page
// with neither shows none. Its copy is every citation copy's (useCitations
// copyPage).
import React, { useEffect, useState } from "react";
import { t } from "../shared/i18n/i18n.js";
import { CopyBox, useCopied } from "../shared/ui/Widgets";
import { CitationHtml } from "./CitationHtml";
import { preloadCitations, styleLabel, styleMeta } from "./render.js";

// `cite` is useCitations' answer; `pageId` the open page, `pageBibtex` its
// stored entry and `meta` its record as saved: a save changes them, and the
// page's entry is read again (useCitations drops the answers it kept).
export function ReferenceLine({ cite, pageId, pageBibtex, meta }) {
  const [shown, setShown] = useState(null); // {pageId, html}; the last one stays while the next renders
  const metaKey = meta ? JSON.stringify(meta) : "";
  useEffect(() => {
    if (!pageId || !(metaKey || pageBibtex)) { setShown(null); return undefined; }
    let live = true;
    preloadCitations();
    cite.pageEntry(pageId)
      .then((entry) => (entry?.csl ? cite.render({ items: [entry.csl] }) : null))
      .then((pair) => { if (live) setShown(pair?.text ? { pageId, html: pair.html } : null); },
        () => { if (live) setShown(null); });
    return () => { live = false; };
  }, [pageId, pageBibtex, metaKey, cite.style, cite.locale]);
  const [copied, flash] = useCopied();
  if (!shown || shown.pageId !== pageId) return null;
  const style = styleMeta(cite.style).title;
  return (
    <div className="metaRefBlock">
      <div className="citeHead">
        <span className="citeLabel">{t("Reference")}</span>
        <span className="citeSourceTag" title={style}>{styleLabel(cite.style)}</span>
      </div>
      <CopyBox copied={!!copied} onCopy={() => cite.copyPage(pageId, { mode: "bibliography" }, () => flash())}
        title={t("Copy the reference in {style}", { style })} label={t("Copy reference")}>
        <CitationHtml html={shown.html} className="citeRefPreview" />
      </CopyBox>
    </div>
  );
}
