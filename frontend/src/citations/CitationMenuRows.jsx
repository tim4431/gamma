// The citation rows of the page menu (a page, the library's selection, a
// Pages inside row, a Files window row) and of a folder's menu
// (docs/dev/citations.md, ui-design.md "Menus and submenus"): Copy
// citation, in the text, in the default style (only while Quick Copy is the
// style, as Zotero shows its Copy as … then; never for a folder), Copy
// bibliography under the Quick Copy format's label, and Copy as ▸ with
// every format and style in labelled groups (formats.js copyAsGroups): the file
// formats, the key commands, your styles (the default, marked so, and the
// added ones), then the other bundled styles. A folder's copy every paper
// filed in it.
//
// App renders one as a group of its own, and only where an acted-on page
// has a record (cite.hasRecord). Opening the menu fetches the engine and
// the answer the rows copy from (a folder's waits for the click).
import React, { useEffect } from "react";
import { t } from "../shared/i18n/i18n.js";
import { BookIcon, ClipboardIcon, QuoteMarkIcon } from "../shared/ui/Icons";
import { MenuGroup, MenuItem, SubMenuItem } from "../shared/ui/Menus";
import { copyAsRows } from "./citeRules.js";
import { copyAsGroups, formatLabel, quickCopyLabel } from "./formats.js";
import { preloadCitations, preloadStyle } from "./render.js";

// `cite` is useCitations' answer; `ids` the acted-on pages, or `folder` a
// folder's id; `keyFolder` the folder the library shows, whose keys a copy
// takes; `keyLabel(id)` the chord a command answers to, shown where the
// rows act on what the commands act on (the library's selection); `onClose`
// closes the menu.
export function CitationMenuRows({ cite, ids = null, folder = "", keyFolder = "", keyLabel = null, onClose }) {
  // Once, as the menu opens on these pages.
  useEffect(() => {
    if (folder) preloadCitations();
    else cite.prefetch(ids, keyFolder);
  }, []);
  const copy = (as) => {
    onClose();
    if (folder) cite.copyFolder(folder, as);
    else cite.copyPages(ids, as, keyFolder);
  };
  const { mine, others } = copyAsRows({ style: cite.style, added: cite.added });
  const styleRow = (style) => (
    <MenuItem key={style.id} title={style.title} onPointerEnter={() => preloadStyle(style.id)}
      trailing={style.id === cite.style ? <span className="ctxMenuHint">{t("default")}</span> : null}
      onClick={() => copy(`style:${style.id}`)}>{style.title}</MenuItem>
  );
  return (
    <>
      {cite.quickCopy === "style" && !folder ? (
        <MenuItem icon={QuoteMarkIcon} keys={keyLabel?.("app.copyCitation")}
          title={t("The citation in the text, in your citation style")} onPointerEnter={preloadCitations}
          onClick={() => copy("citation")}>{t("Copy citation")}</MenuItem>
      ) : null}
      <MenuItem icon={BookIcon} keys={keyLabel?.("app.copyBibliography")} onPointerEnter={preloadCitations}
        title={folder ? t("Every paper filed in this folder, in the format of Settings → Citations › Quick Copy") : undefined}
        onClick={() => copy("bibliography")}>{quickCopyLabel(cite.quickCopy)}</MenuItem>
      <SubMenuItem id="copyas" icon={ClipboardIcon} label={t("Copy as")}>
        {copyAsGroups().map((group) => (
          <MenuGroup key={group.label} label={t(group.label)}>
            {group.formats.map((format) => <MenuItem key={format} onClick={() => copy(format)}>{formatLabel(format)}</MenuItem>)}
          </MenuGroup>
        ))}
        <MenuGroup label={t("Your styles")}>{mine.map(styleRow)}</MenuGroup>
        {others.length ? <MenuGroup label={t("Other styles")}>{others.map(styleRow)}</MenuGroup> : null}
      </SubMenuItem>
    </>
  );
}
