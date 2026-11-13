// Quick open (Ctrl+P): a VS Code-style palette over the library, drawn by
// the shared palette (shared/ui/Palette.jsx: the field, the keys, the focus
// hold). Its pages are rankLibraryPages' rows drawn by PageOption, as in the
// chat's @ picker and the page pickers (PagePicker.jsx): an empty query lists
// the recently viewed pages, then the open tabs, then the rest by last edit,
// each group under its heading with its time on the right. Typing ranks
// pages, folders and labels through createLibraryMatcher — the home listing's
// search: typo-tolerant, on the title or the folder/label chips (a sub-page
// on its top's folders, and also on the pages it is in, its row's second
// line), title hits first — in sections of their own with the matched
// characters marked, and ends with two actions: search notes, PDFs and files
// for the query (Ctrl+Enter; the workspace search opens with it) and create
// a page titled with it (Shift+Enter). ↑↓ moves, Enter opens, Esc closes.
//
// For a member (`files`), the work folders' files the query names by name or
// path follow the pages, under Files: the server's rows and marks
// (explorer/useFileSearch.js, GET /api/files), each with its work folder and
// directory under the name; a file a page row already lists is left out.
//
// A query starting with ">" is the command palette (Ctrl+Shift+P opens it
// with the prefix typed): `commands()` — App's list of what applies right
// now, each {id, label, group, keyLabel, run} (docs/dev/hotkeys.md) —
// filtered by the same matcher on the label and group, Enter runs the pick.

import React, { useEffect, useLayoutEffect, useMemo, useState } from "react";
import { FileGlyph, FolderIcon, LabelIcon, PlusIcon, SearchIcon, TerminalIcon } from "../shared/ui/Icons";
import { commandIcon } from "../app/commandIcons.jsx";
import { chordLabel, composing } from "../shared/lib/hotkeys.js";
import { MarkedText } from "../search/SearchPanel";
import PageOption, { recencySections } from "./PageOption";
import { createLibraryMatcher, rankLibraryPages } from "./librarySearch";
import { filedIn, folderPath, formatRelativeTime } from "./libraryUtils";
import { pageDocument } from "../documents/pageDocument.js";
import { fileParts, fileRows } from "../explorer/fileSearch.js";
import { useFileSearch } from "../explorer/useFileSearch.js";
import { fileNameOf } from "../explorer/workPaths.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { guideEvents } from "../guide/events.js";
import Palette from "../shared/ui/Palette";
import { IconSwap } from "../shared/ui/MotionWidgets";

const MAX_ROWS = 40;
const MAX_CONTAINERS = 8;
const COMMAND_PREFIX = ">";
const SEARCH_CHORD = "Mod-Enter";
const CREATE_CHORD = "Shift-Enter";

