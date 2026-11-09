// Where an open sub-page is: the pages above it, top first, each a crumb
// that opens that page, on one line above the title, which follows the last
// separator. Nothing for a library page. App reads `ancestors` ([{id,
// title}]) from GET /pages/{id}/family and does the navigation; a crumb is
// a page a dragged page can be dropped into, through what `targetProps(page)`
// gives it (its drop handlers and a `className` while a drag is over it).
import React from "react";
import { t } from "../shared/i18n/i18n.js";

export function PageCrumbs({ ancestors, onOpen, targetProps }) {
  if (!ancestors?.length) return null;
  return (
    <nav className="pageCrumbs" aria-label={t("Pages this page is in")}>
      {ancestors.map((page) => {
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
