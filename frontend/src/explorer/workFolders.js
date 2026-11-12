// The work folders' rules on the client (docs/dev/work_folders.md "The
// mark" and "The op rule"): which work folder a folder is in, and what a
// person may do with a work folder's file and with the folders inside a
// work folder, so the menus, the drops and the open page offer only what
// the op path takes. A work folder's root folder W carries the mark
// ({pull, library}, libraryUtils.libraryTree's `mark`); W(f), the work
// folder a folder is in, is the nearest folder on f's chain, f included,
// that carries it. The twin of gamma/work_folders/marks.py (treeOf, workOf,
// markOf, filingTargets) and filing.py (rootPatchAllowed,
// creationOf, mayTrash, mayRestore, treeOpAllowed, the words);
// tests/shared/workfiling.json holds both to the same answers. Pure:
// tests/workFolders.test.mjs.
import { STAMPS, follows as followsMark, workFolderOf } from "../documents/library.js";
import { inPlace, isPdf, pageDocument } from "../documents/pageDocument.js";
import { EMPTY_TREE, addToFolder, folderSubtree, folderTargets } from "../library/libraryUtils.js";
import { T, t } from "../shared/i18n/i18n.js";

// The folder tree's pseudo-page: an op names it as a top folder's parent.
const TOP = "folders";

// The words a person reads, by kind (filing.WORDS, the server's English
// word for word, so a refusal the server sends translates as these do).
export const WORDS = {
  filed: T("A work folder's file is filed by its directory: add it to the library to file it in library folders as well."),
  named: T("A work folder's file is named by its file: rename it on disk."),
  trash: T("A work folder's file leaves when its file is deleted or untracked: use Choose files…."),
  restore: T("It comes back when its file is tracked again."),
  directories: T("A work folder's directories follow the disk."),
  remove: T("Remove the synced vault to delete its folder."),
  document: T("That file's PDF is read where it is: it cannot be changed in Gamma."),
};
export const refusalText = (kind) => (Object.hasOwn(WORDS, kind) ? t(WORDS[kind]) : "");

// ---------- the folder tree ----------

// The folder tree as the rules read it, from the listing's
// (libraryUtils.libraryTree): `parent` (id → parent id, "" at the top),
// `marks` (W → {pull, library}), `work` (id → W(f), "" for none), `order`
// (ids, parents first) and `lib`, the listing's tree. Made once per listing
// tree; every function here takes either.
const built = new WeakMap();
export function treeOf(libTree) {
  if (libTree?.work instanceof Map) return libTree;
  const lib = libTree || EMPTY_TREE;
  let tree = built.get(lib);
  if (tree) return tree;
  const parent = new Map(), marks = new Map(), work = new Map(), order = [];
  for (const [id, folder] of lib.folders) {
    parent.set(id, folder.parent);
    order.push(id);
    if (folder.mark) marks.set(id, folder.mark);
    work.set(id, folder.mark ? id : work.get(folder.parent) || "");
  }
  tree = { parent, marks, work, order, lib };
  built.set(lib, tree);
  return tree;
}

// W(f): the work folder `folderId` is in, "" for none or an unknown id.
export const workOf = (tree, folderId) => treeOf(tree).work.get(folderId) || "";
// W's mark, {pull, library}; null for a folder that carries none.
export const markOf = (tree, w) => treeOf(tree).marks.get(w) || null;
// The folders a person may file a page in: every folder in no work folder,
// in tree order.
export function filingTargets(tree) {
  const tr = treeOf(tree);
  return tr.order.filter((id) => !tr.work.get(id));
}

// ---------- a page ----------

const folderList = (value) => (Array.isArray(value) ? value.filter((id) => typeof id === "string") : []);

// What the rules read of a page: a row of App's page list
// (libraryUtils.pageBlocksOf) or a block with its properties.
function facts(page) {
  if (page && "_workFolder" in page) {
    return { work: page._workFolder || "", doc: page._doc || null, hasDoc: !!page._doc, library: page._library === true,
      folders: page._folders || [] };
  }
  const props = page?.properties || {};
  return { work: workFolderOf(page), doc: pageDocument(page), hasDoc: props.doc != null, library: props.library === true,
    folders: folderList(props.folders) };
}

