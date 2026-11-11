import { folderRules, treeOf, workOf } from "../explorer/workFolders.js";
import { EMPTY_TREE, inFolder } from "./libraryUtils.js";

// What the person may do with the LIBRARY they are looking at — the home
// listing, its folders and labels — as opposed to a page's notes (that is
// App's `readOnly`). One object derived from how the library was reached,
// consulted by every affordance of the home library: the "New page" / "New
// folder" rows, drags and drops, the context menus, pins, the recents and
// pinned strips, and how far up the folder browser may go. Nothing in the
// listing checks a role or a share token itself.
//
//   root        the folder the view is confined to ("" = the whole library):
//               a folder share's folder — the browser never climbs above it
//   browse      whether there is a library to list at all (a page share has none)
//   organize    create, rename, move, duplicate, delete, label, file — every
//               write to the library's structure, incl. sharing a folder
//   pin         pins and the pinned strip (page pins are a block write)
//   history     the recents strip and the account's view history
//   contains    whether a folder (an id) is inside `root`
//   clamp       that folder when it is, else `root`
//   workFolder  the work folder a folder (an id) is in, its root folder's
//               id, "" for none (docs/dev/work_folders.md)
//   organizeIn  what may be done with a folder ("" the top), as
//               explorer/workFolders.js folderRules says and `organize`
//               allows: {rename, move, moveTargets, delete, newSubfolder,
//               newPage, chooseFiles}; inside a work folder its directories
//               follow the disk
//
// A workspace viewer browses everything but organizes nothing; a share
// visitor browses the shared folder only. `tree` is the listing's folder
// tree (libraryUtils.libraryTree), which says what is below `root` and
// which folders are work folders.
const NOTHING = { rename: false, move: false, moveTargets: [], delete: false, newSubfolder: false, newPage: false, chooseFiles: false };

export function libraryAccess({ shareMode = false, shareFolder = "", role = "", tree = EMPTY_TREE } = {}) {
  const root = shareMode ? shareFolder : "";
  const organize = !shareMode && role !== "viewer";
  const contains = (id) => !root || id === root || inFolder(tree, id, root);
  const work = treeOf(tree);
  return {
    root,
    browse: !shareMode || !!shareFolder,
    organize,
    pin: organize,
    history: !shareMode,
    contains,
    clamp: (id) => (contains(id || "") ? id || "" : root),
    workFolder: (id) => workOf(work, id),
    organizeIn: (id) => (organize ? folderRules(work, id || "") : NOTHING),
  };
}
