import React from "react";
import { FileIcon, FolderIcon } from "../shared/ui/Icons";
import { fmtBytes } from "../shared/lib/utils";
import { itemIds, treeItemIds, treeSize } from "./importReview";
import { t } from "../shared/i18n/i18n.js";

// A review of thousands of notes (a vault) stays quick to open and to click
// through: past OPEN_UP_TO files its folders start closed, a closed
// folder's rows are made once it opens, and a list shows ROWS files at a
// time.
const OPEN_UP_TO = 300;
const ROWS = 300;

function SelectionBox({ ids, selected, onSelect, label }) {
  const ref = React.useRef(null);
  const count = ids.filter(id => selected.has(id)).length;
  React.useEffect(() => { if (ref.current) ref.current.indeterminate = count > 0 && count < ids.length; }, [count, ids.length]);
  return <input ref={ref} type="checkbox" aria-label={label} checked={count === ids.length && count > 0}
    onChange={event => onSelect(ids, event.target.checked)} />;
}
function Folder({ name, child, open, ...tree }) {
  const [toggled, setToggled] = React.useState(null); // the reader's own open or close, else `open`
  const shown = toggled ?? open;
  return <li><details open={shown} onToggle={event => { if (event.currentTarget.open !== shown) setToggled(event.currentTarget.open); }}>
    <summary>{tree.onSelect ? <SelectionBox ids={treeItemIds(child)} selected={tree.selected} onSelect={tree.onSelect} label={t("Select folder {name}", { name: name })} /> : null}
      <FolderIcon size={16} /><span>{name}</span></summary>
    {shown ? <ImportTree node={child} open={open} {...tree} /> : null}
    {!child.folders.size && !child.files.length ? <span className="importEmpty">{t("Empty folder")}</span> : null}
  </details></li>;
}
export default function ImportTree({ node, library = false, selected, onSelect, complete = false, open = treeSize(node) <= OPEN_UP_TO }) {
  const [rows, setRows] = React.useState(ROWS);
  const tree = { library, selected, onSelect, complete };
  return <ul className="importTree">
    {[...node.folders].sort(([a], [b]) => a.localeCompare(b)).map(([name, child]) =>
      <Folder key={name} name={name} child={child} open={open} {...tree} />)}
    {node.files.slice(0, rows).map((file, index) => <li key={`${file.id || file.key || file.path}-${index}`}>
      <div className="importTreeFile" title={file.source_path || file.path}>
        {onSelect ? <SelectionBox ids={itemIds(file)} selected={selected} onSelect={onSelect} label={t("Import {name}", { name: file.name })} /> : null}
        <FileIcon size={16} />
        <div className="importFileText"><span>{file.name}</span>
          {library ? <small>{file.action === "skip" ? t("Already in library") : file.action === "merge" ? (complete ? t("Updated") : t("Update existing page")) : (complete ? t("Imported") : t("New page"))}
            {file.notes ? t(" · {notes} exported note{_s}", { notes: file.notes, _s: file.notes === 1 ? "" : "s" }) : ""}
            {file.attachments ? t(" · {n} attachment{_s}", { n: file.attachments, _s: file.attachments === 1 ? "" : "s" }) : ""}</small>
            : <small>{fmtBytes(file.size)}{file.status === "not_imported" ? t(" · Not imported") : ""}</small>}
          {library && file.warnings?.length ? <small className="importWarning">{file.warnings.map(w => w.reason).join(" ")}</small> : null}
        </div>
        {library ? <span className="importKind">{file.kind === "pdf" ? "PDF" : file.kind === "chat" ? t("Chat") : t("Page")}</span> : null}
      </div>
    </li>)}
    {node.files.length > rows ? <li><button type="button" className="loadMoreBtn" onClick={() => setRows(n => n + ROWS)}>
      {t("Showing {shown} of {total} — load more", { shown: rows, total: node.files.length })}</button></li> : null}
  </ul>;
}
