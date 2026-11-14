// One row of a tree of folders and files (docs/dev/ui-design.md "Trees"):
// the Files window (FileTree.jsx) and a synced folder's folder view
// (FolderTree.jsx) draw every row with it. A folder's or a file's row:
// indent guides down to its level, a chevron for a folder, its icon, its
// name, what follows the name, and the "⋯". A "more" row is a button
// ("Show {n} more files"), a "note" row a muted line (Reading…, Could not
// read this folder. with Try again, No tracked files here.).
import { t } from "../shared/i18n/i18n.js";
import FileIcon from "../shared/ui/FileIcon.jsx";
import { MenuButton } from "../shared/ui/Menus";
import { Chevron } from "../shared/ui/MotionWidgets";
import "./tree.css";

// `row` a fileTree.js row; rowRef, tabbable, active and onActive(key) the
// tree's (useTree.js rowProps); `selected`; onPress(row) a "more" row's
// press and a failed note's Try again. `icon` in place of the row's own (a
// synced folder's file or directory by its theme icon, FileIcon, a root
// folder's the theme's root; a library folder's FolderIcon, a library
// page's FileGlyph), `name` in place of its name (a rename's field),
// `after` what follows the name (a count, the folder view's columns),
// `menu` the "⋯"'s opener; `props` the row element's own (its press, its
// menu gesture, a drag, a drop, a motion key).
export default function TreeRow({ row, rowRef, tabbable, active, onActive, selected = false, onPress, icon, name, after, menu,
  className = "", ...props }) {
  const item = {
    ref: rowRef, role: "treeitem", "aria-level": row.level, tabIndex: tabbable ? 0 : -1,
    onFocus: (e) => { if (e.target === e.currentTarget) onActive(row.key); },
    "data-kind": row.kind, "data-id": row.id, "data-key": row.key, style: { "--level": row.level },
  };
  const classes = `fileTreeRow${active ? " active" : ""}${className ? ` ${className}` : ""}`;
  if (row.kind === "more") {
    return (
      <button type="button" {...item} {...props} className={`${classes} fileTreeMore`} aria-selected={false}
        aria-disabled={row.loading || undefined} onClick={() => onPress(row)}>
        <span className="fileTreeChevron" />
        <span className="fileTreeName">{row.name}</span>
      </button>
    );
  }
  if (row.kind === "note") {
    return (
      <div {...item} {...props} className={`${classes} fileTreeNote`} aria-selected={false}>
        <span className="fileTreeChevron" />
        <span className="fileTreeName">{row.name}</span>
        {row.failed ? <button type="button" className="uiBtn sm" tabIndex={-1} onClick={() => onPress(row)}>{t("Try again")}</button> : null}
      </div>
    );
  }
  const folder = row.kind === "folder";
  return (
    <div {...item} {...props} aria-expanded={folder ? row.expanded : undefined} aria-selected={selected}
      className={`${classes} rowMenuHost${selected ? " selected" : ""}`}>
      <span className="fileTreeChevron">{folder ? <Chevron open={row.expanded} dir="right" /> : null}</span>
      {icon || <FileIcon path={row.path} format={row.format} dir={folder} open={row.expanded} root={row.root} size={16} />}
      {name || <span className="fileTreeName">{row.name}</span>}
      {after}
      <MenuButton className="fileTreeMenu" open={menu} tabIndex={-1} />
    </div>
  );
}
