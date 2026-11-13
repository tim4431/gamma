// Where the open page is, on one line above the title, which follows the
// last separator. A work folder's file: its folders from the work folder
// down to its directory (`folders`, [{id, name}] from explorer/workPaths.js
// fileCrumbs), each a crumb that opens that folder in the library. A
// sub-page: the pages above it, top first (`ancestors`, [{id, title}] from
// GET /pages/{id}/family), each a crumb that opens that page and takes a
// dragged page through what `targetProps(page)` gives it (its drop handlers
// and a `className` while a drag is over it). The two never mix — a work
// folder holds no sub-pages — and a library page has neither: nothing.
// App does the navigation. A folder crumb is no drop target: a file is not
// refiled by a drop.
import React from "react";
import { t } from "../shared/i18n/i18n.js";

export function PageCrumbs({ ancestors, onOpen, targetProps, folders = [], onOpenFolder }) {
  if (!ancestors?.length && !folders.length) return null;
  return (
    <nav className="pageCrumbs" aria-label={t("Where this page is")}>
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
