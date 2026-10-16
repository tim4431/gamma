// Paging for the workspace search (SearchPanel.jsx): the request for one page
// of a group's hits, and a further page added to the ones listed. Pure, so
// node tests it (tests/searchPages.test.mjs).
//
// The notes (/api/block-search) and library PDF (/api/pdf-search) groups are
// searched a page at a time, scoped on the server to the filter chips, so a
// match inside the chosen folder or label is found however many outside
// outrank it. An answer with more to come carries `next`; the group's "Show
// more" sends it back as `cursor`. Titles are matched in the browser, all at
// once, and shown a page at a time.

export const NOTES_PAGE = 20;
export const PDF_PAGE = 15;
export const TITLES_PAGE = 8;

// The query string of one page: the query, the page size, the filter chips
// as `scope` (their ids — the server keeps to the pages inside every one),
// the cursor a "Show more" continues from, then the endpoint's own params
// (an empty one left out).
export function searchQuery(q, { limit, chips = [], cursor = "", ...params }) {
  const query = new URLSearchParams({ q, limit: String(limit) });
  if (chips.length) query.set("scope", chips.map((c) => c.id).join(","));
  if (cursor) query.set("cursor", cursor);
  for (const [key, value] of Object.entries(params)) {
    if (value !== "" && value != null) query.set(key, String(value));
  }
  return query.toString();
}

// `more` after `list`, less the hits `list` already holds (`keyOf`: a hit's
// identity): the PDF index may grow between two pages, shifting the ranks.
export function appendNew(list, more, keyOf) {
  const seen = new Set(list.map(keyOf));
  return [...list, ...more.filter((hit) => !seen.has(keyOf(hit)))];
}
