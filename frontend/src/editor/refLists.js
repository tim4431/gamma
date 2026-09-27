// The [[ link picker's lists (BlockTree's refPopup, RefPicker.jsx): pages
// matched by title first, then note blocks as one plain line under their
// page path. Also the hand-typed [[title]] rule: the one page with exactly
// that title. Pure, so node tests it (tests/refLists.test.mjs).
import { createLibraryMatcher } from "../library/librarySearch.js";
import { plainSnippet } from "../search/snippets.js";

const PICKER_ROWS = 8;
const RECENT_PAGES = 4; // offered before anything is typed

// App's page list ([{id, content, _attachment, _folders, _labels,
// _updatedAt}]) → the pages the query names, best first: the library's own
// title matcher (typo-tolerant, folder/label chips as a weaker hit). An
// empty query offers the most recently edited pages. `excludeId` is the
// open page (a note linking its own page is no link).
export function rankRefPages(pages, query, excludeId) {
  const list = (pages || []).filter((p) => p.id !== excludeId);
  const q = (query || "").trim();
  if (!q) return [...list].sort((a, b) => (b._updatedAt || "").localeCompare(a._updatedAt || "")).slice(0, RECENT_PAGES);
  const match = createLibraryMatcher(q);
  if (!match) return [];
  return list
    .map((p, i) => ({ p, i, s: match(p.content || "", [...(p._folders || []), ...(p._labels || [])]) }))
    .filter((x) => x.s > 0)
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .map((x) => x.p);
}

// How many of each fit in the popup's rows: pages first, up to five while
// blocks wait, and either list takes the room the other leaves.
export function pickerCounts(pageCount, blockCount) {
  const pages = Math.min(pageCount, Math.max(5, PICKER_ROWS - blockCount));
  return [pages, Math.min(blockCount, PICKER_ROWS - pages)];
}

// A block's markdown as the picker shows it: one plain line (search's
// plainSnippet), its [[refs]] as their labels.
export function refBlockText(content, labelOf) {
  const src = String(content || "").replace(/\[\[([a-zA-Z0-9_-]+)\]\]/g, (m, id) => {
    const label = String(labelOf?.(id) || id).replace(/[[\]|\n]+/g, " ").trim();
    return `[[${label || id}]]`;
  });
  return plainSnippet(src);
}

// The page path a block result sits under ("Page › parent block"), plain.
export function refBlockPath(block) {
  return (block?.ancestors || []).map((a) => plainSnippet(a.content) || "…").join(" › ");
}

// The page a hand-typed [[title]] names: the one page whose title is
// exactly that (case and runs of spaces aside), else null.
export function pageByTitle(pages, text) {
  const norm = (s) => String(s || "").trim().replace(/\s+/g, " ").toLowerCase();
  const want = norm(text);
  if (!want) return null;
  const hits = (pages || []).filter((p) => norm(p.content) === want);
  return hits.length === 1 ? hits[0] : null;
}
