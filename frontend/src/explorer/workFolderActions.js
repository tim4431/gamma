// What App's menus, drops, open page and list view ask of the work-folder
// rules (explorer/workFolders.js) about the pages and folders in hand
// (docs/dev/work_folders.md "The op rule", "The folder's list view"): which
// of them a rule refuses, what filing leaves out, the rows a page menu
// offers, a copy's properties, the requests of Add to library, Remove from
// library and Save a copy, and the rows of a directory's list. `pages` is
// App's page rows by id (libraryUtils.pageBlocksOf), `tree` the folder tree
// as the rules read it (treeOf). The commands over them are
// useWorkFolderActions.jsx's. Pure: tests/workFolderActions.test.mjs.
import { STAMPS, workFolderOf } from "../documents/library.js";
import { folderChain } from "../library/libraryUtils.js";
import { t, tn } from "../shared/i18n/i18n.js";
import { fileRules, folderRules, isWorkFile, refusalText, treeOf, workOf } from "./workFolders.js";

// The row of `id` when it is a work folder's file, else null.
export function fileRow(pages, id) {
  const b = pages.get(id);
  return b && isWorkFile(b) ? b : null;
}

// Whether `ids` name a work folder's file whose rules (fileRules) `may`
// refuses.
export const refusesFiles = (pages, ids, may, tree) => ids.some((id) => {
  const b = fileRow(pages, id);
  return !!b && !may(fileRules(b, tree));
});

// A work folder's directories stay where the disk has them, and no folder
// goes into a work folder (folderRules): whether a move of `ids` under
// `parent` ("" the top) is refused.
export const foldersMoveRefused = (tree, ids, parent) =>
  ids.length > 0 && (!!(parent && workOf(tree, parent)) || ids.some((id) => !folderRules(tree, id).move));

// The open page as a work folder's file: its work folder (`work`, "" for
// none), what a person may do with it (`rules`: fileRules of its row, else
// of the page read), whether its title is the file's, which only the disk
// renames (`titleLocked`), and whether it is in the library (`inLibrary`).
export function openFile(row, block, tree) {
  const work = workFolderOf(block);
  const rules = fileRules(row || block, tree);
  return { work, rules, titleLocked: !!work && !rules.rename, inLibrary: block?.properties?.library === true };
}

// A copy's properties (`properties`, the page's own less what a copy never
// takes): a work folder's note's copy is a library page, without the
// vault's stamps, filed in the library folders it was in.
export function libraryCopy(properties, tree) {
  if (!properties.work_folder) return properties;
  const tr = treeOf(tree);
  const out = Object.fromEntries(Object.entries(properties).filter(([key]) => !STAMPS.includes(key)));
  const folders = (properties.folders || []).filter((f) => tr.lib.folders.has(f) && !workOf(tr, f));
  if (folders.length) out.folders = folders;
  else delete out.folders;
  return out;
}

// A page inside another is filed where its top is (its own folders count
// for nothing), and a work folder's file not in the library by its
// directory alone, so filing leaves both out: {ids, left, why}, the pages
// of `pageIds` filing takes, what the status adds about the ones left out
// ("" for none) and, with none to take, why ("" otherwise).
export function filingOf(pages, pageIds) {
  const tops = pageIds.filter((id) => !pages.get(id)?._parentPage);
  const ids = tops.filter((id) => { const b = pages.get(id); return !b || !isWorkFile(b) || b._library; });
  if (!ids.length) {
    return { ids, left: "", why: tops.length ? refusalText("filed") : t("A page inside another page is filed with it.") };
  }
  const inside = pageIds.length - tops.length, files = tops.length - ids.length;
  const left = [inside ? tn("Left out {n} page inside another page, filed with its top.",
    "Left out {n} pages inside other pages, filed with their tops.", inside) : "",
  files ? tn("Left out {n} work folder's file, filed by its directory.", "Left out {n} work folders' files, filed by their directories.", files) : "",
  ].map((s) => (s ? ` ${s}` : "")).join("");
  return { ids, left, why: "" };
}

// Pages dropped on a work folder's directory `folder`: only its notes that
// follow move there (fileRules refile), as pull then moves their files.
// {into, why}: each page's folders once moved (id → folders), or null and
// why not.
export function directoryMoves(pages, pageIds, folder, tree) {
  const moves = pageIds.map((id) => [id, pages.get(id) ? fileRules(pages.get(id), tree).refile(folder) : null]);
  const refused = moves.find(([, move]) => !move?.folders);
  if (refused) return { into: null, why: refused[1]?.why || refusalText("directories") };
  return { into: new Map(moves.map(([id, move]) => [id, move.folders])), why: "" };
}

