// A synced folder's folder view (docs/dev/work_folders.md "The folder
// view"): its sort, the directories open in it, the row the keys are on,
// and the reads of the directory shown and of each open one below it
// (useDirectoryRows.js), from GET /api/folders/{id}/pages, sorted on the
// server. The library listing leaves a work folder's files out (unless
// they are in the library), so these rows are the second source of pages
// App's lookups merge in (explorer/knownPages.js), and the menus, the
// selection and the tabs find them there.
import { useCallback, useMemo, useState } from "react";
import { usePersistedState } from "../shared/lib/utils";
import { openDirs } from "./fileTree.js";
import { useDirectoryRows } from "./useDirectoryRows.js";

// The sorts the view offers, the server's keys; Modified lists the newest
// first, the others from A.
export const WORK_SORTS = ["name", "format", "size", "modified"];
const orderOf = (sort) => (sort === "modified" ? "desc" : "asc");

// {sort, setSort, expanded, toggle(id), active, setActive(key), dirs, rows,
// reveal, showFile(dir, pageId), revealDone()}
// for the directory `folderId` ("" for none: nothing is read). `sort` is
// the view's own, kept in this browser (`gamma-work-sort`) and never taken
// from the library's sort of its folders; `expanded` the directories open
// in it (a Set, this tab's), `active` the key of the row the keys are on;
// `dirs` the reads (useDirectoryRows: of(id), more, retry, patch, drop),
// read again whenever `version` (App's listing tree, read again on coming
// home, on wake and after an Add or a Remove) changes, so the columns (the
// open comments) stay the server's, a read again reading as many rows as
// were loaded; `rows` every row read of the directories drawn, for App's
// lookups. `libTree` the listing's tree. `showFile(dir, pageId)` shows a
// file as the view opens on its directory `dir` (a tab's Go to folder): the
// directory paged until it holds the file (useDirectoryRows find), then its
// row scrolled into sight with the keys on it (FolderTree), which ends the
// `reveal` ({dir, pageId}, null for none) with `revealDone()`.
export function useFolderTree({ folderId, libTree, version }) {
  const [stored, setSort] = usePersistedState("gamma-work-sort", "name");
  const sort = WORK_SORTS.includes(stored) ? stored : "name";
  const [expanded, setExpanded] = useState(() => new Set());
  const [active, setActive] = useState("");
  const wanted = useMemo(() => (folderId ? openDirs(libTree, folderId, expanded) : []), [libTree, folderId, expanded]);
  const dirs = useDirectoryRows({ wanted, counts: libTree.workCounts, changes: libTree.workChanges, enabled: !!folderId,
    sort, order: orderOf(sort), version });
  const rows = useMemo(() => wanted.flatMap((id) => dirs.of(id).rows), [wanted, dirs]);
  const toggle = useCallback((id) => setExpanded((prev) => {
    const next = new Set(prev);
    if (!next.delete(id)) next.add(id);
    return next;
  }), []);
  const [reveal, setReveal] = useState(null);
  const { find } = dirs;
  const showFile = useCallback((dir, pageId) => {
    find(dir, pageId);
    setReveal({ dir, pageId });
  }, [find]);
  const revealDone = useCallback(() => setReveal(null), []);
  return { sort, setSort, expanded, toggle, active, setActive, dirs, rows, reveal, showFile, revealDone };
}
