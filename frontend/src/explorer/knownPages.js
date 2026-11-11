// The pages the client knows (docs/dev/work_folders.md "The readers,
// classified"): the library listing's rows, which leave a work folder's
// files out unless they are in the library or pinned, and the rows read
// beside it, a work folder's directory read in pages and the summaries of
// the pages open in tabs, recently viewed or open now. App
// merges them into one page list (libraryUtils.pageBlocksOf), so menus,
// selection, tabs and recents find a file; what the library lists is the
// rows that are `_listed`. Pure: tests/knownPages.test.mjs.
import { listed } from "../documents/library.js";

// The listing's rows and `extraRows` as one array by id, the listing's row
// winning: the listing's in its order, then each extra row it lacks, in
// theirs. An extra row gets its effective parent as `_parentPage` ("" for a
// library page) from the server's `parent`, as App gives the listing's.
export function mergeKnown(listingRows, extraRows) {
  if (!extraRows?.length) return listingRows;
  const ids = new Set(listingRows.map((row) => row.id));
  const out = [...listingRows];
  for (const row of extraRows) {
    if (ids.has(row.id)) continue;
    ids.add(row.id);
    out.push("_parentPage" in row ? row : { ...row, _parentPage: row.parent || "" });
  }
  return out;
}

// Whether the library lists `row`: what its top says (a sub-page goes by
// the page it is in, at any depth), from `byId` (rows by id, with
// `_parentPage`). A top the map lacks counts as listed: a sub-page carries
// no stamps of its own, and its top's are not known here.
export function listedOf(row, byId) {
  const seen = new Set();
  let top = row;
  while (top?._parentPage && !seen.has(top.id)) {
    seen.add(top.id);
    top = byId.get(top._parentPage);
  }
  return !top || listed(top);
}
