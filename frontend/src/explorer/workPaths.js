// A work folder's paths on the client (docs/dev/work_folders.md "Paths"):
// where a file is, as its crumbs show it, and which links of a note are
// paths, the twin of gamma/work_folders/paths.py's is_relative_href; a
// file's path, and a link's page, are the server's to give (each row it
// answers carries its path). A path is the names of the directories from
// the work folder's root folder W down, W left out, joined with "/", then
// the file's name. tests/shared/workpaths.json holds both sides to the
// same answers; revealPath is the Files window's. Pure:
// tests/workPaths.test.mjs and tests/fileTree.test.mjs.
//
// fileCrumbs takes `filed`: {folders (the page's folder ids, its
// directory first), work (W's id)}, built from a page row
// (libraryUtils.pageBlocksOf: _folders, _workFolder) or from the open
// page's own state, so nothing is read to answer.
import { folderChain } from "../library/libraryUtils.js";
import { workOf } from "./workFolders.js";

// The folders from W down to the page's directory, ids; null when the page
// is in no work folder or its directory is not in its W (paths.py's guard).
function chainOf(libTree, workTree, filed) {
  const work = filed?.work || "";
  const dir = filed?.folders?.[0] || "";
  if (!work || !dir || workOf(workTree, dir) !== work) return null;
  const chain = folderChain(libTree, dir);
  const at = chain.indexOf(work);
  return at < 0 ? null : chain.slice(at);
}

// A file row's name as the server gives the row (a directory's read, a
// search's hit): its file's, else the title of a note its vault has not
// named yet (db.SQL_FILE_NAME's rule); "" for neither.
export const fileNameOf = (row) => row?.properties?.file_name || row?.content || "";

// The folders a file page's crumbs name, [{id, name}] from W down to its
// directory: W first, the folder a person opens, which the path leaves out.
// [] outside a work folder and when the guard fails.
export function fileCrumbs(libTree, workTree, filed) {
  const chain = chainOf(libTree, workTree, filed);
  return chain ? chain.map((id) => ({ id, name: libTree.folders.get(id)?.name || "" })) : [];
}

// The folders the Files window opens to show a page (`row`: App's page row,
// or a page as the server answers it), from the top down to its first
// folder, a library page's as a file's; [] for a page filed in none.
export function revealPath(libTree, row) {
  const first = row?._folders?.[0] ?? row?.properties?.folders?.[0];
  return first ? folderChain(libTree, first) : [];
}

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

// Whether a link's address is a path relative to the note it is in, which
// the server resolves (page_at_href refuses every other): read with the
// spaces around it dropped, not empty, no scheme ("C:/…" reads as one, and
// is no relative link either), and not starting with "/", "#" or "?".
export function isRelativeHref(href) {
  if (typeof href !== "string") return false;
  const text = href.trim();
  return !!text && !SCHEME.test(text) && !/^[/#?]/.test(text);
}
