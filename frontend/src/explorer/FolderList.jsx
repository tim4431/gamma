// A work folder's directory as the home listing shows it (docs/dev/
// work_folders.md "The folder's list view"), in place of the library's grid
// or list: its directories first, by name, each with the files below it,
// then its files a page of rows at a time (explorer/useFolderRows.js),
// in the order the server sorts them. One column table draws the header and
// every row, so they line up (library/library.css). What a row does (open,
// select, its menu, its pin, a rename, a drag) is the library's, handed in
// as `row`: the rows are App's pages like the listing's, found in its
// lookups.
import { t, tn } from "../shared/i18n/i18n.js";
import { composing } from "../shared/lib/hotkeys.js";
import { fmtBytes } from "../shared/lib/format.js";
import { FilePlusIcon, FileGlyph, FolderIcon, PinIcon } from "../shared/ui/Icons";
import { MenuButton } from "../shared/ui/Menus";
import ListMotion from "../shared/ui/ListMotion";
import { SelectCheck } from "../library/FileBrowser";
import { formatFullDate, formatLabel, formatShortDate, pageFormat } from "../library/libraryUtils";
import { emptyKind, fileName } from "./workFolderActions.js";

// The columns after the name: [key, class, header, a file's cell, a
// directory's cell].
const COLUMNS = [
  ["format", "fileRowFormat", () => t("Format"), (b) => formatLabel(pageFormat(b)), (d) => tn("{n} file", "{n} files", d.count)],
  ["size", "fileRowSize", () => t("File size"), (b) => (b._size != null ? fmtBytes(b._size) : "—"), () => ""],
  ["modified", "fileRowDate", () => t("Modified"), (b) => formatShortDate(b._updatedAt), () => ""],
  ["comments", "fileRowComments", () => t("Comments"), (b) => (b.open_comments ? String(b.open_comments) : ""), () => ""],
];

// Rows a page of the read holds, as useFolderRows reads them: a page more
// of rows is another listing, in which nothing moves.
const PAGE_ROWS = 200;

const menuSlot = <span className="ctlBtn fileRowMenu fileRowMenuSlot" aria-hidden="true" />;

function Columns({ cells, title, menu, pin }) {
  return (
    <>
      {COLUMNS.map(([key, className], i) => (
        <span key={key} className={className} title={key === "modified" ? title || undefined : undefined}>{cells[i]}</span>
      ))}
      {pin || <span className="fileRowPinSlot" aria-hidden="true" />}
      {menu ? <MenuButton className="fileRowMenu" open={menu} /> : menuSlot}
    </>
  );
}

// A directory's row: a click selects it, a double-click opens it.
function DirRow({ d, row }) {
  const menu = row.folderMenu(d.id);
  const selected = row.selectedFolders.has(d.id);
  return (
    <div data-motion-key={`dir:${d.id}`} className={`folderRow rowMenuHost ${selected ? "selected" : ""}`}
      onClick={(e) => row.onFolderClick(d.id, e)}
      // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
      onDoubleClick={() => row.onFolderOpen(d.id)}
      {...row.menuPress(menu)} title={t("Click to select · double-click to open")}>
      {row.selecting && selected ? <SelectCheck /> : <FolderIcon size={16} />}
      <span className="folderName">{d.name}</span>
      <Columns cells={COLUMNS.map((c) => c[4](d))} menu={menu} />
    </div>
  );
}

// A file's row: a click selects it, a double-click opens it; its name an
// input while it is renamed.
function FileRow({ b, row }) {
  const id = b._pageId;
  const isPinned = !!b._pinned;
  const isEditing = row.editingId === id;
  const selected = row.selectedPages.has(id);
  const menu = row.pageMenu(id, b.content);
  return (
    <div data-motion-key={id} className={`fileRow rowMenuHost ${selected ? "selected" : ""}`}
      draggable={row.draggable && !isEditing} onDragStart={(e) => row.onDragStart(e, id)}
      onClick={(e) => row.onPageClick(b, e)}
      // ui-allow double-click: a tap opens where a click selects (ui-design.md, Device questions)
      onDoubleClick={() => { if (!isEditing) row.onPageOpen(id); }}
      {...row.menuPress(menu)} title={t("{content}\nClick to select · double-click to open", { content: b.content })}>
      <span className="fileRowIcon">{row.selecting && selected ? <SelectCheck /> : <FileGlyph format={b._doc?.format} />}</span>
      {isEditing ? (
        <input autoFocus className="fileRowRename" defaultValue={b.content} onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => {
            if (composing(e)) return;
            if (e.key === "Enter") row.onRename(id, e.currentTarget.value);
            else if (e.key === "Escape") row.onRenameCancel();
          }}
          onBlur={(e) => row.onRename(id, e.currentTarget.value)} />
      ) : <span className="fileRowName">{fileName(b)}</span>}
      <Columns cells={COLUMNS.map((c) => c[3](b))} title={formatFullDate(b._updatedAt)} menu={menu} pin={row.onPin ? (
        <button className={`pinBtn fileRowPin ${isPinned ? "pinned" : ""}`} title={isPinned ? t("Unpin") : t("Pin to top")}
          onClick={(e) => { e.stopPropagation(); row.onPin([id], !isPinned); }}><PinIcon filled={isPinned} size={14} /></button>
      ) : null} />
    </div>
  );
}

