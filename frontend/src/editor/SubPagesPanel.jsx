// "N pages inside": the open page's sub-pages (the pages whose parent_page
// is it), under its notes beside "Linked from", in library order. A row opens
// that page; "New page inside", when `onNew` is given, makes another. Nothing
// shows for a page with none: the page's menu makes the first. The collapsed
// state is a browser preference (subPagesCollapsed). App fetches the list
// (GET /pages/{id}/family's `sub_pages`: [{id, title, updated_at, format}],
// `format` its document's on one that carries one, so a share view, which
// has no library listing, draws the right glyph too) and does the
// navigation and the creation. `onMenu(page)`, when given, is the opener of
// a row's menu (a right-click, a held finger, its "⋯"), and `rowProps(page)`
// what App adds to a row: its drag and its drop, and a `className` while a
// drag is over it.
import React from "react";
import { FileGlyph, PlusIcon } from "../shared/ui/Icons";
import { MenuButton } from "../shared/ui/Menus";
import { Collapse } from "../shared/ui/MotionWidgets";
import { menuPress } from "../shared/ui/press.js";
import { t, tn } from "../shared/i18n/i18n.js";

export function SubPagesPanel({ pages, onOpen, onNew, onMenu, rowProps, collapsed, onCollapsedChange }) {
  if (!pages?.length) return null;
  return (
    <section className="subPagesPanel" data-motion-key="subpages">
      <button type="button" className="backlinksHead" aria-expanded={!collapsed}
        onClick={() => onCollapsedChange(!collapsed)}>
        <span className={`backlinksCaret turnCaret${collapsed ? "" : " open"}`} aria-hidden="true">▸</span>
        {tn("{n} page inside", "{n} pages inside", pages.length)}
      </button>
      <Collapse open={!collapsed}>
        {pages.map((page) => {
          const title = page.title || t("Untitled");
          const menu = onMenu?.(page);
          const { className = "", ...props } = rowProps?.(page) || {};
          return (
            <div key={page.id} className={`subPageRow${menu ? " rowMenuHost" : ""} ${className}`} data-page={page.id}
              {...menuPress(menu)} {...props}>
              <button type="button" className="backlinkPage subPageOpen"
                title={t("Open the page \"{name}\"", { name: title })} onClick={() => onOpen(page.id)}>
                <FileGlyph format={page.format} size={14} />
                <span className="backlinkPageTitle">{title}</span>
              </button>
              {menu ? <MenuButton className="subPageMenu" open={menu} /> : null}
            </div>
          );
        })}
        {onNew ? (
          <button type="button" className="backlinkPage subPageNew" onClick={() => onNew()}>
            <PlusIcon size={14} />
            <span className="backlinkPageTitle">{t("New page inside")}</span>
          </button>
        ) : null}
      </Collapse>
    </section>
  );
}
