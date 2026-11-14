// A library page as an option of a picker's list: Ctrl+P (QuickOpen.jsx) and
// the chat's @ picker (chat/PaperMentionInput.jsx), over rankLibraryPages'
// rows. The kind glyph, the title with the query's matches marked (exact
// matches only; a typo-only match stays unmarked), under it the pages a
// sub-page is in ("Top › Parent", the row's `path`) then authors and year, a
// file row's folder/label chips (a sub-page's labels only: its path is
// where it is, and folders it carries count for nothing), then a check
// when `checked`, "Current" on the open page, or else the row's time. A
// work folder's file is a FileOption. The picker passes the option button's
// own props (selection, hover, click).
import React from "react";
import { CheckIcon, FileGlyph } from "../shared/ui/Icons";
import { MarkedText } from "../search/SearchPanel";
import { CardLabels } from "./FileBrowser";
import { filingChips, formatRelativeTime } from "./libraryUtils";
import { workFolderOf } from "../documents/library.js";
import { pageDocument } from "../documents/pageDocument.js";
import { fileParts, placeParts } from "../explorer/fileSearch.js";
import { filePlace } from "../explorer/workPaths.js";
import { t } from "../shared/i18n/i18n.js";

// The headings of the list no query has narrowed, by a row's `section`.
export const recencySections = () => ({ recent: t("Recent"), tabs: t("Open tabs"), rest: t("Everything else") });

// What ends an option: a check when `checked`, "Current" on the open page,
// or else the row's time.
function OptionEnd({ checked, current, time }) {
  return checked ? <CheckIcon size={14} />
    : current ? <em className="quickOpenTag">{t("Current")}</em>
    : time ? <em className="quickOpenTime">{formatRelativeTime(time)}</em> : null;
}

// A work folder's file as an option, one row whatever lists it (a recent, an
// open tab, a title hit, a file GET /api/files found): the kind glyph, the
// file's name with the query's matches marked, under it its place
// (explorer/workPaths.js filePlace, "W / a / b"), its library folders and
// labels (filingChips: its directory is its place, no chip), then the end.
// A found file (`match`) is marked at the server's spans, in its name and
// its directory.
export function FileOption({ page, tree, query, time, currentPageId, checked, ...props }) {
  const place = filePlace(tree, tree, page);
  const name = place.name || t("Untitled");
  const found = page.match;
  const chips = filingChips(tree, page.properties);
  return (
    <button title={[name, place.text].filter(Boolean).join("\n")} data-kind="file" {...props}>
      <FileGlyph format={pageDocument(page)?.format} size={16} />
      <span>
        <strong>{found ? <MarkedText parts={fileParts(name, found.name)} /> : <MarkedText text={name} query={query} />}</strong>
        {place.text ? (
          <small className="quickOpenPath">
            {found ? <MarkedText parts={placeParts(place.work, page.dir, found.path)} /> : place.text}
          </small>
        ) : null}
      </span>
      <CardLabels className="fileRowLabels" folders={chips.folders} labels={chips.labels} />
      <OptionEnd checked={checked} current={page.id === currentPageId} time={time} />
    </button>
  );
}

export default function PageOption({ row: { page, time, path }, tree, query, currentPageId, checked, ...props }) {
  if (workFolderOf(page)) {
    return <FileOption {...props} page={page} tree={tree} query={query} time={time} currentPageId={currentPageId} checked={checked} />;
  }
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
      <OptionEnd checked={checked} current={page.id === currentPageId} time={time} />
    </button>
  );
}
