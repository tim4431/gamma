// "Linked from N pages": the blocks elsewhere that [[link]] to the open page,
// under its notes, grouped by page. Each entry is the page title and the
// linking block rendered as a short snippet (its [[ref]]s as chips); a click
// opens that page and scrolls to the block. The collapsed state is a browser
// preference (backlinksCollapsed). App fetches the list (/blocks/{id}/backlinks)
// and does the navigation.
import React, { useEffect, useMemo } from "react";
import { BlockMarkdown } from "./BlockTree";
import { FileGlyph } from "../shared/ui/Icons";
import { t, tn } from "../shared/i18n/i18n.js";

const REF_RE = /\[\[([a-zA-Z0-9_-]+)\]\]/g;

export function BacklinksPanel({ backlinks, pageId, pageTitle, pages, refCache, onFetchRefs, collapsed, onCollapsedChange, onOpen }) {
  // Grouped by page, in the server's order (most recently edited first).
  const groups = useMemo(() => {
    const byPage = new Map();
    for (const bl of backlinks) {
      const id = bl.page_root_id || bl.id;
      if (!byPage.has(id)) byPage.set(id, { id, title: bl.page_title || t("Untitled"), items: [] });
      byPage.get(id).items.push(bl);
    }
    return [...byPage.values()];
  }, [backlinks]);

  // Chip labels: the open page is the one they link to; any other [[ref]]
  // in a snippet resolves through App's ref cache (fetched once here).
  const refLabels = useMemo(() => {
    const out = {};
    for (const bl of backlinks) {
      for (const [, id] of (bl.content || "").matchAll(REF_RE)) {
        if (id === pageId) out[id] = { content: pageTitle };
        else if (refCache?.[id]) out[id] = { content: refCache[id].content, page_title: refCache[id].page_title };
      }
    }
    return out;
  }, [backlinks, pageId, pageTitle, refCache]);
  useEffect(() => {
    const unknown = new Set();
    for (const bl of backlinks) {
      for (const [, id] of (bl.content || "").matchAll(REF_RE)) if (id !== pageId && !refCache?.[id]) unknown.add(id);
    }
    if (unknown.size) onFetchRefs?.([...unknown]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backlinks, pageId]);

  if (!groups.length) return null;
  const open = (bl) => onOpen(bl);
  const keyOpen = (bl) => (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); open(bl); }
  };
  return (
    <section className="backlinksPanel">
      <button type="button" className="backlinksHead" aria-expanded={!collapsed}
        onClick={() => onCollapsedChange(!collapsed)}>
        <span className="backlinksCaret" aria-hidden="true">{collapsed ? "▸" : "▾"}</span>
        {tn("Linked from {n} page", "Linked from {n} pages", groups.length)}
      </button>
      {collapsed ? null : groups.map((g) => {
        const isPdf = !!pages?.find((p) => p.id === g.id)?._attachment;
        return (
          <div key={g.id} className="backlinkGroup">
            <div className="backlinkPage" role="button" tabIndex={0} title={t("Open the page \"{name}\"", { name: g.title })}
              onClick={() => open(g.items[0])} onKeyDown={keyOpen(g.items[0])}>
              <FileGlyph isPdf={isPdf} size={14} />
              <span className="backlinkPageTitle">{g.title}</span>
            </div>
            {g.items.map((bl) => (
              <div key={bl.id} className="backlinkItem" role="button" tabIndex={0}
                title={t("Open the page on this block")}
                onClick={() => open(bl)} onKeyDown={keyOpen(bl)}>
                <div className="blockRendered backlinkSnippet">
                  {(bl.content || "").trim()
                    ? <BlockMarkdown content={bl.content} blockId={`backlink:${bl.id}`} refLabels={refLabels} onBlockRefClick={() => open(bl)} />
                    : <span className="blockPlaceholder">{t("(empty)")}</span>}
                </div>
              </div>
            ))}
          </div>
        );
      })}
    </section>
  );
}
