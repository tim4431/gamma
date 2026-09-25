// Quick open (Ctrl+P): a VS Code-style palette over the library's pages.
// Empty query lists the recently viewed pages, then the open tabs, then the
// rest by last edit; typing ranks pages through createLibraryMatcher — the
// home listing's search: typo-tolerant, on the title or the folder/label
// chips, title hits first. ↑↓ moves, Enter opens, Esc closes. Rows reuse the
// chat mention picker's option style and the home file rows' folder/label chips.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { BookIcon, FileTextIcon, SearchIcon } from "../shared/ui/Icons";
import { CardLabels } from "./FileBrowser";
import { createLibraryMatcher } from "./librarySearch";
import { pageAttachment, parseFolderTags } from "./libraryUtils";
import { t } from "../shared/i18n/i18n.js";

const MAX_ROWS = 40;

export default function QuickOpen({ open, onClose, pages, recentViews, openTabs, currentPageId, onOpen }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => { if (open) { setQuery(""); setActive(0); } }, [open]);
  // Modal focus: autoFocus only runs at mount, and a page that finishes
  // loading right after Ctrl+P may focus its editor — the keystrokes meant
  // for the palette would land in the page. Pull focus back while open.
  useEffect(() => {
    if (!open) return undefined;
    const hold = (e) => {
      const input = inputRef.current;
      if (input && !input.closest('[role="dialog"]')?.contains(e.target)) input.focus();
    };
    document.addEventListener("focusin", hold, true);
    return () => document.removeEventListener("focusin", hold, true);
  }, [open]);
  useEffect(() => { setActive(0); }, [query]);
  useEffect(() => {
    listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const results = useMemo(() => {
    if (!open) return [];
    const recentRank = new Map(recentViews.map((r, i) => [r.id, i]));
    const tabs = new Set(openTabs.map((t) => t.id));
    const match = createLibraryMatcher(query);
    const score = (p) => match(p.content, [
      ...parseFolderTags(p.properties?.folder), ...parseFolderTags(p.properties?.category),
    ]);
    const rankOf = (p) => (recentRank.has(p.id) ? recentRank.get(p.id) : tabs.has(p.id) ? 1000 : 2000);
    return pages
      .map((p) => ({ page: p, score: match ? score(p) : 1, rank: rankOf(p) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || (a.rank - b.rank)
        || (b.page.updated_at || "").localeCompare(a.page.updated_at || ""))
      .slice(0, MAX_ROWS)
      .map((r) => ({
        page: r.page,
        tag: r.page.id === currentPageId ? "Current" : recentRank.has(r.page.id) ? "Recent" : tabs.has(r.page.id) ? "Open" : "",
      }));
  }, [open, pages, recentViews, openTabs, query, currentPageId]);

  if (!open) return null;
  const choose = (page) => { if (page) { onClose(); onOpen(page.id); } };
  return (
    <div className="reportOverlay quickOpenOverlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="reportModal quickOpen" role="dialog" aria-label={t("Open a page")}>
        <div className="quickOpenInput">
          <SearchIcon size={15} />
          <input
            ref={inputRef}
            autoFocus
            className="searchInput"
            placeholder={t("Search pages by title or label")}
            aria-label={t("Search pages by title or label")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + results.length) % (results.length || 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(results[active]?.page);
              } else if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
          />
        </div>
        <div ref={listRef} className="quickOpenList" role="listbox" aria-label={t("Pages")}>
          {results.map(({ page, tag }, i) => {
            const meta = page.properties?.meta || {};
            const authors = (meta.authors || []).slice(0, 2).join(", ");
            const detail = [authors, meta.year].filter(Boolean).join(" · ");
            const folders = parseFolderTags(page.properties?.folder);
            const labels = parseFolderTags(page.properties?.category);
            const title = page.content || "Untitled";
            return (
              <button
                type="button" role="option" key={page.id} tabIndex={-1}
                aria-selected={i === active}
                className={`slashMenuItem chatMentionOption quickOpenRow${i === active ? " selected" : ""}`}
                title={[title, detail].filter(Boolean).join("\n")}
                onPointerDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(page)}
              >
                {pageAttachment(page) ? <BookIcon size={15} /> : <FileTextIcon size={15} />}
                <span><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
                <CardLabels className="fileRowLabels" folders={folders} labels={labels} />
                {tag && <em className="quickOpenTag">{tag}</em>}
              </button>
            );
          })}
          {!results.length && <div className="popoverHint">{pages.length ? "No matching pages." : "No pages yet."}</div>}
        </div>
        <div className="chatMentionHint">{t("↑↓ choose · Enter open · Esc close")}</div>
      </div>
    </div>
  );
}