// What a page menu offers for the pages it acts on (`acted`, their rows by
// `ids`, a missing one undefined): the work folders' files among them
// (`files`, `allFiles`, `anyFile`) and whether a rule holds for each file
// (`ruled(rule)`), the library pages Move to folder files (`filable`) and
// how many it leaves out as inside a page (`leftOut`), the folders they may
// be taken out of (`ownFolders`: never a file's directory) and the ids
// Duplicate copies (`copied`).
export function pagesMenu(acted, ids, pages, tree) {
  const files = acted.filter((b) => b && isWorkFile(b));
  const filable = acted.filter((b) => b && !b._parentPage && (!isWorkFile(b) || b._library));
  return {
    files,
    allFiles: files.length === ids.length,
    anyFile: files.length > 0,
    ruled: (rule) => files.every((b) => fileRules(b, tree)[rule]),
    filable,
    leftOut: ids.length - acted.filter((b) => b && !b._parentPage).length,
    ownFolders: [...new Set(filable.flatMap((b) => b._folders || []))].filter((f) => !workOf(tree, f)),
    copied: ids.filter((id) => !pages.get(id) || fileRules(pages.get(id), tree).duplicate),
  };
}

// The library rows a menu has for work folders' files (`files`, rows):
// {ids, add, remove, copy}, Add to library… while none is in it, Remove
// from library while all are, Save a copy to library… for their PDFs.
export function libraryRows(files, tree) {
  const rules = files.map((b) => fileRules(b, tree));
  return {
    ids: files.map((b) => b._pageId),
    add: rules.every((r) => r.addToLibrary),
    remove: rules.every((r) => r.removeFromLibrary),
    copy: rules.every((r) => r.saveCopy),
  };
}

// The request of one file's Add to library (`how` "add", into `folder`),
// Remove from library ("remove") or Save a copy ("copy"): POST or DELETE
// /pages/{id}/library, POST /pages/{id}/library-copy. {path, init}, the
// path under the API.
export function libraryRequest(id, how, folder = "") {
  const path = `/pages/${encodeURIComponent(id)}/${how === "copy" ? "library-copy" : "library"}`;
  return {
    path,
    init: how === "remove" ? { method: "DELETE" }
      : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(folder ? { folder } : {}) },
  };
}

// The Background tasks row of `n` files' batch.
export const libraryTask = (how, n) => (how === "remove" ? t("Remove {n} files from the library", { n })
  : how === "copy" ? t("Save copies of {n} files to the library", { n }) : t("Add {n} files to the library", { n }));

// What the status says once `done` files went, `existed` of a copy's
// answered with the library page that owns their PDF already.
export function librarySaid(how, done, existed = 0) {
  if (how === "remove") return tn("Removed {n} file from the library.", "Removed {n} files from the library.", done);
  if (how !== "copy") return tn("Added {n} file to the library.", "Added {n} files to the library.", done);
  return [tn("Saved a copy of {n} file to the library.", "Saved copies of {n} files to the library.", done - existed),
    existed ? tn("{n} was in the library already.", "{n} were in the library already.", existed) : ""].filter(Boolean).join(" ");
}

// The folders inside a work folder (`inWork`) and the files each holds,
// itself and below (`counts`, id → n), from the listing's `work_counts`
// (the files filed right in each directory), which a folder's count is in
// place of its pages'.
export function workCounts(tree) {
  const tr = treeOf(tree);
  const inWork = new Set(tr.order.filter((id) => tr.work.get(id)));
  const counts = new Map();
  for (const [dir, n] of Object.entries(tr.lib.workCounts || {})) {
    if (!inWork.has(dir)) continue;
    for (const f of folderChain(tr.lib, dir)) if (inWork.has(f)) counts.set(f, (counts.get(f) || 0) + n);
  }
  return { inWork, counts };
}

// ---------- the list view (explorer/FolderList.jsx) ----------

// The name a file is listed by: its file's, else (a note made in Gamma whose
// file the vault has not written yet) its title.
export const fileName = (b) => b._fileName || b.content || t("Untitled");

// The directories of the level shown (`ids`), by name, each with the files
// below it (`meta`, App's folder rollup): [{id, name, count}].
export const listDirs = (ids, tree, meta) => ids
  .map((id) => ({ id, name: treeOf(tree).lib.folders.get(id).name, count: meta[id]?.count || 0 }))
  .sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));

// The page rows of a directory's loaded files (`rows`, useFolderRows's),
// those App knows, each with the read's `open_comments`, which the filter
// box's text (`query`) keeps by name.
export function listRows(rows, pages, query) {
  const q = query.trim().toLowerCase();
  const known = rows.filter((r) => pages.has(r.id)).map((r) => ({ ...pages.get(r.id), open_comments: r.open_comments || 0 }));
  return q ? known.filter((b) => fileName(b).toLowerCase().includes(q)) : known;
}

// What a list with no row says: "choosing" while Choose files… is open,
// "nomatch" when the filter box's text keeps none of the files, "untracked"
// at the work folder's top (nothing tracked yet), "none" in a directory.
export function emptyKind({ choosing = false, query = "", loaded = 0, atTop = false }) {
  if (choosing) return "choosing";
  if (query.trim() && loaded) return "nomatch";
  if (atTop) return "untracked";
  return query.trim() ? "nomatch" : "none";
}
