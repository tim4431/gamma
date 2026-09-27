// Quick open (Ctrl+P): a VS Code-style palette over the library. Empty
// query lists the recently viewed pages, then the open tabs, then the rest
// by last edit, each group under its heading with its time on the right.
// Typing ranks pages, folders and labels through createLibraryMatcher — the
// home listing's search: typo-tolerant, on the title or the folder/label
// chips, title hits first — in sections of their own with the matched
// characters marked (exact matches only; a typo-only match stays
// unmarked), and ends with two actions: search inside notes and
// PDFs for the query (Ctrl+Enter; the workspace search opens with it) and
// create a page titled with it (Shift+Enter). ↑↓ moves, Enter opens, Esc
// closes. Rows reuse the chat mention picker's option style and the home
// file rows' folder/label chips.
//
// A query starting with ">" is the command palette (Ctrl+Shift+P opens it
// with the prefix typed): `commands()` — App's list of what applies right
// now, each {id, label, group, keyLabel, run} (docs/dev/hotkeys.md) —
// filtered by the same matcher on the label and group, Enter runs the pick.

import React, { useEffect, useMemo, useRef, useState } from "react";
import { FileGlyph, FolderIcon, LabelIcon, PlusIcon, SearchIcon, TerminalIcon } from "../shared/ui/Icons";
import { commandIcon } from "../app/commandIcons.jsx";
import { chordLabel } from "../shared/lib/hotkeys.js";
import { MarkedText } from "../search/SearchPanel";
import { CardLabels } from "./FileBrowser";
import { createLibraryMatcher } from "./librarySearch";
import { formatRelativeTime, pageAttachment, parseFolderTags } from "./libraryUtils";
import { t, tn } from "../shared/i18n/i18n.js";

const MAX_ROWS = 40;
const MAX_CONTAINERS = 8;
const COMMAND_PREFIX = ">";
const SEARCH_CHORD = "Mod-Enter";
const CREATE_CHORD = "Shift-Enter";

