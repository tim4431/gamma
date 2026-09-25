// Quick open (Ctrl+P): a VS Code-style palette over the library's pages.
// Empty query lists the recently viewed pages, then the open tabs, then the
// rest by last edit; typing ranks pages through createLibraryMatcher — the
// home listing's search: typo-tolerant, on the title or the folder/label
// chips, title hits first. ↑↓ moves, Enter opens, Esc closes. Rows reuse the
// chat mention picker's option style and the home file rows' folder/label chips.
//
// A query starting with ">" is the command palette (Ctrl+Shift+P opens it
// with the prefix typed): `commands()` — App's list of what applies right
// now, each {id, label, group, keyLabel, run} (docs/dev/hotkeys.md) —
// filtered by the same matcher on the label and group, Enter runs the pick.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { BookIcon, FileTextIcon, SearchIcon, TerminalIcon } from "../shared/ui/Icons";
import { commandIcon } from "../app/commandIcons.jsx";
import { CardLabels } from "./FileBrowser";
import { createLibraryMatcher } from "./librarySearch";
import { pageAttachment, parseFolderTags } from "./libraryUtils";
import { t } from "../shared/i18n/i18n.js";

const MAX_ROWS = 40;
const COMMAND_PREFIX = ">";

export default function QuickOpen({ open, prefix = "", commands, onClose, pages, recentViews, openTabs, currentPageId, onOpen }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const commandMode = query.startsWith(COMMAND_PREFIX);

  useEffect(() => { if (open) { setQuery(prefix); setActive(0); } }, [open, prefix]);
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

  // The commands that apply are read once per opening of the mode: App
  // builds the list from its current state.
  const commandList = useMemo(() => (open && commandMode && commands ? commands() : []), [open, commandMode, commands]);

  const results = useMemo(() => {
    if (!open) return [];
    if (commandMode) {
      const match = createLibraryMatcher(query.slice(1));
      return commandList
        .map((cmd) => ({ cmd, score: match ? match(cmd.label, [cmd.group]) : 1 }))
        .filter((r) => r.score > 0)
        .sort((a, b) => b.score - a.score)
        .slice(0, MAX_ROWS)
        .map((r) => ({ key: r.cmd.id, cmd: r.cmd }));
    }
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
        key: r.page.id,
        page: r.page,
        tag: r.page.id === currentPageId ? t("Current") : recentRank.has(r.page.id) ? t("Recent") : tabs.has(r.page.id) ? t("Open") : "",
      }));
  }, [open, commandMode, commandList, pages, recentViews, openTabs, query, currentPageId]);

  if (!open) return null;
  const choose = (r) => {
    if (!r) return;
    onClose();
    // A command runs once the palette is gone: with the input still focused,
    // a command that looks at the focus (rename, undo) would decline, and
    // the focus hold above would pull focus back from what it opens.
    if (r.cmd) setTimeout(r.cmd.run, 0);
    else onOpen(r.page.id);
  };
  const placeholder = commandMode ? t("Type a command") : t("Search pages by title or label");
  return (
    <div className="reportOverlay quickOpenOverlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="reportModal quickOpen" role="dialog" aria-label={commandMode ? t("Command palette") : t("Open a page")}>
        <div className="quickOpenInput">
          {commandMode ? <TerminalIcon size={15} /> : <SearchIcon size={15} />}
          <input
            ref={inputRef}
            autoFocus
            className="searchInput"
            placeholder={placeholder}
            aria-label={placeholder}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.nativeEvent.isComposing || e.keyCode === 229) return;
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + results.length) % (results.length || 1));
              } else if (e.key === "Enter") {
                e.preventDefault();
                choose(results[active]);
              } else if (e.key === "Escape") {
                e.preventDefault();
                onClose();
              }
            }}
          />
        </div>
        <div ref={listRef} className="quickOpenList" role="listbox" aria-label={commandMode ? t("Commands") : t("Pages")}>
          {results.map((r, i) => {
            if (r.cmd) {
              const CmdIcon = commandIcon(r.cmd);
              return (
                <button
                  type="button" role="option" key={r.key} tabIndex={-1}
                  aria-selected={i === active}
                  className={`slashMenuItem chatMentionOption quickOpenRow${i === active ? " selected" : ""}`}
                  title={r.cmd.label}
                  onPointerDown={(e) => e.preventDefault()}
                  onMouseEnter={() => setActive(i)}
                  onClick={() => choose(r)}
                >
                  <CmdIcon size={15} />
                  <span><strong>{r.cmd.label}</strong><small>{r.cmd.group}</small></span>
                  {r.cmd.keyLabel && <em className="quickOpenTag quickOpenKey">{r.cmd.keyLabel}</em>}
                </button>
              );
            }
            const { page, tag } = r;
            const meta = page.properties?.meta || {};
            const authors = (meta.authors || []).slice(0, 2).join(", ");
            const detail = [authors, meta.year].filter(Boolean).join(" · ");
            const folders = parseFolderTags(page.properties?.folder);
            const labels = parseFolderTags(page.properties?.category);
            const title = page.content || t("Untitled");
            return (
              <button
                type="button" role="option" key={r.key} tabIndex={-1}
                aria-selected={i === active}
                className={`slashMenuItem chatMentionOption quickOpenRow${i === active ? " selected" : ""}`}
                title={[title, detail].filter(Boolean).join("\n")}
                onPointerDown={(e) => e.preventDefault()}
                onMouseEnter={() => setActive(i)}
                onClick={() => choose(r)}
              >
                {pageAttachment(page) ? <BookIcon size={15} /> : <FileTextIcon size={15} />}
                <span><strong>{title}</strong>{detail && <small>{detail}</small>}</span>
                <CardLabels className="fileRowLabels" folders={folders} labels={labels} />
                {tag && <em className="quickOpenTag">{tag}</em>}
              </button>
            );
          })}
          {!results.length && (
            <div className="popoverHint">
              {commandMode ? t("No matching commands.") : pages.length ? t("No matching pages.") : t("No pages yet.")}
            </div>
          )}
        </div>
        <div className="chatMentionHint">
          {commandMode ? t("↑↓ choose · Enter run · Esc close") : t("↑↓ choose · Enter open · Esc close · > commands")}
        </div>
      </div>
    </div>
  );
}
