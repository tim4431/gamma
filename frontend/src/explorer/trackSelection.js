// What the tracking dialog has chosen (docs/dev/work_folders.md "The
// tracking dialog"), as the server reads a selection: a base and the
// changes made on it, in order, so it never needs the whole directory
// loaded. The base is "file" (what the vault's .gammaignore tracks now, or
// the default without one) or "preset" (PDFs and notes outside the
// directories git ignores); the tree route (GET /api/folder-links/{id}/tree)
// reads each entry's `selected` under it. A change is {path, on}, a file's
// path or a folder's ending in "/" for every file below it, or {format,
// on} for every file of a format anywhere ("pdf", "markdown", "image", or
// "latex": LaTeX and BibTeX sources). The server's preview and Save
// (work_folders/selection.py) evaluate the same list, and add a page's
// other file to one ticked alone and the files a file vault inside follows.
// An entry is the tree route's: {path, directory, format, language,
// selected, never, locked, walked, …}. A file opened with Gamma that the
// work folder does not track is ticked as the dialog opens (tickChange).
// Pure: tests/trackSelection.test.mjs.

export const FORMATS = ["pdf", "markdown", "latex", "image"];

export const initial = (base) => ({ base, changes: [] });

// A path without the "/" a folder's may end in; a folder's as a change names
// it, with its "/" ("" for the top); the folder a path is in.
const bare = (path) => String(path || "").replace(/\/+$/, "");
const folderKey = (path) => (bare(path) ? `${bare(path)}/` : "");
const parentPath = (path) => bare(path).split("/").slice(0, -1).join("/");
const keyOf = (entry) => (entry.directory ? folderKey(entry.path) : entry.path);
const isTop = (entry) => !bare(entry.path).includes("/");

// Whether a file entry is of a format filter's.
export function ofFormat(entry, format) {
  if (entry.directory) return false;
  if (format === "latex") return entry.format === "text" && (entry.language === "latex" || entry.language === "bibtex");
  return entry.format === format;
}

const covers = (change, entry) => (change.format ? ofFormat(entry, change.format)
  : change.path.endsWith("/") ? entry.path.startsWith(change.path) : entry.path === change.path);

// A file or a folder ticked (`on`) or unticked: a folder's change takes the
// place of the earlier changes below it, a file's of the earlier ones of
// its path. A row that is never tracked, or always (locked), stays, and so
// does the top (the whole directory is toggleAll's).
export function toggle(state, entry, on) {
  const key = keyOf(entry);
  if (entry.never || entry.locked || !key) return state;
  const below = (change) => change.path !== undefined && (entry.directory ? change.path.startsWith(key) : change.path === key);
  return { ...state, changes: [...state.changes.filter((change) => !below(change)), { path: key, on }] };
}

// Every file ticked or unticked (Select all, Deselect all): one change for
// each entry at the top of `entries` (the loaded ones, which hold the whole
// top level), so every earlier change gives way.
export function toggleAll(state, entries, on) {
  const top = [...entries].filter((entry) => isTop(entry) && keyOf(entry) && !entry.never && !entry.locked);
  return { ...state, changes: top.map((entry) => ({ path: keyOf(entry), on })) };
}

// Every file of a format ticked or unticked, wherever it is: an earlier
// change of the same format gives way.
export function toggleFormat(state, format, on) {
  return { ...state, changes: [...state.changes.filter((change) => change.format !== format), { format, on }] };
}

// The file the dialog opens with ticked (`tick`, its path: a work folder's
// file opened with Gamma that the folder does not track yet) over the
// entries read so far (by path, as boxOf's): {read, state, entry, why}.
// `read` is the folder on its way the listing has but has not read yet,
// to read before anything else ("" once none is left). Then `entry` is
// the file's row and `state` ticks it unless something ticks it already
// (its base, a change, being locked); or `why` says why it is not ticked,
// `state` left as it was: "never", a row on its way that is never tracked
// (`entry` that row, its `never` the code), or "gone", the listing has no
// such file.
export function tickChange(state, entries, tick) {
  const byPath = new Map([...entries.values()].map((entry) => [bare(entry.path), entry]));
  const parts = bare(tick).split("/");
  const result = (fields) => ({ read: "", state, entry: null, why: "", ...fields });
  for (let i = 1; i < parts.length; i++) {
    const folder = byPath.get(parts.slice(0, i).join("/"));
    if (!folder?.directory) return result({ why: "gone" });
    if (folder.never) return result({ entry: folder, why: "never" });
    if (folder.walked === false) return result({ read: bare(folder.path) });
  }
  const entry = byPath.get(parts.join("/"));
  if (!entry || entry.directory) return result({ why: "gone" });
  if (entry.never) return result({ entry, why: "never" });
  return result({ entry, state: isSelected(state, entry) ? state : toggle(state, entry, true) });
}

