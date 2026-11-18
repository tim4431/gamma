// A synced folder's file or directory by the kind its name says (shared/ui/
// fileIcons.js; docs/dev/ui-design.md "File icons"): a file is FileGlyph's
// sheet with its kind's mark, a directory FolderKindIcon. `path` is the
// file's path in its synced folder, or its name; a page with none (a
// library page) and a name of no kind draw FileGlyph of `format`. `dir` a
// directory, `root` the synced folder itself, which is SyncedFolderIcon
// (`badge` its sync's tone); `size` in px, none for a card's cover
// (.tileGlyph).
import { fileKind, folderKind } from "./fileIcons.js";
import { FileGlyph, FolderGlyph, FolderKindIcon, SyncedFolderIcon } from "./Icons";

export default function FileIcon({ path = "", format = "", dir = false, root = false, badge = "", size, className = "" }) {
  if (root) {
    return size ? <SyncedFolderIcon size={size} badge={badge} className={className || undefined} />
      : <SyncedFolderIcon size={null} strokeWidth={1.5} badge={badge} className={`tileGlyph${className ? ` ${className}` : ""}`} />;
  }
  if (dir) return size ? <FolderKindIcon size={size} kind={folderKind(path)} /> : <FolderGlyph />;
  return <FileGlyph kind={fileKind(path)} format={format} size={size} />;
}
