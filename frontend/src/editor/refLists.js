// The [[ link picker's lists (useRefPicker.js, RefPicker.jsx): pages
// matched by title first, then note blocks as one plain line under their
// page path. Also the typed "[[query" and the edit a picked row makes, and
// the hand-typed [[title]] rule: the one page with exactly that title.
// Pure, so node tests it (tests/refLists.test.mjs).
import { createLibraryMatcher } from "../library/librarySearch.js";
import { plainSnippet } from "../search/snippets.js";
import { REF_RE } from "./refLabels.js";

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
  const src = String(content || "").replace(REF_RE, (m, id) => {
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

// The "[[query" being typed just before the caret (`before`: the text up
// to it), as its query, or null when no picker is being typed.
export function refQueryAt(before) {
  const m = String(before || "").match(/\[\[([^\]\n]*)$/);
  return m ? m[1] : null;
}

// A picked row: the typed "[[query" before `cursor` becomes [[id]] (a "!"
// before it stays, making it an embed). {from, to, insert, value, caret},
// or null when no "[[" is being typed there.
export function refInsertEdit(value, cursor, id) {
  const query = refQueryAt(String(value || "").slice(0, cursor));
  if (query == null) return null;
  const from = cursor - query.length - 2, insert = `[[${id}]]`;
  return { from, to: cursor, insert, value: value.slice(0, from) + insert + value.slice(cursor), caret: from + insert.length };
}

// "]]" typed by hand after a page's exact title, ending at `cursor`: that
// title's [[id]] as {from, to, insert, page}, or null (no such page, or
// already an id).
export function typedTitleLink(value, cursor, pages) {
  const m = String(value || "").slice(0, cursor).match(/\[\[([^[\]\n]+)\]\]$/);
  const page = m && pageByTitle(pages, m[1]);
  if (!page || page.id === m[1]) return null;
  return { from: cursor - m[0].length, to: cursor, insert: `[[${page.id}]]`, page };
}
