// A synced folder's file or directory by its theme icon (shared/ui/
// fileIcons.js; docs/dev/ui-design.md "File icons"): an <img> of the icon's
// versioned URL, the light theme's variant on a light scheme. `path` is the
// file's path in its synced folder, or its name; a page with none (a
// library page), a file the theme has no icon for and an icon that fails to
// load draw FileGlyph of `format` instead (a directory FolderIcon), and
// while the map is first read the icon's room stays empty. `dir` a
// directory, `open` an open one, `root` the synced folder itself, which is
// Gamma's SyncedFolderIcon, never the theme's (`badge` its sync's tone);
// `size` in px, none for a card's cover (.tileGlyph).
import { useState, useSyncExternalStore } from "react";
import { iconFor, iconUrl, iconsNow, subscribeIcons } from "./fileIcons.js";
import { FileGlyph, FolderGlyph, FolderIcon, SyncedFolderIcon } from "./Icons";

const unwatched = () => () => {};

export default function FileIcon({ path = "", format = "", dir = false, open = false, root = false, badge = "", size, className = "" }) {
  const named = !!path && !root;
  const icons = useSyncExternalStore(named ? subscribeIcons : unwatched, iconsNow);
  const [broken, setBroken] = useState("");
  if (root) {
    return size ? <SyncedFolderIcon size={size} badge={badge} className={className || undefined} />
      : <SyncedFolderIcon size={null} strokeWidth={1.5} badge={badge} className={`tileGlyph${className ? ` ${className}` : ""}`} />;
  }
  const folder = dir;
  const fallback = folder ? (size ? <FolderIcon size={size} /> : <FolderGlyph />) : <FileGlyph format={format} size={size} />;
  if (!named || icons?.failed) return fallback;
  // the glyph's own classes, so every place that sets a kind glyph sets it
  const classes = `tileGlyph fileIcon${size ? " kindGlyph" : ""}${className ? ` ${className}` : ""}`;
  const style = size ? { width: size, height: size } : undefined;
  if (!icons) return <span className={classes} style={style} aria-hidden="true" />;
  const id = iconFor(icons.map, path, { dir, open, root, light: icons.light });
  const src = iconUrl(icons.version, id);
  if ((!folder && id === icons.map.file) || broken === src) return fallback;
  return <img className={classes} src={src} alt="" style={style} draggable={false} onError={() => setBroken(src)} />;
}
