// The workspace search's paging (search/searchPages.js): one page's request
// body, kept to the filters' pages, and a further page added without repeats.
import assert from "node:assert/strict";
import { test } from "node:test";
import { NOTES_PAGE, PDF_PAGE, TITLES_PAGE, appendNew, searchBody } from "../src/search/searchPages.js";

test("the first pages keep their sizes", () => {
  assert.deepEqual([NOTES_PAGE, PDF_PAGE, TITLES_PAGE], [20, 15, 8]);
});

test("searchBody: the query and page size; the filters' pages, [] kept apart from none", () => {
  assert.deepEqual(searchBody("3,000 qubit & more", { limit: 20 }), { q: "3,000 qubit & more", limit: 20 });
  assert.deepEqual(searchBody("atom", { limit: 15, pages: ["p1", "p2"] }), { q: "atom", limit: 15, pages: ["p1", "p2"] });
  assert.deepEqual(searchBody("atom", { limit: 15, pages: [] }), { q: "atom", limit: 15, pages: [] }, "[] reaches no page");
  assert.deepEqual(searchBody("atom", { limit: 15, pages: null }), { q: "atom", limit: 15 }, "none: the whole library");
});

test("searchBody: a cursor, the kinds and the endpoint's fields, empty ones left out", () => {
  const body = searchBody("atom", { limit: 20, cursor: "2026-10-16T09:00:00.000000Z|42", kinds: ["highlight"],
    case: false, whole: true, exclude: "" });
  assert.deepEqual(body, { q: "atom", limit: 20, cursor: "2026-10-16T09:00:00.000000Z|42", kinds: ["highlight"],
    case: false, whole: true });
  assert.deepEqual(searchBody("atom", { limit: 15, cursor: "", kinds: null, exclude: null }), { q: "atom", limit: 15 });
  assert.deepEqual(searchBody("atom", { limit: 15, kinds: [] }), { q: "atom", limit: 15 }, "no kinds: every kind");
  assert.deepEqual(searchBody("atom", { limit: 15, exclude: "pg1" }), { q: "atom", limit: 15, exclude: "pg1" });
});

test("appendNew adds a page after the list, without the hits it holds", () => {
  const key = (r) => `${r.doc_id}:${r.page}`;
  const first = [{ doc_id: "a", page: 1 }, { doc_id: "b", page: 4 }];
  const next = [{ doc_id: "b", page: 4 }, { doc_id: "a", page: 2 }, { doc_id: "c", page: 1 }];
  assert.deepEqual(appendNew(first, next, key).map(key), ["a:1", "b:4", "a:2", "c:1"]);
  assert.deepEqual(appendNew(first, [], key), first);
  assert.deepEqual(appendNew([], next, key), next);
});
