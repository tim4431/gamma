// Paging for the workspace search (SearchPanel.jsx): the request for one page
// of a group's hits, and a further page added to the ones listed. Pure, so
// node tests it (tests/searchPages.test.mjs).
//
// The notes (POST /api/block-search) and documents (POST
// /api/document-search: PDF pages and text files' lines) groups are
// searched a page at a time inside the filters' scope
// (search/searchFilters.js): the folders and labels of the chips and the
// `folder:` / `label:` clauses as their ids, which the server resolves, and
// the listing pages the other qualifiers keep as theirs, so a match inside
// them is found however many outside outrank it. An answer with more to
// come carries `next`; the group's "Show more" sends it back as `cursor`,
// with the scope its first page was asked with. Titles are matched in the
// browser, all at once, and shown a page at a time.

export const NOTES_PAGE = 20;
export const DOC_PAGE = 15;
export const TITLES_PAGE = 8;

// The body of one page's POST: the query, the page size, the cursor a
// "Show more" continues from, the scope (`folders` and `labels`, each left
// out when empty), `pages` (the ids the predicates keep: [] reaches no
// page; left out, every page of the scope, or of the library without
// one), `kinds` (the block kinds in: keeps, left out for all), then the
// endpoint's own fields. An empty value is left out, an empty `pages`
// never.
export function searchBody(q, { limit, cursor = "", scope = null, pages = null, kinds = null, ...params }) {
  const body = { q, limit };
  if (cursor) body.cursor = cursor;
  if (scope?.folders?.length) body.folders = scope.folders;
  if (scope?.labels?.length) body.labels = scope.labels;
  if (Array.isArray(pages)) body.pages = pages;
  if (kinds?.length) body.kinds = kinds;
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value != null) body[key] = value;
  }
  return body;
}

// A document hit's identity: its source, page and document, and the PDF
// page or the file's line it names (two files of equal bytes are two hits).
export const docHitKey = (r) => `${r.source}:${r.page_id}:${r.doc_id}:${r.page ?? r.line}`;

// `more` after `list`, less the hits `list` already holds (`keyOf`: a hit's
// identity): the document index may grow between two pages, shifting the
// ranks.
export function appendNew(list, more, keyOf) {
  const seen = new Set(list.map(keyOf));
  return [...list, ...more.filter((hit) => !seen.has(keyOf(hit)))];
}