// Whether a file entry is chosen: its base value, then each change that
// covers it, in order. A never-tracked one never is, a locked one always.
export function isSelected(state, entry) {
  if (entry.never) return false;
  if (entry.locked) return true;
  let on = !!entry.selected;
  for (const change of state.changes) if (covers(change, entry)) on = change.on;
  return on;
}

// A folder none of whose entries is loaded (`walked` false, or an empty
// one): its base value, then each change, a folder's covering it deciding,
// and a format's or one below it making it part ticked where it differs.
// One not read that its base tracks is part ticked until a change covers it
// whole: the base may leave out some of its files (the default's other
// formats, a line below it), which nobody has read.
function unread(state, folder) {
  const key = folderKey(folder.path);
  let on = !!folder.selected, mixed = folder.walked === false && on;
  for (const change of state.changes) {
    if (change.format || (change.path.startsWith(key) && change.path !== key)) {
      if (change.on !== on) mixed = true;
    } else if (change.path.endsWith("/") && key.startsWith(change.path)) {
      on = change.on;
      mixed = false;
    }
  }
  return mixed ? "mixed" : on ? "on" : "off";
}

// The folders' tallies of a state over the loaded entries, once per state
// and entries: folder path ("" the top) → {on, off, mixed}, each file below
// it counted, and each folder below it whose entries are not loaded as one.
const tallied = new WeakMap();
function tallies(state, entriesByPath) {
  const kept = tallied.get(state);
  if (kept?.entries === entriesByPath) return kept.byFolder;
  const byFolder = new Map();
  const count = (path, value, self) => {
    const parts = bare(path).split("/");
    for (let i = self ? parts.length : parts.length - 1; i >= 0; i--) {
      const folder = parts.slice(0, i).join("/");
      if (!byFolder.has(folder)) byFolder.set(folder, { on: 0, off: 0, mixed: 0 });
      byFolder.get(folder)[value] += 1;
    }
  };
  const loaded = new Set([...entriesByPath.values()].map((entry) => parentPath(entry.path)));
  for (const entry of entriesByPath.values()) {
    if (entry.never) continue;
    if (!entry.directory) count(entry.path, isSelected(state, entry) ? "on" : "off", false);
    else if (entry.walked === false && !loaded.has(bare(entry.path))) count(entry.path, unread(state, entry), true);
  }
  tallied.set(state, { entries: entriesByPath, byFolder });
  return byFolder;
}

const valueOf = (tally) => (tally.mixed || (tally.on && tally.off) ? "mixed" : tally.on ? "on" : "off");

// A row's box: {checked, indeterminate, disabled}. A file is its choice; a
// folder (`{path: "", directory: true}` the whole directory) is ticked when
// every loaded file below it is and part ticked when some are, a folder
// below it whose entries are not loaded counting by its own change or base;
// a folder with nothing loaded below it goes by those itself. A row never
// tracked, or always (locked), cannot be changed. `entriesByPath`: the
// loaded entries by path.
export function boxOf(state, node, entriesByPath) {
  const disabled = !!(node.never || node.locked);
  if (!node.directory) return { checked: isSelected(state, node), indeterminate: false, disabled };
  if (node.never) return { checked: false, indeterminate: false, disabled };
  const tally = tallies(state, entriesByPath).get(bare(node.path));
  const value = tally ? valueOf(tally) : unread(state, node);
  return { checked: value === "on", indeterminate: value === "mixed", disabled };
}

// The request's selection (the preview's and the Save's `selection`).
export const body = (state) => ({ base: state.base, changes: state.changes.map((change) => ({ ...change })) });