// Whether the page is a work folder's file: a row (`_workFolder`) or a block.
export const isWorkFile = (page) => !!facts(page).work;

// Whether a person's renames, moves, trash and restore of the file reach
// the disk: a note (no document) of a work folder that pulls.
export function follows(page, tree) {
  const { work, hasDoc } = facts(page);
  return !!work && !hasDoc && markOf(tree, work)?.pull === true;
}

const refused = (kind) => ({ folders: null, why: refusalText(kind) });

// What a person may do with a page (`page`: a row or a block), as the menus
// and drops ask it: {rename, refileTargets() (the folders Move to folder
// offers), removableFolders (those it may be taken out of), trash, restore,
// moveIntoPage, duplicate, addToLibrary, removeFromLibrary, saveCopy (Save
// a copy to library), refile(folder) ({folders, why}: its folders once filed
// in `folder` by a drop, or null and the words that say why not)}. A
// library page may do it all but what only a file has; a work folder's file
// is filed by its directory, and in library folders too while it is in the
// library, and is named by its file unless it is in the library (its title
// is then the paper's) or follows. A copy (`duplicate`) is made of a page
// with stored bytes or none: not of one reading its PDF from a file on this
// computer (the copy's address would stop answering when the file changes),
// nor of a file's PDF, whose Save a copy to library is its copy.
export function fileRules(page, tree) {
  const tr = treeOf(tree);
  const { work, doc, hasDoc, library, folders } = facts(page);
  if (!work) {
    return {
      rename: true, refileTargets: () => filingTargets(tr), removableFolders: folders, trash: true, restore: true,
      moveIntoPage: true, duplicate: !inPlace(doc), addToLibrary: false, removeFromLibrary: false, saveCopy: false,
      refile: (folder) => (workOf(tr, folder) ? refused("directories") : { folders: addToFolder(tr.lib, folders, folder), why: "" }),
    };
  }
  const follow = follows(page, tr);
  const dir = folders.find((id) => workOf(tr, id) === work);
  const others = folders.filter((id) => !workOf(tr, id));
  return {
    rename: library || follow,
    refileTargets: () => (library ? filingTargets(tr) : []),
    removableFolders: library ? others : [],
    trash: follow,
    restore: follow,
    moveIntoPage: false,
    duplicate: !hasDoc,
    addToLibrary: !library,
    removeFromLibrary: library,
    saveCopy: isPdf(doc) && !library,
    refile: (folder) => {
      const into = workOf(tr, folder);
      if (into) return into === work && follow ? { folders: [folder, ...others], why: "" } : refused("filed");
      return library && dir ? { folders: [dir, ...addToFolder(tr.lib, others, folder)], why: "" } : refused("filed");
    },
  };
}

// What a person may do with a folder (`folderId`, "" for the library's
// top): {rename, move, moveTargets (the folders it may go into, the top
// aside: the library's, none of them below it; the folder menu's Move to),
// delete, newSubfolder, newPage, chooseFiles (the folder menu's Choose
// files…)}. Inside a work folder the folder tree is the disk's: W may be
// renamed and moved out of every work folder, never deleted; its
// directories are neither renamed, moved nor deleted, and no folder is made
// in any of them. A page is made in a work folder that pulls (a note, which
// pull writes), and Choose files… is W's.
export function folderRules(tree, folderId) {
  const tr = treeOf(tree);
  const w = workOf(tr, folderId);
  const pulls = !!markOf(tr, w)?.pull;
  if (w && w !== folderId) {
    return { rename: false, move: false, moveTargets: [], delete: false, newSubfolder: false, newPage: pulls, chooseFiles: false };
  }
  const known = tr.parent.has(folderId);
  const inside = known ? folderSubtree(tr.lib, folderId) : new Set();
  return {
    rename: known,
    move: known,
    moveTargets: known ? folderTargets(tr.lib, [folderId], filingTargets(tr)) : [],
    delete: known && !w && ![...tr.marks.keys()].some((root) => inside.has(root)),
    newSubfolder: !w,
    newPage: w ? pulls : true,
    chooseFiles: !!w,
  };
}

