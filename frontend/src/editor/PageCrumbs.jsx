// Where the open page is, on one line above the title, which follows the
// last separator. First the folders of its place (`folders`, [{id, name}]):
// a work folder's file's from the work folder down to its directory
// (explorer/workPaths.js fileCrumbs), any other filed page's (a sub-page's
// by its top) the path of one folder it is filed in (App's openCrumbs),
// each a crumb that opens that folder in the library. Then, for a sub-page,
// the pages above it, top first (`ancestors`, [{id, title}] from
// GET /pages/{id}/family), each a crumb that opens that page and takes a
// dragged page through what `targetProps(page)` gives it (its drop handlers
// and a `className` while a drag is over it). A page at the library's root
// has neither: nothing. App does the navigation. A folder crumb is no drop
// target.
import React from "react";
import { t } from "../shared/i18n/i18n.js";

export function PageCrumbs({ ancestors, onOpen, targetProps, folders = [], onOpenFolder }) {
  if (!ancestors?.length && !folders.length) return null;
  return (
    <nav className="pageCrumbs" aria-label={t("Where this page is")} data-guide={ancestors?.length ? "page.crumbs" : undefined}>
      {folders.map((folder) => (
        <React.Fragment key={folder.id}>
          <button type="button" className="crumbBtn crumbFolder" title={t("Open the folder “{name}”", { name: folder.name })}
            onClick={() => onOpenFolder?.(folder.id)}>
            {folder.name}
          </button>
          <span className="crumbSep" aria-hidden="true">/</span>
        </React.Fragment>
      ))}
      {(ancestors || []).map((page) => {
        const title = page.title || t("Untitled");
        const { className = "", ...props } = targetProps?.(page) || {};
        return (
          <React.Fragment key={page.id}>
            <button type="button" className={`crumbBtn ${className}`} title={t("Open the page \"{name}\"", { name: title })}
              onClick={() => onOpen(page.id)} {...props}>
              {title}
            </button>
            <span className="crumbSep" aria-hidden="true">/</span>
          </React.Fragment>
        );
      })}
    </nav>
  );
}
