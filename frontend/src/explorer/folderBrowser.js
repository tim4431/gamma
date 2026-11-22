// The folder browser's tree as rows (docs/dev/ui-design.md "Trees";
// explorer/FolderBrowser.jsx): a store's folders and files from its top,
// read a folder at a time, as the rows TreeRow draws and useTree's keys
// walk. A path is the folder's names from the top joined by "/", "" the
// top itself. Pure: tests/folderBrowser.test.mjs.
import { t } from "../shared/i18n/i18n.js";

export const joinPath = (parent, name) => (parent ? `${parent}/${name}` : name);

// `path` and each folder above it, the top's first, "" left out:
// "a/b" → ["a", "a/b"].
export function pathsTo(path) {
  const names = path ? path.split("/") : [];
  return names.map((_, i) => names.slice(0, i + 1).join("/"));
}

// A new folder's name as typed, trimmed and its runs of spaces made one, as
// the server keeps it. A "/" would make it two folders; every other rule
// is the store's, which says why it refuses a name.
export const cleanName = (text) => text.trim().replace(/\s+/g, " ");
export const nameProblem = (name) => (name.includes("/") ? t("A folder's name cannot hold /.") : "");

const byName = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true });

// The rows drawn, top to bottom: the top (`root`, its name; level 1), then
// under each open folder (`expanded`, paths) its folders by name — those
// its read gave and those of `added` (paths) not among them, which are not
// made yet (`made` false: a bucket makes a folder with the first thing put
// in it, and the folder chosen before may be gone) — then the field of the
// folder being named there (`naming`: {parent}, a "field" row), then its
// files by name, then a note row: Reading…, Could not read this folder
// (with Try again), empty, Not made yet, or that the read showed one page
// of more. `reads`: path → {loaded, loading, error, folders: [{name,
// modified}], files: [{name, size, modified}], more}, a folder never read
// absent. Each row: {key, kind ("folder", "file", "field" or "note"), id
// (its path; a note's, its folder's), path, name, level, parent (the
// folder row's key, "" for the top), expanded, made, size, modified,
// loading, failed} — the fields TreeRow and fileTree.js keyAction read,
// and the folder browser's own.
export function browserRows({ root, reads, expanded, added = [], naming = null }) {
  const open = expanded instanceof Set ? expanded : new Set(expanded);
  const pending = new Map(); // a folder's path → the names of the folders of `added` in it
  for (const path of added) {
    const at = path.lastIndexOf("/");
    const parent = at < 0 ? "" : path.slice(0, at);
    pending.set(parent, [...(pending.get(parent) || []), path.slice(at + 1)]);
  }
  const rows = [];
  const row = (fields) => rows.push({ expanded: false, made: true, size: null, modified: "", loading: false, failed: false, ...fields });
  function folder(path, name, level, parent, made) {
    const key = `folder:${path}`;
    const isOpen = open.has(path);
    row({ key, kind: "folder", id: path, path, name, level, parent, expanded: isOpen, made });
    if (isOpen) contents(path, level + 1, key, made);
  }
  function contents(path, level, parent, made) {
    const read = reads.get(path);
    const real = read?.folders || [];
    const known = new Set(real.map((f) => f.name));
    const folders = [...real.map((f) => ({ name: f.name, made: true })),
      ...[...new Set(pending.get(path) || [])].filter((name) => !known.has(name)).map((name) => ({ name, made: false }))].sort(byName);
    for (const f of folders) folder(joinPath(path, f.name), f.name, level, parent, f.made);
    const named = naming?.parent === path;
    if (named) row({ key: `field:${path}`, kind: "field", id: `field:${path}`, path: "", name: "", level, parent });
    const files = [...(read?.files || [])].sort(byName);
    for (const f of files) {
      const at = joinPath(path, f.name);
      row({ key: `file:${at}`, kind: "file", id: at, path: at, name: f.name, level, parent, size: f.size ?? null, modified: f.modified || "" });
    }
    const note = (name, fields = {}) => row({ key: `note:${path}`, kind: "note", id: path, path, name, level, parent, ...fields });
    const holds = folders.length || files.length || named;
    if (!made) { if (!holds) note(t("Not made yet: it is made when something is saved in it.")); }
    else if (read?.error) note(t("Could not read this folder: {error}", { error: read.error }), { failed: true });
    else if (!read?.loaded) note(t("Reading…"), { loading: true });
    else if (read.more) note(t("Only the first {n} items are shown.", { n: real.length + files.length }));
    else if (!holds) note(t("This folder is empty."));
  }
  folder("", root, 1, "", true);
  return rows;
}

// The folders to read now: each open folder drawn, made and never read.
export const unread = (rows, reads) => rows.filter((r) => r.kind === "folder" && r.expanded && r.made && !reads.has(r.id)).map((r) => r.id);
