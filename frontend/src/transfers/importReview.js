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