// ---------- the op rule's twin (filing.py) ----------
//
// Each answers a verdict: {allowed: true, value} with what is stored, or
// {allowed: false, kind, why}, the kind of words (WORDS) and the words.
// `writer` is "person" (a tab, a share editor), "vault" or "mirror".

class Refused extends Error {
  constructor(kind) {
    super(kind);
    this.kind = kind;
  }
}

function verdict(run) {
  try {
    return { allowed: true, value: run() };
  } catch (err) {
    if (!(err instanceof Refused)) throw err;
    return { allowed: false, kind: err.kind, why: refusalText(err.kind) };
  }
}

const asBlock = (properties) => ({ properties });
const isObject = (value) => !!value && typeof value === "object" && !Array.isArray(value);

// Whether two JSON values are equal, objects whatever their keys' order.
function same(a, b) {
  if (a === b) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object" || Array.isArray(a) !== Array.isArray(b)) return false;
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => Object.hasOwn(b, key) && same(a[key], b[key]));
}

// The properties after a patch, a null removing its key.
function merged(before, patch) {
  const out = { ...before };
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete out[key];
    else out[key] = value;
  }
  return out;
}

// S1–S4 on the properties `after` a write, for what it `touched`.
function shape(tree, after, touched, targetIsFile) {
  const w = workFolderOf(asBlock(after));
  const stamp = touched.work_folder ?? null;
  if (stamp !== null && (typeof stamp !== "string" || !stamp || (tree.parent.has(stamp) && !markOf(tree, stamp)))) {
    throw new Refused("filed");
  }
  const name = touched.file_name ?? null;
  if (name !== null && (typeof name !== "string" || !w)) throw new Refused("named");
  const library = touched.library ?? null;
  if (library !== null && (library !== true || !w)) throw new Refused("filed");
  if ((touched.parent_page ?? null) !== null && (w || targetIsFile)) throw new Refused("filed");
  if ("folders" in touched || (stamp !== null && w)) {
    const folders = folderList(after.folders);
    if (w) {
      if (!folders.length || workOf(tree, folders[0]) !== w || folders.slice(1).some((id) => workOf(tree, id))) {
        throw new Refused("filed");
      }
      if (folders.length > 1 && after.library !== true) throw new Refused("filed");
    } else if (folders.some((id) => workOf(tree, id))) {
      throw new Refused("directories");
    }
  }
}

function rootPatch(tree, before, patch, content, writer, targetIsFile) {
  if (writer === "mirror") return patch;
  const was = isObject(before) ? before : {};
  const out = patch ? { ...patch } : {};
  if ("library" in out && out.library === null && workFolderOf(asBlock(merged(was, out)))) {
    const have = folderList("folders" in out ? out.folders : was.folders);
    if (have.length > 1) out.folders = have.slice(0, 1);
  }
  if (writer === "person") {
    for (const [key, kind] of [["work_folder", "filed"], ["file_name", "named"]]) {
      if (key in out && !same(out[key], was[key] ?? null)) throw new Refused(kind);
    }
    const w = workFolderOf(asBlock(was));
    if (w) {
      const follow = followsMark(asBlock(was), markOf(tree, w));
      if ("doc" in out && !same(out.doc, was.doc ?? null)) throw new Refused("document");
      const libraryAfter = "library" in out ? out.library === true : was.library === true;
      if ("folders" in out) {
        const old = folderList(was.folders), now = folderList(out.folders);
        if ((now[0] ?? null) !== (old[0] ?? null) && !(follow && now.length && workOf(tree, now[0]) === w)) {
          throw new Refused("filed");
        }
        if (now.length > 1 && (!libraryAfter || now.slice(1).some((id) => workOf(tree, id)))) throw new Refused("filed");
      }
      if (content != null && !(was.library === true || libraryAfter || follow)) throw new Refused("named");
    }
  }
  shape(tree, merged(was, out), out, targetIsFile);
  return patch != null ? out : null;
}