export default function QuickOpen({
  open, prefix = "", commands, onClose, pages, recentViews, openTabs, currentPageId, onOpen,
  tree, folderMeta = {}, onOpenFolder, onOpenLabel, onSearch, onCreate, files = false, onOpenFile,
}) {
  const [query, setQuery] = useState("");
  const commandMode = query.startsWith(COMMAND_PREFIX);
  const q = query.trim();
  const canSearch = !!onSearch;
  const canCreate = !!onCreate;
  const search = useFileSearch({ query, enabled: open && files && !commandMode });

  // The box keeps the last query while closed: the prefix replaces it before
  // the open palette paints, or a key typed at once would be undone.
  useLayoutEffect(() => { if (open) setQuery(prefix); }, [open, prefix]);
  // The command palette found (the guide's palette hint retires on it).
  useEffect(() => { if (open && commandMode) guideEvents.emit("commands.opened"); }, [open, commandMode]);

  // The commands that apply: App builds the list from its current state.
  const commandList = useMemo(() => (open && commandMode && commands ? commands() : []), [open, commandMode, commands]);

  // Every label in the library with its page count (the Labels section).
  const labelCounts = useMemo(() => {
    const m = new Map();
    if (!open) return m;
    for (const p of pages) for (const l of filedIn(tree.labels, p.properties?.labels)) m.set(l, (m.get(l) || 0) + 1);
    return m;
  }, [open, pages, tree]);

  // One flat list for the keyboard; each row names the section it sits
  // under: {key, section, page | file | folder | label | action | cmd}.
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
    const pageRows = rankLibraryPages(pages, tree, query, { recentViews, openTabs, limit: MAX_ROWS })
      .map((r) => ({ key: r.page.id, ...r }));
    const found = fileRows(search.rows, pageRows);
    const match = createLibraryMatcher(query);
    if (!match) return [...pageRows, ...found];
    // A folder matches on its own name or its path ("Physics / Rydberg").
    const folderRows = [...tree.folders.values()]
      .map((f) => { const path = folderPath(tree, f.id); return { folder: f, path, score: match(f.name, [path]) }; })
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || a.path.localeCompare(b.path))
      .slice(0, MAX_CONTAINERS)
      .map((r) => ({ key: `folder:${r.folder.id}`, folder: r.folder, section: "folders" }));
    const labelRows = [...tree.labels.values()]
      .map((l) => ({ label: l, score: match(l.name) }))
      .filter((r) => r.score > 0)
      .sort((a, b) => (b.score - a.score) || a.label.name.localeCompare(b.label.name))
      .slice(0, MAX_CONTAINERS)
      .map((r) => ({ key: `label:${r.label.id}`, label: r.label, section: "labels" }));
    const actions = [];
    if (canSearch) actions.push({ key: "action:search", action: "search", section: "actions" });
    if (canCreate) actions.push({ key: "action:create", action: "create", section: "actions" });
    return [...pageRows, ...found, ...folderRows, ...labelRows, ...actions];
  }, [open, commandMode, commandList, pages, tree, recentViews, openTabs, query, canSearch, canCreate, search.rows]);

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
    // the palette's focus hold would pull focus back from what it opens.
    if (r.cmd) setTimeout(r.cmd.run, 0);
    else if (r.file) onOpenFile(r.file);
    else if (r.folder) onOpenFolder(r.folder.id);
    else if (r.label) onOpenLabel(r.label.id);
    else onOpen(r.page.id);
  };
  const sectionTitle = {
    ...recencySections(), pages: t("Pages"), files: t("Files"), folders: t("Folders"), labels: t("Labels"), actions: t("Actions"),
  };
  const placeholder = commandMode ? t("Type a command") : t("Search pages, files, folders and labels");
  // The line under the list while files are asked for, or when nothing but
  // the actions answered (they are rows then, so the list's `empty` is not).
  const note = commandMode || !q || !files ? null
    : search.failed ? t("Files could not be searched.")
    : search.loading ? (results.some((r) => r.file) ? null : t("Searching files…"))
    : results.some((r) => !r.action) ? null : t("No matching pages or files.");
  const hint = commandMode
    ? t("↑↓ choose · Enter run · Esc close")
    : onCreate
      ? t("↑↓ choose · Enter open · {search} search everywhere · {create} new page · > commands", { search: chordLabel(SEARCH_CHORD), create: chordLabel(CREATE_CHORD) })
      : t("↑↓ choose · Enter open · {search} search everywhere · > commands", { search: chordLabel(SEARCH_CHORD) });
  const rowOf = (r, props) => {
    if (r.cmd) {
      const CmdIcon = commandIcon(r.cmd);
      return (
        <button {...props} title={r.cmd.label}>
          <CmdIcon size={16} />
          <span><strong>{r.cmd.label}</strong><small>{r.cmd.group}</small></span>
          {r.cmd.keyLabel && <em className="quickOpenTag quickOpenKey">{r.cmd.keyLabel}</em>}
        </button>
      );
    }
    if (r.action) {
      const search = r.action === "search";
      return (
        <button {...props} data-action={r.action}>
          {search ? <SearchIcon size={16} /> : <PlusIcon size={16} />}
          <span className="quickOpenAction">
            {search ? t("Search notes, PDFs and files for “{q}”", { q }) : t("Create page “{q}”", { q })}
          </span>
          <em className="quickOpenTag quickOpenKey">{chordLabel(search ? SEARCH_CHORD : CREATE_CHORD)}</em>
        </button>
      );
    }
    if (r.file) {
      const file = r.file;
      // the server's spans: a path term's match is its to say
      const name = fileNameOf(file);
      const dir = file.dir || "";
      const wName = tree.folders.get(file.properties?.work_folder)?.name || "";
      return (
        <button {...props} data-kind="file" title={`${name}\n${[wName, file.path].filter(Boolean).join(" / ")}`}>
          <FileGlyph format={pageDocument(file)?.format} size={16} />
          <span>
            <strong><MarkedText parts={fileParts(name, file.match?.name)} /></strong>
            <small className="quickOpenPath">
              <span>{wName}</span>{dir ? <> / <MarkedText parts={fileParts(dir, file.match?.path)} /></> : null}
            </small>
          </span>
          {file.id === currentPageId ? <em className="quickOpenTag">{t("Current")}</em>
            : <em className="quickOpenTime">{formatRelativeTime(file.updated_at)}</em>}
        </button>
      );
    }
    if (r.folder) {
      const f = r.folder;
      const parent = folderPath(tree, f.parent);
      const count = tn("{n} page", "{n} pages", folderMeta[f.id]?.count || 0);
      return (
        <button {...props} title={folderPath(tree, f.id)} data-kind="folder">
          <FolderIcon size={16} className="quickOpenFolderIcon" />
          <span>
            <strong><MarkedText text={f.name} query={q} /></strong>
            {parent ? <small>{t("in {parent}", { parent })}</small> : null}
          </span>
          <em className="quickOpenTime">{count}</em>
        </button>
      );
    }
    if (r.label) {
      return (
        <button {...props} title={r.label.name} data-kind="label">
          <LabelIcon size={16} />
          <span><strong><MarkedText text={r.label.name} query={q} /></strong></span>
          <em className="quickOpenTime">{tn("{n} page", "{n} pages", labelCounts.get(r.label.id) || 0)}</em>
        </button>
      );
    }
    return <PageOption {...props} row={r} tree={tree} query={q} currentPageId={currentPageId} />;
  };
  return (
    <Palette
      label={commandMode ? t("Command palette") : t("Open a page")}
      listLabel={commandMode ? t("Commands") : t("Pages")}
      icon={<IconSwap swap={commandMode} from={<SearchIcon size={16} />} to={<TerminalIcon size={16} />} />}
      guide="palette.input"
      placeholder={placeholder}
      query={query}
      onQuery={setQuery}
      rows={results}
      sectionTitle={sectionTitle}
      renderRow={rowOf}
      onChoose={choose}
      // Ctrl+Enter searches everywhere for the query, Shift+Enter makes it a page
      onKeyDown={(e) => {
        const action = e.key !== "Enter" || commandMode || !q ? null
          : (e.ctrlKey || e.metaKey) && onSearch ? "search"
          : e.shiftKey && onCreate ? "create" : null;
        if (!action) return false;
        e.preventDefault();
        runAction(action);
        return true;
      }}
      onClose={onClose}
      empty={commandMode ? t("No matching commands.") : pages.length ? t("No matching pages.") : t("No pages yet.")}
      note={note}
      hint={hint}
    />
  );
}
