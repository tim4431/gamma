// A page picked from the library in the palette (shared/ui/Palette.jsx), the
// look and keys of Ctrl+P: Move to page (a block, or an image, table or
// diagram), Move into page (pages), and the PDF's link dialog
// (pdf/LinkDialog.jsx). Its page rows are quick open's — rankLibraryPages
// drawn by PageOption: recently viewed, open tabs, then the rest by last
// edit, a query ranking them as Ctrl+P does, and a sub-page's row naming the
// pages it is in — so a sub-page is offered like any page. `pages` is the
// whole listing, `exclude` (a Set of ids) the pages not offered. `first` (page
// ids) leads the list no query has narrowed, under `firstTitle`: a block's
// move puts the open page's own sub-pages there.
//
// `actions(query)` are rows before the pages, each {key, section, icon,
// title, detail?, tag?, run}, under `sections[section]` (the link dialog's
// "Link to …" for a typed DOI). A pick runs an action's `run` or calls
// `onPick(page)`; the caller closes the picker. `label` names the dialog,
// `placeholder` the field, which opens holding `initialQuery`; `note` says
// what a pick does, beside the keys.
import React, { useMemo, useState } from "react";
import { SearchIcon } from "../shared/ui/Icons";
import Palette from "../shared/ui/Palette";
import PageOption, { recencySections } from "./PageOption";
import { rankLibraryPages } from "./librarySearch";
import { t } from "../shared/i18n/i18n.js";

const MAX_ROWS = 40;
const NO_ACTIONS = () => [];

export default function PagePicker({
  label, placeholder = label, initialQuery = "", note, hint, pages, exclude, tree, recentViews, openTabs,
  first, firstTitle, actions = NO_ACTIONS, sections, onPick, onClose,
}) {
  const [query, setQuery] = useState(initialQuery);
  const q = query.trim();
  const rows = useMemo(() => [
    ...actions(q),
    ...rankLibraryPages(pages, tree, q, { recentViews, openTabs, first, exclude, limit: MAX_ROWS })
      .map((r) => ({ key: r.page.id, ...r })),
  ], [actions, q, pages, exclude, tree, recentViews, openTabs, first]);
  const sectionTitle = { ...recencySections(), first: firstTitle, pages: t("Pages"), ...sections };
  const rowOf = (r, props) => {
    if (r.page) return <PageOption {...props} row={r} tree={tree} query={q} />;
    const Icon = r.icon;
    return (
      <button {...props} data-action={r.key} title={[r.title, r.detail].filter(Boolean).join("\n")}>
        <Icon size={16} />
        <span><strong>{r.title}</strong>{r.detail ? <small>{r.detail}</small> : null}</span>
        {r.tag ? <em className="quickOpenTag">{r.tag}</em> : null}
      </button>
    );
  };
  return (
    <Palette
      label={label}
      listLabel={t("Pages")}
      icon={<SearchIcon size={16} />}
      placeholder={placeholder}
      query={query}
      onQuery={setQuery}
      rows={rows}
      sectionTitle={sectionTitle}
      renderRow={rowOf}
      onChoose={(r) => (r.page ? onPick(r.page) : r.run())}
      onClose={onClose}
      empty={pages.some((p) => !exclude?.has(p.id)) ? t("No matching pages.") : t("No other pages yet.")}
      note={note}
      hint={hint}
    />
  );
}
