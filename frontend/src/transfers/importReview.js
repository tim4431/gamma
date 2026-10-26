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
// a "/", so they are never split).
export function buildImportTree(items, library = false) {
  const root = { folders: new Map(), files: [] };
  const at = names => {
    let node = root;
    for (const part of names.filter(Boolean)) {
      if (!node.folders.has(part)) node.folders.set(part, { folders: new Map(), files: [] });
      node = node.folders.get(part);
    }
    return node;
  };
  for (const item of items) {
    if (library) {
      for (const folder of item.folders?.length ? item.folders : [[]]) at(folder).files.push({ ...item, name: item.title });
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
// by name, then its own files) until `visit(file, path, index)` — `path`
// the folder names from the top, `index` the file's place in its folder —
// returns true; returns whether one did.
export function walkTree(node, visit, path = []) {
  for (const [name, child] of [...node.folders].sort(byName)) {
    if (walkTree(child, visit, [...path, name])) return true;
  }
  return node.files.some((file, index) => visit(file, path, index));
}
// The first file of `node` whose `rowKey` is one of `keys`, as
// `{ key, path, index }` (what ImportTree opens and scrolls to), or null.
export function firstRow(node, keys, rowKey) {
  let found = null;
  walkTree(node, (file, path, index) => {
    const key = rowKey(file);
    if (keys.has(key)) found = { key, path, index };
    return Boolean(found);
  });
  return found;
}

// How the two columns of a review answer each other: a destination page
// names the upload's members it comes from (`source_paths`, its own file
// first), exactly as the source column names them.
export const pageSources = page => page.source_paths || [];
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

export function treeItemIds(node) {
  return [...new Set([...node.files.flatMap(itemIds), ...[...node.folders.values()].flatMap(treeItemIds)])];
}
// The number of files in a tree, its folders' included.
export function treeSize(node) {
  let n = node.files.length;
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
