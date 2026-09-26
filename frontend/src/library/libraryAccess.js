// What the person may do with the LIBRARY they are looking at — the home
// listing, its folders and labels — as opposed to a page's notes (that is
// App's `readOnly`). One object derived from how the library was reached,
// consulted by every affordance of the home library: the "New page" / "New
// folder" rows, drags and drops, the context menus, pins, the recents and
// pinned strips, and how far up the folder browser may go. Nothing in the
// listing checks a role or a share token itself.
//
//   root      the folder the view is confined to ("" = the whole library):
//             a folder share's folder — the browser never climbs above it
//   browse    whether there is a library to list at all (a page share has none)
//   organize  create, rename, move, duplicate, delete, label, file — every
//             write to the library's structure, incl. sharing a folder
//   pin       pins and the pinned strip (page pins are a block write)
//   history   the recents strip and the account's view history
//   contains  whether a folder path is inside `root`
//   clamp     that path when it is, else `root`
//
// A workspace viewer browses everything but organizes nothing; a share
// visitor browses the shared folder only.
export function libraryAccess({ shareMode = false, shareFolder = "", role = "" } = {}) {
  const root = shareMode ? shareFolder : "";
  const organize = !shareMode && role !== "viewer";
  const contains = (path) => !root || path === root || path.startsWith(root + "/");
  return {
    root,
    browse: !shareMode || !!shareFolder,
    organize,
    pin: organize,
    history: !shareMode,
    contains,
    clamp: (path) => (contains(path || "") ? path || "" : root),
  };
}
