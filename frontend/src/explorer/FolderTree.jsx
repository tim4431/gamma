// A synced folder's directory as the home listing shows it (docs/dev/
// work_folders.md "The folder view"), in place of the library's grid or
// list: a tree as VS Code's explorer draws one, the rows the Files window's
// (TreeRow.jsx, fileTree.js folderRows), its directories first by name,
// each opening in place, then its files a page of rows at a time
// (useFolderTree.js), in the order the server sorts them. After each name
// the columns the header names: Format, File size, Modified and Comments.
// The keys are the Files window's (useTree.js); what a file's row does
// (select, open, its menu, its pin, a rename, a drag) is the library's,
// handed in as `row`: the rows are App's pages like the listing's, found in
// its lookups.
import { t, tn } from "../shared/i18n/i18n.js";
import { composing } from "../shared/lib/hotkeys.js";
import { fmtBytes } from "../shared/lib/format.js";
import { FilePlusIcon, PinIcon } from "../shared/ui/Icons";
import { SelectCheck } from "../library/FileBrowser";
import { formatFullDate, formatLabel, formatShortDate, pageFormat } from "../library/libraryUtils";
import TreeRow from "./TreeRow.jsx";
import { useTree } from "./useTree.js";
import { emptyKind, fileName } from "./workFolderActions.js";

// The columns after the name: [key, class, header, a file's cell, a
// directory's cell].
const COLUMNS = [
  ["format", "fileRowFormat", () => t("Format"), (b) => formatLabel(pageFormat(b)), (d) => tn("{n} file", "{n} files", d.count)],
  ["size", "fileRowSize", () => t("File size"), (b) => (b._size != null ? fmtBytes(b._size) : "—"), () => ""],
  ["modified", "fileRowDate", () => t("Modified"), (b) => formatShortDate(b._updatedAt), () => ""],
  ["comments", "fileRowComments", () => t("Comments"), (b) => (b.open_comments ? String(b.open_comments) : ""), () => ""],
];

function Columns({ cells, title, pin }) {
  return (
    <>
      {COLUMNS.map(([key, className], i) => (
        <span key={key} className={className} title={key === "modified" ? title || undefined : undefined}>{cells[i]}</span>
      ))}
      {pin || <span className="fileRowPinSlot" aria-hidden="true" />}
    </>
  );
}

