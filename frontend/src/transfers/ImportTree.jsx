import React from "react";
import { FileIcon, FolderIcon } from "../shared/ui/Icons";
import { fmtBytes } from "../shared/lib/utils";
import { reducedMotion } from "../shared/ui/motion.js";
import { byName, itemIds, treeItemIds, treeSize } from "./importReview";
import { t } from "../shared/i18n/i18n.js";

// A review of thousands of notes (a vault) stays quick to open and to click
// through: past OPEN_UP_TO files its folders start closed, a closed
// folder's rows are made once it opens, and a list shows ROWS files at a
// time.
const OPEN_UP_TO = 300;
const ROWS = 300;

// The two columns answer each other (`link`, from ImportReviewDialog): a
// file with a match in the other column has its name as a button, the
// rows of the match chosen are lit, and `link.reveal` ({ key, path, index,
// n }) is the row of this column to show — each new `n` opens the folders
// on its `path`, makes its list long enough to hold it and scrolls to it.

function SelectionBox({ ids, selected, onSelect, label }) {
  const ref = React.useRef(null);
  const count = ids.filter(id => selected.has(id)).length;
  React.useEffect(() => { if (ref.current) ref.current.indeterminate = count > 0 && count < ids.length; }, [count, ids.length]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={count === ids.length && count > 0}
    onChange={event => onSelect(ids, event.target.checked)} />;
}
function Folder({ name, child, open, reveal, ...tree }) {
  const [toggled, setToggled] = React.useState(null); // the reader's own open or close, else `open`
  const [revealed, setRevealed] = React.useState(0);
  // On the way to the row to show, a folder opens at once (importReview.css):
  // grown open, its rows would not yet stand where the scroll must reach.
  // Only that opening snaps; the reader's own folds move as any other.
  const [snap, setSnap] = React.useState(false);
  if (reveal && reveal.n !== revealed) { setRevealed(reveal.n); setToggled(true); setSnap(true); } // a row to show is inside
  React.useEffect(() => {
    if (!snap) return undefined;
    const frame = requestAnimationFrame(() => setSnap(false));
    return () => cancelAnimationFrame(frame);
  }, [snap]);
  const shown = toggled ?? open;
  return <li><details className={snap ? "importTreeReveal" : undefined} open={shown} onToggle={event => { if (event.currentTarget.open !== shown) setToggled(event.currentTarget.open); }}>
    <summary>{tree.onSelect ? <SelectionBox ids={treeItemIds(child)} selected={tree.selected} onSelect={tree.onSelect} label={t("Select folder {name}", { name: name })} /> : null}
      <FolderIcon size={16} /><span>{name}</span></summary>
    {shown ? <TreeList node={child} open={open} reveal={reveal} {...tree} /> : null}
    {!child.folders.size && !child.files.length ? <span className="importEmpty">{t("Empty folder")}</span> : null}
  </details></li>;
}
function FileRow({ file, library, selected, onSelect, complete, link }) {
  const key = link ? link.rowKey(file) : null;
  const linked = key != null && link.linked(key);
  const lit = linked && link.lit.has(key);
  // The match just chosen flashes once, on a mark under the row's content:
  // a new key per choice starts it again, while the row and its button stay
  // (the one pressed keeps the focus).
  const flash = lit && link.reveal ? link.reveal.n : 0;
  return <div className={`importTreeFile${lit ? " linked" : ""}`}
    data-row={linked ? key : undefined} title={file.source_path || file.path}>
    {flash ? <span key={flash} className="importRowFlash" aria-hidden="true" /> : null}
    {onSelect ? <SelectionBox ids={itemIds(file)} selected={selected} onSelect={onSelect} label={t("Import {name}", { name: file.name })} /> : null}
    <FileIcon size={16} />
    <div className="importFileText">
      {linked ? <button type="button" className="importRowLink" aria-pressed={link.pressed === key}
        onClick={() => link.onPick(key, file.name)}>{file.name}</button> : <span>{file.name}</span>}
      {library ? <small>{file.action === "skip" ? t("Already in library") : file.action === "merge" ? (complete ? t("Updated") : t("Update existing page")) : (complete ? t("Imported") : t("New page"))}
        {file.notes ? t(" · {notes} exported note{_s}", { notes: file.notes, _s: file.notes === 1 ? "" : "s" }) : ""}
        {file.attachments ? t(" · {n} attachment{_s}", { n: file.attachments, _s: file.attachments === 1 ? "" : "s" }) : ""}</small>
        : <small>{fmtBytes(file.size)}{file.status === "not_imported" ? t(" · Not imported") : ""}</small>}
      {library && file.warnings?.length ? <small className="importWarning">{file.warnings.map(w => w.reason).join(" ")}</small> : null}
    </div>
    {library ? <span className="importKind">{file.kind === "pdf" ? "PDF" : file.kind === "chat" ? t("Chat") : t("Page")}</span> : null}
  </div>;
}
function TreeList({ node, library = false, selected, onSelect, complete = false, open = treeSize(node) <= OPEN_UP_TO, link, reveal, listRef }) {
  const [rows, setRows] = React.useState(ROWS);
  const [revealed, setRevealed] = React.useState(0);
  if (reveal && !reveal.path.length && reveal.n !== revealed) { // the row to show is one of these files
    setRevealed(reveal.n);
    if (reveal.index >= rows) setRows(Math.ceil((reveal.index + 1) / ROWS) * ROWS);
  }
  const tree = { library, selected, onSelect, complete, link };
  return <ul className="importTree" ref={listRef}>
    {[...node.folders].sort(byName).map(([name, child]) =>
      <Folder key={name} name={name} child={child} open={open} {...tree}
        reveal={reveal?.path[0] === name ? { ...reveal, path: reveal.path.slice(1) } : null} />)}
    {node.files.slice(0, rows).map((file, index) => <li key={`${file.id || file.key || file.path}-${index}`}>
      <FileRow file={file} {...tree} />
    </li>)}
    {node.files.length > rows ? <li><button type="button" className="loadMoreBtn" onClick={() => setRows(n => n + ROWS)}>
      {t("Showing {shown} of {total} — load more", { shown: rows, total: node.files.length })}</button></li> : null}
  </ul>;
}
// The row to show, centred in its column's scroll box, and that box brought
// into the dialog's view (a phone stacks the columns).
function showRow(row) {
  const behavior = reducedMotion() ? "auto" : "smooth";
  const box = row.closest(".importTreeScroll");
  if (!box) { row.scrollIntoView({ block: "nearest", behavior }); return; }
  const at = row.getBoundingClientRect(), view = box.getBoundingClientRect();
  if (at.top < view.top || at.bottom > view.bottom) {
    box.scrollTo({ top: box.scrollTop + at.top - view.top - (view.height - at.height) / 2, behavior });
  }
  box.scrollIntoView({ block: "nearest", behavior });
}
export default function ImportTree({ link, ...props }) {
  const list = React.useRef(null);
  const reveal = link?.reveal || null;
  // Once per choice, after the render that opened the way to its row.
  React.useEffect(() => {
    const row = reveal && list.current?.querySelector(`[data-row="${CSS.escape(reveal.key)}"]`);
    if (row) showRow(row);
  }, [reveal?.n]); // eslint-disable-line react-hooks/exhaustive-deps
  return <TreeList {...props} link={link} reveal={reveal} listRef={list} />;
}
