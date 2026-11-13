// The library rule (docs/dev/work_folders.md "The library rule"), stated
// once over a block's properties:
//
//   workFolderOf(P)    P.work_folder when it is a non-empty string, else ""
//   listed(P)          no work folder, or P.library is true
//   inListing(P)       listed(P), or P.pinned is a non-empty string
//   libraryDocument(P) P.library is true              for a work folder's file
//                      not inPlace(pageDocument(P))   for any other page
//   isLibraryPdf(P)    a PDF P owns (ownHash), and libraryDocument(P)
//
// `listed` is what the library lists (its root, label counts, the pickers'
// default lists); `inListing` what the listing route carries, pinned files
// included; `libraryDocument` what is a paper: the record looked up on open,
// the Paper metadata button. A page outside every work folder is a library
// document unless its document is read in place (an Opened files page). The
// stamps (`STAMPS`) are the vault's. The twin of
// gamma/work_folders/library.py; tests/shared/library.json holds both to the
// same answers.
import { inPlace, isPdf, ownHash, pageDocument } from "./pageDocument.js";

export const STAMPS = ["work_folder", "library", "file_name"];

const propsOf = (block) => {
  const props = block?.properties;
  return props && typeof props === "object" && !Array.isArray(props) ? props : {};
};

// The work folder a page is a file of (its root folder's id), "" for none.
export function workFolderOf(block) {
  const value = propsOf(block).work_folder;
  return typeof value === "string" ? value : "";
}

export const listed = (block) => !workFolderOf(block) || propsOf(block).library === true;

export function inListing(block) {
  const pinned = propsOf(block).pinned;
  return listed(block) || (typeof pinned === "string" && pinned !== "");
}

export function libraryDocument(block) {
  return workFolderOf(block) ? propsOf(block).library === true : !inPlace(pageDocument(block));
}

export function isLibraryPdf(block) {
  const doc = pageDocument(block);
  return isPdf(doc) && ownHash(doc) !== "" && libraryDocument(block);
}

// Whether a person's renames, moves, trash and restore of the page reach
// the disk: a work folder's note (no document) where the vault pulls (`mark`,
// its root folder's {pull, library}). Everything else in a work folder
// follows the disk alone.
export function follows(block, mark) {
  return !!workFolderOf(block) && propsOf(block).doc == null && mark?.pull === true;
}
