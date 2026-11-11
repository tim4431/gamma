// A library page as an option of a picker's list: Ctrl+P (QuickOpen.jsx) and
// the chat's @ picker (chat/PaperMentionInput.jsx), over rankLibraryPages'
// rows. The kind glyph, the title with the query's matches marked (exact
// matches only; a typo-only match stays unmarked), under it the pages a
// sub-page is in ("Top › Parent", the row's `path`) then authors and year, a
// file row's folder/label chips (a sub-page's labels only: its path is
// where it is, and folders it carries count for nothing), then a check
// when `checked`, "Current" on the open page, or else the row's time. The
// picker passes the option button's own props (selection, hover, click).
import React from "react";
import { CheckIcon, FileGlyph } from "../shared/ui/Icons";
import { MarkedText } from "../search/SearchPanel";
import { CardLabels } from "./FileBrowser";
import { filingChips, formatRelativeTime } from "./libraryUtils";
import { pageDocument } from "../documents/pageDocument.js";
import { t } from "../shared/i18n/i18n.js";

// The headings of the list no query has narrowed, by a row's `section`.
export const recencySections = () => ({ recent: t("Recent"), tabs: t("Open tabs"), rest: t("Everything else") });

export default function PageOption({ row: { page, time, path }, tree, query, currentPageId, checked, ...props }) {
  const meta = page.properties?.meta || {};
  const authors = (meta.authors || []).slice(0, 2).join(", ");
  const detail = [path, authors, meta.year].filter(Boolean).join(" · ");
  const title = page.content || t("Untitled");
  const chips = filingChips(tree, page.properties);
  return (
    <button title={[title, detail].filter(Boolean).join("\n")} data-kind="page" {...props}>
      <FileGlyph format={pageDocument(page)?.format} size={16} />
      <span><strong><MarkedText text={title} query={query} /></strong>{detail && <small>{detail}</small>}</span>
      <CardLabels className="fileRowLabels" folders={path ? [] : chips.folders} labels={chips.labels} />
      {checked ? <CheckIcon size={14} />
        : page.id === currentPageId ? <em className="quickOpenTag">{t("Current")}</em>
        : time ? <em className="quickOpenTime">{formatRelativeTime(time)}</em> : null}
    </button>
  );
}
