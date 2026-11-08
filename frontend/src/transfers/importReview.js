import { t, tn } from "../shared/i18n/i18n.js";
export const IMPORT_FILTERS = [
  ["all", t("All")], ["missing", t("Missing")], ["warnings", t("Warnings")], ["selected", t("Selected")],
];

export const itemIds = page => page.selection_ids || [];
export const itemSelected = (page, selected) => itemIds(page).every(id => selected.has(id));
export const allItemIds = pages => [...new Set(pages.flatMap(itemIds))];
export function filterImportPages(pages, filter, selected) {
  return pages.filter(page => filter === "all" || (filter === "missing" && page.missing)
    || (filter === "warnings" && page.warnings?.length) || (filter === "selected" && itemSelected(page, selected)));
}
export function selectItems(selected, ids, checked) {
  const next = new Set(selected);
  for (const id of ids) { if (checked) next.add(id); else next.delete(id); }
  return next;
}
// The tree a review column shows: the archive's entries by their paths in
// the zip, or (`library`) the destination pages by their folders — each a
// list of names from the top, as the server reports them (a name may hold
// a "/", so they are never split). There a sub-page (`parent`, the id of
// another of `items`) is a file of its parent's `inside` node, in the
// order of `items`, rather than one of its folders' files; one whose parent
// is not among them, or whose chain of parents loops, is filed by its
// folders. With `selected` (the review, before the import) a ticked
// sub-page whose parent will not be imported — a new page left unticked —
// is filed where the import puts it then, by its directory
// (`folders_alone`); a page already in the library is always its parent.
// `all`: every page of the review, of which `items` are the ones a filter
// shows — a parent the filter hides still decides where its pages go.
export function buildImportTree(items, library = false, selected = null, all = items) {
  const root = { folders: new Map(), files: [] };
  const at = names => {
    let node = root;
    for (const part of names.filter(Boolean)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] });
      node = node.folders.get(part);
    }
    return node;
  };
  const byId = new Map();
  for (const item of library ? all : []) if (item.id && !byId.has(item.id)) byId.set(item.id, item);
  const drawn = new Set(items);
  const leftAlone = (item, up) => Boolean(selected && item.folders_alone && up.action !== "skip"
    && itemSelected(item, selected) && !itemSelected(up, selected));
  const parentOf = item => {
    const up = item.parent && item.parent !== item.id ? byId.get(item.parent) : undefined;
    return up && drawn.has(up) && !leftAlone(item, up) ? up : undefined;
  };
  const nested = item => {
    const seen = new Set([item]);
    for (let up = parentOf(item); up; up = parentOf(up)) {
      if (seen.has(up)) return false;
      seen.add(up);
    }
    return Boolean(parentOf(item));
  };
  const inside = new Map();
  for (const item of library ? items : []) {
    if (!nested(item)) continue;
    if (!inside.has(item.parent)) inside.set(item.parent, []);
    inside.get(item.parent).push(item);
  }
  const pageRow = item => ({ ...item, name: item.title,
    ...(inside.has(item.id) ? { inside: { folders: new Map(), files: inside.get(item.id).map(pageRow) } } : {}) });
  for (const item of items) {
    if (library) {
      if (nested(item)) continue;
      const up = item.parent && byId.get(item.parent);
      const folders = up && leftAlone(item, up) ? item.folders_alone : item.folders;
      for (const folder of folders?.length ? folders : [[]]) at(folder).files.push(pageRow(item));
    } else if (item.directory) at(item.path.split("/"));
    else {
      const parts = item.path.split("/");
      const name = parts.pop();
      at(parts).files.push({ ...item, name });
    }
  }
  return root;
}
// A column's folders by name, numbers by value: Zotero's files/999
// before files/1000.
const names = new Intl.Collator(undefined, { numeric: true });
export const byName = ([a], [b]) => names.compare(a, b);
// A tree's files in the order ImportTree shows them (each folder's folders
// by name, then its own files, each followed by the pages inside it) until
// `visit(file, path, at)` — `path` the folder names from the top, `at` the
// file's place in its folder's files, then in each `inside` down to it —
// returns true; returns whether one did.
function walkTree(node, visit, path = [], above = []) {
  for (const [name, child] of [...node.folders].sort(byName)) {
    if (walkTree(child, visit, [...path, name])) return true;
  }
  return node.files.some((file, index) => visit(file, path, [...above, index])
    || Boolean(file.inside && walkTree(file.inside, visit, path, [...above, index])));
}
// The first file of `node` whose `rowKey` is one of `keys`, as
// `{ key, path, index }` (what ImportTree opens and scrolls to), `sub` its
// places in the `inside` lists below that file for a sub-page; or null.
export function firstRow(node, keys, rowKey) {
  let found = null;
  walkTree(node, (file, path, [index, ...sub]) => {
    const key = rowKey(file);
    if (keys.has(key)) found = { key, path, index, ...(sub.length ? { sub } : {}) };
    return Boolean(found);
  });
  return found;
}