// `dirs`: [{id, name, count}]; `rows`: App's page rows of the loaded files
// (filtered already, workFolderActions.listRows), each with the read's
// `open_comments`; `read`: the directory's read (useFolderRows: its rows,
// total, next, loading, failed, still, more()), with `onRetry` reading the
// first page again. `folderId`, `sort` and `query` (the filter box's text)
// make the listing ListMotion moves within, and `still` (the library's)
// keeps it as it was. `atTop`: the work folder's top is shown; `choosing`:
// Choose files… is open, which `onChoose` opens (null where it is not
// this app's). `onNewPage`: New page, a note written into the directory
// (null where none is). `row`: what a row does — {selecting,
// selectedFolders, selectedPages, folderMenu(id), pageMenu(id, title),
// menuPress(menu), onFolderClick(id, e), onFolderOpen(id), onPageClick(b,
// e), onPageOpen(id), editingId, onRename(id, text), onRenameCancel(),
// onPin(ids, pinned) or null, draggable, onDragStart(e, id)}.
export default function FolderList({ dirs, rows, read, folderId, sort, query, atTop, choosing, onChoose, onNewPage, onRetry,
  listRef, still, onBlank, row }) {
  const keys = [...dirs.map((d) => `dir:${d.id}`), ...rows.map((b) => b._pageId)];
  const none = !dirs.length && !rows.length;
  const loaded = read.rows.length;
  const chooseButton = onChoose ? <button type="button" className="uiBtn sm" onClick={onChoose}>{t("Choose files…")}</button> : null;
  const empty = {
    choosing: <>{t("Choosing files…")} {chooseButton}</>,
    nomatch: t("No file here matches “{q}”.", { q: query.trim() }),
    untracked: <>{t("Nothing tracked yet.")} {chooseButton}</>,
    none: t("No tracked files here."),
  }[emptyKind({ choosing, query, loaded, atTop })];
  return (
    <>
      {onNewPage ? (
        <button className="folderRow folderNewBtn" onClick={onNewPage} title={t("A new note, written into this directory")}>
          <FilePlusIcon size={16} />
          <span className="folderName">{t("New page")}</span>
        </button>
      ) : null}
      {none && read.failed ? (
        <div className="empty homeEmpty" role="alert">
          {t("Could not read this folder.")}{" "}
          <button type="button" className="uiBtn sm" onClick={onRetry}>{t("Try again")}</button>
        </div>
      ) : none && !read.loading ? <div className="empty homeEmpty">{empty}</div> : null}
      <div className="fileList workList" ref={listRef} onClick={(e) => { if (e.target.classList.contains("fileList")) onBlank(); }}>
        {none ? null : (
          <div className="fileListHead" aria-hidden="true">
            <span className="fileListHeadName">{t("Name")}</span>
            {COLUMNS.map(([key, className, head]) => <span key={key} className={className}>{head()}</span>)}
            <span className="fileRowPinSlot" />
            {menuSlot}
          </div>
        )}
        <ListMotion keys={keys} scope={`work|${folderId}|${sort}|${query}|${Math.ceil(loaded / PAGE_ROWS)}`}
          still={still || read.still} enter="fade" leave="row">
          {dirs.map((d) => <DirRow key={`dir:${d.id}`} d={d} row={row} />)}
          {rows.map((b) => <FileRow key={b._pageId} b={b} row={row} />)}
        </ListMotion>
      </div>
      {read.next != null ? (
        <button type="button" className="loadMoreBtn" disabled={read.loading} onClick={read.more}>
          {t("Showing {shown} of {total} — load more", { shown: loaded, total: read.total })}
        </button>
      ) : null}
    </>
  );
}
