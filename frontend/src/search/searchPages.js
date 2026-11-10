// Paging for the workspace search (SearchPanel.jsx): the request for one page
// of a group's hits, and a further page added to the ones listed. Pure, so
// node tests it (tests/searchPages.test.mjs).
//
// The notes (POST /api/block-search) and library PDF (POST /api/pdf-search)
// groups are searched a page at a time inside the pages the filters keep
// (search/searchFilters.js: the chips and the typed qualifiers, evaluated
// on the library listing), sent as their ids, so a match inside them is
// found however many outside outrank it. An answer with more to come
// carries `next`; the group's "Show more" sends it back as `cursor`, with
// the page ids its first page was asked with. Titles are matched in the
// browser, all at once, and shown a page at a time.

export const NOTES_PAGE = 20;
export const PDF_PAGE = 15;
export const TITLES_PAGE = 8;

// The body of one page's POST: the query, the page size, the cursor a
// "Show more" continues from, `pages` (the ids the filters keep: [] reaches
// no page, left out it is the whole library), `kinds` (the block kinds
// in: keeps, left out for all), then the endpoint's own fields. An empty
// value is left out, an empty `pages` never.
export function searchBody(q, { limit, cursor = "", pages = null, kinds = null, ...params }) {
  const body = { q, limit };
  if (cursor) body.cursor = cursor;
  if (Array.isArray(pages)) body.pages = pages;
  if (kinds?.length) body.kinds = kinds;
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value != null) body[key] = value;
  }
  return body;
}

// `more` after `list`, less the hits `list` already holds (`keyOf`: a hit's
// identity): the PDF index may grow between two pages, shifting the ranks.
export function appendNew(list, more, keyOf) {
  const seen = new Set(list.map(keyOf));
  return [...list, ...more.filter((hit) => !seen.has(keyOf(hit)))];
}