// How the two columns of a review answer each other: a destination page
// names the upload's members it comes from (`source_paths`, its own file
// first), exactly as the source column names them.
const pageSources = page => page.source_paths || [];
// A library row's key: the items it imports, or — a report's rows, which
// a Zotero import gives none — the page itself.
export const pageKey = page => itemIds(page).join("\n") || `${page.kind || "page"}:${page.id}`;
// Which pages each file of the upload feeds (`toPages`, by path) and which
// listed files each page comes from (`toFiles`, by pageKey), as sets.
export function importLinks(entries, pages) {
  const listed = new Set(entries.filter(entry => !entry.directory).map(entry => entry.path));
  const toPages = new Map(), toFiles = new Map();
  const add = (map, key, value) => { if (!map.has(key)) map.set(key, new Set()); map.get(key).add(value); };
  for (const page of pages) {
    const key = pageKey(page);
    for (const path of pageSources(page)) {
      if (listed.has(path)) { add(toPages, path, key); add(toFiles, key, path); }
    }
  }
  return { toPages, toFiles };
}
// The destination pages in the source column's order: each by the first
// of its files the column lists; pages it lists none of keep their order,
// after the rest. `tree` is the source column's tree.
export function orderLikeSource(pages, tree) {
  const order = new Map();
  walkTree(tree, file => { if (!order.has(file.path)) order.set(file.path, order.size); });
  const rank = page => {
    for (const path of pageSources(page)) if (order.has(path)) return order.get(path);
    return order.size;
  };
  return pages.map((page, index) => ({ page, index, at: rank(page) }))
    .sort((a, b) => a.at - b.at || a.index - b.index).map(({ page }) => page);
}

// The items a row's checkbox covers: its own and those of the pages inside
// it, as a folder's covers its contents. Unticking a page leaves the pages
// inside it out too: imported without it, they would be filed by their
// directory rather than under it, where the column shows them.
export function rowItemIds(file) {
  return file.inside ? [...new Set([...itemIds(file), ...treeItemIds(file.inside)])] : itemIds(file);
}
// The items of a tree's files, its folders' and the pages inside them included.
export function treeItemIds(node) {
  return [...new Set([...node.files.flatMap(rowItemIds), ...[...node.folders.values()].flatMap(treeItemIds)])];
}
// The number of files in a tree, its folders' and the pages inside them included.
export function treeSize(node) {
  let n = node.files.length;
  for (const file of node.files) if (file.inside) n += treeSize(file.inside);
  for (const child of node.folders.values()) n += treeSize(child);
  return n;
}
export function resultPages(data) {
  const unique = new Map();
  for (const page of data.pages || []) {
    const key = `${page.kind || "page"}:${page.id}`;
    const prior = unique.get(key);
    unique.set(key, { ...page, folders: page.folders || [],
      source_paths: [...new Set([...(prior?.source_paths || []), ...pageSources(page)])],
      action: page.created || prior?.action === "create" ? "create" : page.action || "merge" });
  }
  return [...unique.values()];
}
export const importWarnings = data => [...(data.skipped || []), ...(data.warnings || [])];
// Whether a review's job failed on the file itself: the source's refusal
// (nothing it reads, a damaged archive), which sending the file again
// cannot help. A review the server's restart cut short (gamma/jobs.py
// INTERRUPTED) or a bug ("unexpected error …", jobs._message) may get past
// it on another try.
export const reviewRefused = error => !/^(interrupted:|unexpected error)/.test(error || "");
// An import report in one line; a count the source left at 0 (or never
// reports) is left out, the new pages excepted.
export function importSummary(data) {
  const created = data.pages_created ?? data.pages_added ?? (data.block_id ? 1 : 0);
  const stored = data.pdfs_stored || data.assets_stored || data.uploads_added;
  const warnings = importWarnings(data).length;
  return [
    tn("{n} new page", "{n} new pages", created),
    data.pages_merged ? tn("{n} updated", "{n} updated", data.pages_merged) : "",
    data.pages_skipped ? tn("{n} already in library", "{n} already in library", data.pages_skipped) : "",
    stored ? tn("{n} file stored", "{n} files stored", stored) : "",
    data.annotations_imported ? tn("{n} annotation", "{n} annotations", data.annotations_imported) : "",
    data.notes_imported ? tn("{n} note", "{n} notes", data.notes_imported) : "",
    data.attachments_imported ? tn("{n} attachment", "{n} attachments", data.attachments_imported) : "",
    data.chats_added ? tn("{n} chat", "{n} chats", data.chats_added) : "",
    data.strokes_imported ? tn("{n} stroke", "{n} strokes", data.strokes_imported) : "",
    data.text_boxes_imported ? tn("{n} text box", "{n} text boxes", data.text_boxes_imported) : "",
    data.pictures_imported ? tn("{n} picture", "{n} pictures", data.pictures_imported) : "",
    data.recordings_imported ? tn("{n} recording", "{n} recordings", data.recordings_imported) : "",
    warnings ? tn("{n} warning", "{n} warnings", warnings) : "",
  ].filter(Boolean).join(" · ");
}