export default function QuickOpen({
  open, prefix = "", commands, onClose, pages, recentViews, openTabs, currentPageId, onOpen,
  folders = [], folderMeta = {}, onOpenFolder, onOpenLabel, onSearch, onCreate,
}) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const commandMode = query.startsWith(COMMAND_PREFIX);
  const q = query.trim();
  const canSearch = !!onSearch;
  const canCreate = !!onCreate;

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

  // The commands that apply: App builds the list from its current state.
  const commandList = useMemo(() => (open && commandMode && commands ? commands() : []), [open, commandMode, commands]);

  // Every label in the library with its page count (the Labels section).
  const labelCounts = useMemo(() => {
    const m = new Map();
    if (!open) return m;
    for (const p of pages) for (const l of parseFolderTags(p.properties?.category)) m.set(l, (m.get(l) || 0) + 1);
    return m;
  }, [open, pages]);

  // One flat list for the keyboard; each row names the section it sits
  // under: {key, section, page | folder | label | action | cmd}.
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
    const recentAt = new Map(recentViews.map((r) => [r.id, r.at || ""]));
    const recentRank = new Map(recentViews.map((r, i) => [r.id, i]));
    const tabs = new Set(openTabs.map((tab) => tab.id));
    const match = createLibraryMatcher(query);
    if (!match) {
      // No query: recents, then open tabs, then everything else by last edit.
      const sectionOf = (p) => (recentRank.has(p.id) ? "recent" : tabs.has(p.id) ? "tabs" : "rest");
      const order = { recent: 0, tabs: 1, rest: 2 };
      return pages
        .map((p) => ({ page: p, section: sectionOf(p) }))
        .sort((a, b) => (order[a.section] - order[b.section])
          || ((recentRank.get(a.page.id) ?? 0) - (recentRank.get(b.page.id) ?? 0))
          || (b.page.updated_at || "").localeCompare(a.page.updated_at || ""))
        .slice(0, MAX_ROWS)
        .map((r) => ({
          key: r.page.id, page: r.page, section: r.section,
          time: r.section === "recent" ? recentAt.get(r.page.id) : r.page.updated_at,
        }));
    }
    const rankOf = (p) => (recentRank.has(p.id) ? recentRank.get(p.id) : tabs.has(p.id) ? 1000 : 2000);
    const pageRows = pages
      .map((p) => ({
        page: p,
        score: match(p.content, [...parseFolderTags(p.properties?.folder), ...parseFolderTags(p.properties?.category)]),
        rank: rankOf(p),
      }))
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || (a.rank - b.rank)
        || (b.page.updated_at || "").localeCompare(a.page.updated_at || ""))
      .slice(0, MAX_ROWS)
      .map((r) => ({ key: r.page.id, page: r.page, section: "pages", time: recentAt.get(r.page.id) || r.page.updated_at }));
    // A folder matches on its own name or its path ("physics/rydberg").
    const folderRows = folders
      .map((f) => ({ folder: f, score: match(f.slice(f.lastIndexOf("/") + 1), [f]) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || a.folder.localeCompare(b.folder))
      .slice(0, MAX_CONTAINERS)
      .map((r) => ({ key: `folder:${r.folder}`, folder: r.folder, section: "folders" }));
    const labelRows = [...labelCounts.keys()]
      .map((l) => ({ label: l, score: match(l) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || a.label.localeCompare(b.label))
      .slice(0, MAX_CONTAINERS)
      .map((r) => ({ key: `label:${r.label}`, label: r.label, section: "labels" }));
    const actions = [];
    if (canSearch) actions.push({ key: "action:search", action: "search", section: "actions" });
    if (canCreate) actions.push({ key: "action:create", action: "create", section: "actions" });
    return [...pageRows, ...folderRows, ...labelRows, ...actions];
  }, [open, commandMode, commandList, pages, folders, labelCounts, recentViews, openTabs, query, canSearch, canCreate]);

  if (!open) return null;
  const runAction = (action) => {
    onClose();
    if (action === "search") onSearch(q);
    else onCreate(q);
  };
  const choose = (r) => {
    if (!r) return;
    if (r.action) { runAction(r.action); return; }
    onClose();
    // A command runs once the palette is gone: with the input still focused,
    // a command that looks at the focus (rename, undo) would decline, and
    // the focus hold above would pull focus back from what it opens.
    if (r.cmd) setTimeout(r.cmd.run, 0);
    else if (r.folder) onOpenFolder(r.folder);
    else if (r.label) onOpenLabel(r.label);
    else onOpen(r.page.id);
  };
  const sectionTitle = {
    recent: t("Recent"), tabs: t("Open tabs"), rest: t("Everything else"),
    pages: t("Pages"), folders: t("Folders"), labels: t("Labels"), actions: t("Actions"),
  };
  const placeholder = commandMode ? t("Type a command") : t("Search pages by title or label");
  const hint = commandMode
    ? t("↑↓ choose · Enter run · Esc close")
    : onCreate
      ? t("↑↓ choose · Enter open · {search} search everywhere · {create} new page · > commands", { search: chordLabel(SEARCH_CHORD), create: chordLabel(CREATE_CHORD) })
      : t("↑↓ choose · Enter open · {search} search everywhere · > commands", { search: chordLabel(SEARCH_CHORD) });
  const optionProps = (r, i, extra) => ({
    type: "button", role: "option", tabIndex: -1,
    "aria-selected": i === active,
    className: `slashMenuItem chatMentionOption quickOpenRow${i === active ? " selected" : ""}`,
    onPointerDown: (e) => e.preventDefault(),
    onMouseEnter: () => setActive(i),
    onClick: () => choose(r),
    ...extra,
  });
  const rowOf = (r, i) => {
    if (r.cmd) {
      const CmdIcon = commandIcon(r.cmd);
      return (
        <button key={r.key} {...optionProps(r, i, { title: r.cmd.label })}>
          <CmdIcon size={16} />
          <span><strong>{r.cmd.label}</strong><small>{r.cmd.group}</small></span>
          {r.cmd.keyLabel && <em className="quickOpenTag quickOpenKey">{r.cmd.keyLabel}</em>}
        </button>
      );
    }
    if (r.action) {
      const search = r.action === "search";
      return (
        <button key={r.key} {...optionProps(r, i, { "data-action": r.action })}>
          {search ? <SearchIcon size={16} /> : <PlusIcon size={16} />}
          <span className="quickOpenAction">
            {search ? t("Search inside notes and PDFs for “{q}”", { q }) : t("Create page “{q}”", { q })}
          </span>
          <em className="quickOpenTag quickOpenKey">{chordLabel(search ? SEARCH_CHORD : CREATE_CHORD)}</em>
        </button>
      );
    }
    if (r.folder) {
      const f = r.folder;
      const parent = f.includes("/") ? f.slice(0, f.lastIndexOf("/")) : "";
      const count = tn("{n} page", "{n} pages", folderMeta[f]?.count || 0);
      return (
        <button key={r.key} {...optionProps(r, i, { title: f, "data-kind": "folder" })}>
          <FolderIcon size={16} className="quickOpenFolderIcon" />
          <span>
            <strong><MarkedText text={f.slice(f.lastIndexOf("/") + 1)} query={q} /></strong>
            {parent ? <small>{t("in {parent}", { parent })}</small> : null}
          </span>
          <em className="quickOpenTime">{count}</em>
        </button>
      );
    }
    if (r.label) {
      return (
        <button key={r.key} {...optionProps(r, i, { title: r.label, "data-kind": "label" })}>
          <LabelIcon size={16} />
          <span><strong><MarkedText text={r.label} query={q} /></strong></span>
          <em className="quickOpenTime">{tn("{n} page", "{n} pages", labelCounts.get(r.label) || 0)}</em>
        </button>
      );
    }
    const { page } = r;
    const meta = page.properties?.meta || {};
    const authors = (meta.authors || []).slice(0, 2).join(", ");
    const detail = [authors, meta.year].filter(Boolean).join(" · ");
    const title = page.content || t("Untitled");
    return (
      <button key={r.key} {...optionProps(r, i, { title: [title, detail].filter(Boolean).join("\n"), "data-kind": "page" })}>
        <FileGlyph isPdf={!!pageAttachment(page)} size={16} />
        <span><strong><MarkedText text={title} query={q} /></strong>{detail && <small>{detail}</small>}</span>
        <CardLabels className="fileRowLabels" folders={parseFolderTags(page.properties?.folder)} labels={parseFolderTags(page.properties?.category)} />
        {page.id === currentPageId
          ? <em className="quickOpenTag">{t("Current")}</em>
          : r.time ? <em className="quickOpenTime">{formatRelativeTime(r.time)}</em> : null}
      </button>
    );
  };
  return (
    <div className="reportOverlay quickOpenOverlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="reportModal quickOpen" role="dialog" aria-label={commandMode ? t("Command palette") : t("Open a page")}>
        <div className="quickOpenInput">
          {commandMode ? <TerminalIcon size={16} /> : <SearchIcon size={16} />}
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
              const action = e.key !== "Enter" || commandMode || !q ? null
                : (e.ctrlKey || e.metaKey) && onSearch ? "search"
                : e.shiftKey && onCreate ? "create" : null;
              if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                setActive((i) => (i + (e.key === "ArrowDown" ? 1 : -1) + results.length) % (results.length || 1));
              } else if (action) {
                e.preventDefault();
                runAction(action);
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
          {results.map((r, i) => (
            <React.Fragment key={r.key}>
              {r.section && r.section !== results[i - 1]?.section ? (
                <div className="quickOpenSection" role="presentation">{sectionTitle[r.section]}</div>
              ) : null}
              {rowOf(r, i)}
            </React.Fragment>
          ))}
          {!results.length && (
            <div className="popoverHint">
              {commandMode ? t("No matching commands.") : pages.length ? t("No matching pages.") : t("No pages yet.")}
            </div>
          )}
        </div>
        <div className="chatMentionHint">{hint}</div>
      </div>
    </div>
  );
}