function creation(tree, props, writer, targetIsFile) {
  if (writer === "mirror") return props;
  const out = { ...(props || {}) };
  if (writer === "person") {
    for (const key of STAMPS) delete out[key];
    const folders = folderList(out.folders);
    if (folders.some((id) => workOf(tree, id))) {
      const w = workOf(tree, folders[0]);
      const mark = w ? markOf(tree, w) : null;
      if (!mark || folders.slice(1).some((id) => workOf(tree, id)) || out.doc != null || !mark.pull) {
        throw new Refused("directories");
      }
      if (out.parent_page != null) throw new Refused("filed");
      out.work_folder = w;
      if (mark.library) {
        const targets = new Set(filingTargets(tree));
        out.library = true;
        out.folders = [...folders.slice(0, 1), ...folders.slice(1).filter((id) => targets.has(id))];
      } else {
        out.folders = folders.slice(0, 1);
      }
    }
  }
  shape(tree, out, out, targetIsFile);
  return out;
}

function trashed(tree, props, writer) {
  const w = workFolderOf(asBlock(props));
  if (writer === "person" && w && !followsMark(asBlock(props), markOf(tree, w))) throw new Refused("trash");
}

function restored(tree, props, writer) {
  if (writer === "mirror") return props;
  const w = workFolderOf(asBlock(props));
  const mark = w ? markOf(tree, w) : null;
  if (w && mark) {
    if (writer === "person" && (props.deleted_by_vault || !followsMark(asBlock(props), mark))) throw new Refused("restore");
    return props;
  }
  const out = w ? Object.fromEntries(Object.entries(props).filter(([key]) => !STAMPS.includes(key))) : { ...props };
  const folders = folderList(out.folders);
  if (folders.some((id) => workOf(tree, id))) out.folders = folders.filter((id) => !workOf(tree, id));
  return out;
}

function treeOp(tree, op, writer) {
  if (writer !== "person") return;
  const folder = op?.id;
  if (isObject(op?.props) && "vault" in op.props) throw new Refused("directories");
  const w = workOf(tree, folder);
  const parent = op?.parent === TOP ? "" : op?.parent;
  if (op?.op === "set") {
    if ((op.content ?? null) !== null && w && w !== folder) throw new Refused("directories");
  } else if (op?.op === "insert") {
    if (workOf(tree, parent)) throw new Refused("directories");
  } else if (op?.op === "move") {
    if (tree.parent.has(folder) && tree.parent.get(folder) === parent) return;
    if ((w && w !== folder) || workOf(tree, parent)) throw new Refused("directories");
  } else if (op?.op === "delete") {
    if (w) throw new Refused(w === folder ? "remove" : "directories");
    const below = folderSubtree(tree.lib, folder);
    if ([...tree.marks.keys()].some((root) => below.has(root))) throw new Refused("remove");
  }
}

// A page root's `set` (filing.root_patch): `before` its properties, `patch`
// the op's (a null removes a key; null for none), `content` its new title
// (null for none), `targetIsFile` whether the `parent_page` it sets names a
// file. The value is the patch as stored: clearing `library` on a file
// drops its library folders in the same patch.
export const rootPatchAllowed = (tree, before, patch, content = null, writer = "person", { targetIsFile = false } = {}) =>
  verdict(() => rootPatch(treeOf(tree), before, patch, content, writer, targetIsFile));
// A new page's properties (filing.creation): a person's stamps dropped, and
// a note filed in a work folder that pulls stamped as its file.
export const creationOf = (tree, props, writer = "person", { targetIsFile = false } = {}) =>
  verdict(() => creation(treeOf(tree), props, writer, targetIsFile));
// Sending the page to Recently deleted (filing.may_trash).
export const mayTrash = (tree, props, writer = "person") => verdict(() => trashed(treeOf(tree), props, writer));
// Bringing it back (filing.may_restore): the value is its properties, a
// stale file's stamps dropped.
export const mayRestore = (tree, props, writer = "person") => verdict(() => restored(treeOf(tree), props, writer));
// An op on the folders pseudo-page, {op, id, parent?, content?, props?}
// (filing.tree_op).
export const treeOpAllowed = (tree, op, writer = "person") => verdict(() => treeOp(treeOf(tree), op, writer));
