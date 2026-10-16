// The workspace search's paging (search/searchPages.js): one page's request,
// scoped to the filter chips, and a further page added without repeats.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NOTES_PAGE, PDF_PAGE, TITLES_PAGE, appendNew, searchQuery } from "../src/search/searchPages.js";

const params = (query) => Object.fromEntries(new URLSearchParams(query));

test("the first pages keep their sizes", () => {
  assert.deepEqual([NOTES_PAGE, PDF_PAGE, TITLES_PAGE], [20, 15, 8]);
});

test("searchQuery: the query and page size, the chips as one scope", () => {
  assert.deepEqual(params(searchQuery("3,000 qubit & more", { limit: 20 })), { q: "3,000 qubit & more", limit: "20" });
  const chips = [{ kind: "folder", id: "f1", name: "Physics / Atoms" }, { kind: "label", id: "l2", name: "toread" }];
  assert.deepEqual(params(searchQuery("atom", { limit: 15, chips })), { q: "atom", limit: "15", scope: "f1,l2" });
});

test("searchQuery: a cursor and the endpoint's params, empty ones left out", () => {
  const query = searchQuery("atom", { limit: 20, cursor: "2026-10-16T09:00:00.000000Z|42", case: 0, whole: 1, exclude: "" });
  assert.deepEqual(params(query), { q: "atom", limit: "20", cursor: "2026-10-16T09:00:00.000000Z|42", case: "0", whole: "1" });
  assert.ok(query.startsWith("q=atom&"), "the query leads, as the browser suite looks for it");
  assert.equal(params(searchQuery("atom", { limit: 15, cursor: "", exclude: null })).cursor, undefined);
});

test("appendNew adds a page after the list, without the hits it holds", () => {
  const key = (r) => `${r.doc_id}:${r.page}`;
  const first = [{ doc_id: "a", page: 1 }, { doc_id: "b", page: 4 }];
  const next = [{ doc_id: "b", page: 4 }, { doc_id: "a", page: 2 }, { doc_id: "c", page: 1 }];
  assert.deepEqual(appendNew(first, next, key).map(key), ["a:1", "b:4", "a:2", "c:1"]);
  assert.deepEqual(appendNew(first, [], key), first);
  assert.deepEqual(appendNew([], next, key), next);
});