// `rows` the tree's rows (fileTree.js folderRows of the directory shown,
// its files kept to the filter box's `query` by name), `tree` the view's
// state (useFolderTree.js), `folderId` the directory shown; pageOf(id) the
// page row App has of a file (with the read's `open_comments`,
// workFolderActions.listRows). `atTop`: the work folder's top is shown;
// `choosing`: Choose files… is open, which `onChoose` opens (null where it
// is not this app's). `onNewPage`: New page, a note written into the
// directory (null where none is). `row`: what a file's row does —
// {selecting, selectedPages, folderMenu(id), pageMenu(id, title),
// menuPress(menu), onPageClick(b, e), onPageOpen(id), editingId,
// onRename(id, text), onRenameCancel(), onPin(ids, pinned) or null,
// draggable, onDragStart(e, id)}; a directory's row opens and closes in
// place.
export default function FolderTree({ rows, tree, folderId, pageOf, query, atTop, choosing, onChoose, onNewPage, listRef, onBlank, row }) {
  const read = tree.dirs.of(folderId);
  const fileOf = (r) => (r.kind === "file" ? pageOf(r.id) : null);
  function press(r) {
    if (r.kind === "folder") tree.toggle(r.id);
    else if (r.kind === "more") tree.dirs.more(r.id);
    else if (r.failed) tree.dirs.retry(r.id);
  }
  const menuOf = (r) => (r.kind === "folder" ? row.folderMenu(r.id) : r.kind === "file" ? row.pageMenu(r.id, fileOf(r)?.content || r.name) : null);
  const keys = useTree({
    rows, active: tree.active, setActive: tree.setActive, selectedId: row.selectedPages.size === 1 ? [...row.selectedPages][0] : "", menuOf,
    act: (kind, r) => {
      const b = fileOf(r);
      if (kind === "toggle") tree.toggle(r.id);
      else if (kind === "open") { if (b) row.onPageOpen(r.id); else press(r); }
      else if (kind === "select" && b) row.onPageClick(b, null);
    },
  });
  const chooseButton = onChoose ? <button type="button" className="uiBtn sm" onClick={onChoose}>{t("Choose files…")}</button> : null;
  // the directory holds nothing to draw once read: the line says why
  const none = read.loaded && !read.loading && !read.failed && !rows.some((r) => r.kind !== "note");
  const empty = {
    choosing: <>{t("Choosing files…")} {chooseButton}</>,
    nomatch: t("No file here matches “{q}”.", { q: query.trim() }),
    untracked: <>{t("Nothing tracked yet.")} {chooseButton}</>,
    none: t("No tracked files here."),
  }[emptyKind({ choosing, query, loaded: read.rows.length, atTop })];
  return (
    <>
      {onNewPage ? (
        <button className="folderRow folderNewBtn" onClick={onNewPage} title={t("A new note, written into this directory")}>
          <FilePlusIcon size={16} />
          <span className="folderName">{t("New page")}</span>
        </button>
      ) : null}
      {none ? <div className="empty homeEmpty">{empty}</div> : null}
      <div className="fileList folderTree" ref={listRef} onClick={(e) => { if (e.target === e.currentTarget) onBlank(); }}>
        {none ? null : (
          <div className="fileListHead folderTreeHead" aria-hidden="true">
            <span className="fileListHeadName">{t("Name")}</span>
            {COLUMNS.map(([key, className, head]) => <span key={key} className={className}>{head()}</span>)}
            <span className="fileRowPinSlot" />
            <span className="ctlBtn fileRowMenu fileRowMenuSlot" />
          </div>
        )}
        {none ? null : (
          <div aria-label={t("Files")} {...keys.treeProps}>
            {rows.map((r) => {
              const own = keys.rowProps(r);
              if (r.kind === "more" || r.kind === "note") return <TreeRow key={r.key} row={r} {...own} onPress={press} />;
              const menu = menuOf(r);
              if (r.kind === "folder") {
                return (
                  <TreeRow key={r.key} row={r} {...own} menu={menu} title={r.name} onClick={() => { tree.setActive(r.key); press(r); }}
                    after={<Columns cells={COLUMNS.map((c) => c[4](r))} />} {...row.menuPress(menu)} />
                );
              }
              const b = fileOf(r);
              const id = r.id;
              const selected = row.selectedPages.has(id);
              const editing = row.editingId === id;
              const pinned = !!b?._pinned;
              return (
                <TreeRow key={r.key} row={r} {...own} selected={selected} menu={menu}
                  icon={row.selecting && selected ? <SelectCheck /> : null}
                  name={editing ? (
                    <input autoFocus className="fileTreeName fileRowRename" defaultValue={b?.content} onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => {
                        if (composing(e)) return;
                        if (e.key === "Enter") row.onRename(id, e.currentTarget.value);
                        else if (e.key === "Escape") row.onRenameCancel();
                      }}
                      onBlur={(e) => row.onRename(id, e.currentTarget.value)} />
                  ) : b ? <span className="fileTreeName">{fileName(b)}</span> : null}
                  after={b ? (
                    <Columns cells={COLUMNS.map((c) => c[3](b))} title={formatFullDate(b._updatedAt)} pin={row.onPin ? (
                      <button type="button" className={`pinBtn fileRowPin ${pinned ? "pinned" : ""}`} title={pinned ? t("Unpin") : t("Pin to top")}
                        tabIndex={-1} onClick={(e) => { e.stopPropagation(); row.onPin([id], !pinned); }}><PinIcon filled={pinned} size={14} /></button>
                    ) : null} />
                  ) : <Columns cells={COLUMNS.map(() => "")} />}
                  draggable={row.draggable && !editing} onDragStart={(e) => row.onDragStart(e, id)}
                  onClick={(e) => { tree.setActive(r.key); if (b) row.onPageClick(b, e); }}
                  // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
                  onDoubleClick={() => { if (!editing) row.onPageOpen(id); }}
                  title={t("{content}\nClick to select · double-click to open", { content: b ? fileName(b) : r.name })}
                  {...row.menuPress(menu)} />
              );
            })}
          </div>
        )}
      </div>
    </>
  );
}
