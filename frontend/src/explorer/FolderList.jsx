// A work folder's directory as the home listing shows it (docs/dev/
// work_folders.md "The folder's list view"), in place of the library's grid
// or list: its directories first, by name, each with the files below it,
// then its files a page of rows at a time (explorer/useFolderRows.js),
// in the order the server sorts them. One column table draws the header and
// every row, so they line up (library/library.css). What a row does (open,
// select, its menu, its pin, a drop) is App's, handed in per row: the rows
// are App's pages like the listing's, found in its lookups.
import { t, tn } from "../shared/i18n/i18n.js";
import { fmtBytes } from "../shared/lib/format.js";
import { MenuButton } from "../shared/ui/Menus";
import ListMotion from "../shared/ui/ListMotion";
import { formatFullDate, formatLabel, formatShortDate, pageFormat } from "../library/libraryUtils";

// The columns after the name: [key, class, header, a file's cell, a
// directory's cell].
const COLUMNS = [
  ["format", "fileRowFormat", () => t("Format"), (b) => formatLabel(pageFormat(b)), (d) => tn("{n} file", "{n} files", d.count)],
  ["size", "fileRowSize", () => t("File size"), (b) => (b._size != null ? fmtBytes(b._size) : "—"), () => ""],
  ["modified", "fileRowDate", () => t("Modified"), (b) => formatShortDate(b._updatedAt), () => ""],
  ["comments", "fileRowComments", () => t("Comments"), (b) => (b.open_comments ? String(b.open_comments) : ""), () => ""],
];

// The name a file is listed by: its file's, else (a note made in Gamma whose
// file the vault has not written yet) its title.
export const fileName = (b) => b._fileName || b.content || t("Untitled");

// The rows a filter box's text keeps: the files whose name holds it.
export function filterRows(rows, query) {
  const q = query.trim().toLowerCase();
  return q ? rows.filter((b) => fileName(b).toLowerCase().includes(q)) : rows;
}

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

// `dirs`: [{id, name, count}]; `rows`: App's page rows of the loaded files
// (filtered already), each with the read's `open_comments`;`loaded`/`total`/`next`/`loading`/`failed`: the read's
// (useFolderRows), with `onMore` reading the next page and `onRetry` the
// first again. `dirRow(dir)` → {props, icon, menu} and `fileRow(b)` →
// {props, icon, name, menu, pin}: what App puts on each row (the name a
// rename's input in place of the file's name). `empty`: the line shown with
// no row at all. `scope` and `still` are ListMotion's: another folder,
// sort, filter or page of rows is another listing, in which nothing moves.
export default function FolderList({ dirs, rows, loaded, total, next, loading, failed, onMore, onRetry, empty, listRef,
  scope, still, dirRow, fileRow, onBlank }) {
  const keys = [...dirs.map((d) => `dir:${d.id}`), ...rows.map((b) => b._pageId)];
  const none = !dirs.length && !rows.length;
  return (
    <>
      {none && failed ? (
        <div className="empty homeEmpty" role="alert">
          {t("Could not read this folder.")}{" "}
          <button type="button" className="uiBtn sm" onClick={onRetry}>{t("Try again")}</button>
        </div>
      ) : none && !loading ? <div className="empty homeEmpty">{empty}</div> : null}
      <div className="fileList workList" ref={listRef} onClick={(e) => { if (e.target.classList.contains("fileList")) onBlank(); }}>
        {none ? null : (
          <div className="fileListHead" aria-hidden="true">
            <span className="fileListHeadName">{t("Name")}</span>
            {COLUMNS.map(([key, className, head]) => <span key={key} className={className}>{head()}</span>)}
            <span className="fileRowPinSlot" />
            {menuSlot}
          </div>
        )}
        <ListMotion keys={keys} scope={scope} still={still} enter="fade" leave="row">
          {dirs.map((d) => {
            const { props, icon, menu } = dirRow(d);
            return (
              <div key={`dir:${d.id}`} data-motion-key={`dir:${d.id}`} {...props}>
                {icon}
                <span className="folderName">{d.name}</span>
                <Columns cells={COLUMNS.map((c) => c[4](d))} menu={menu} />
              </div>
            );
          })}
          {rows.map((b) => {
            const { props, icon, name, menu, pin } = fileRow(b);
            return (
              <div key={b._pageId} data-motion-key={b._pageId} {...props}>
                <span className="fileRowIcon">{icon}</span>
                {name || <span className="fileRowName">{fileName(b)}</span>}
                <Columns cells={COLUMNS.map((c) => c[3](b))} title={formatFullDate(b._updatedAt)} menu={menu} pin={pin} />
              </div>
            );
          })}
        </ListMotion>
      </div>
      {next != null ? (
        <button type="button" className="loadMoreBtn" disabled={loading} onClick={onMore}>
          {t("Showing {shown} of {total} — load more", { shown: loaded, total })}
        </button>
      ) : null}
    </>
  );
}
