// The workspace search's paging (search/searchPages.js): one page's request
// body, kept to the filters' scope and pages, and a further page added
// without repeats.
import assert from "node:assert/strict";
import { test } from "node:test";
import { DOC_PAGE, NOTES_PAGE, TITLES_PAGE, appendNew, docHitKey, searchBody } from "../src/search/searchPages.js";

test("the first pages keep their sizes", () => {
  assert.deepEqual([NOTES_PAGE, DOC_PAGE, TITLES_PAGE], [20, 15, 8]);
});

test("searchBody: the query and page size; the filters' pages, [] kept apart from none", () => {
  assert.deepEqual(searchBody("3,000 qubit & more", { limit: 20 }), { q: "3,000 qubit & more", limit: 20 });
  assert.deepEqual(searchBody("atom", { limit: 15, pages: ["p1", "p2"] }), { q: "atom", limit: 15, pages: ["p1", "p2"] });
  assert.deepEqual(searchBody("atom", { limit: 15, pages: [] }), { q: "atom", limit: 15, pages: [] }, "[] reaches no page");
  assert.deepEqual(searchBody("atom", { limit: 15, pages: null }), { q: "atom", limit: 15 }, "none: the whole library");
});

test("searchBody: the scope's folders and labels travel as ids, an empty scope never", () => {
  assert.deepEqual(searchBody("atom", { limit: 15, scope: { folders: ["w1"], labels: [] } }),
    { q: "atom", limit: 15, folders: ["w1"] }, "a folder chip alone: one id, no page list");
  assert.deepEqual(searchBody("atom", { limit: 15, scope: { folders: [], labels: ["l1", "l2"] } }),
    { q: "atom", limit: 15, labels: ["l1", "l2"] });
  assert.deepEqual(searchBody("atom", { limit: 15, scope: { folders: [], labels: [] } }), { q: "atom", limit: 15 });
  assert.deepEqual(searchBody("atom", { limit: 15, scope: null }), { q: "atom", limit: 15 });
  assert.deepEqual(searchBody("atom", { limit: 15, scope: { folders: ["w1"], labels: ["l1"] }, pages: [] }),
    { q: "atom", limit: 15, folders: ["w1"], labels: ["l1"], pages: [] }, "a predicate's empty set still travels");
});

test("searchBody: a cursor, the kinds and the endpoint's fields, empty ones left out", () => {
  const body = searchBody("atom", { limit: 20, cursor: "2026-10-16T09:00:00.000000Z|42", kinds: ["highlight"],
    case: false, whole: true, exclude: "" });
  assert.deepEqual(body, { q: "atom", limit: 20, cursor: "2026-10-16T09:00:00.000000Z|42", kinds: ["highlight"],
    case: false, whole: true });
  assert.deepEqual(searchBody("atom", { limit: 15, cursor: "", kinds: null, exclude: null }), { q: "atom", limit: 15 });
  assert.deepEqual(searchBody("atom", { limit: 15, kinds: [] }), { q: "atom", limit: 15 }, "no kinds: every kind");
  assert.deepEqual(searchBody("atom", { limit: 15, exclude: "pg1" }), { q: "atom", limit: 15, exclude: "pg1" });
  assert.deepEqual(searchBody("atom", { limit: 15, formats: ["text"] }), { q: "atom", limit: 15, formats: ["text"] });
  assert.deepEqual(searchBody("atom", { limit: 15, formats: null }), { q: "atom", limit: 15 }, "no formats: both");
});

test("appendNew adds a page after the list, without the hits it holds", () => {
  const key = (r) => `${r.doc_id}:${r.page}`;
  const first = [{ doc_id: "a", page: 1 }, { doc_id: "b", page: 4 }];
  const next = [{ doc_id: "b", page: 4 }, { doc_id: "a", page: 2 }, { doc_id: "c", page: 1 }];
  assert.deepEqual(appendNew(first, next, key).map(key), ["a:1", "b:4", "a:2", "c:1"]);
  assert.deepEqual(appendNew(first, [], key), first);
  assert.deepEqual(appendNew([], next, key), next);
});

test("docHitKey: a PDF's page and a file's line, on each page of a document", () => {
  const pdf = (page_id, page) => ({ source: "pdf", block_id: page_id, page_id, doc_id: "d1", page });
  const file = (page_id, line) => ({ source: "file", block_id: page_id, page_id, doc_id: "t1", line, lines: [1, 40] });
  assert.equal(docHitKey(pdf("p1", 3)), "pdf:p1:d1:3");
  assert.equal(docHitKey(file("f1", 2)), "file:f1:t1:2");
  // a page's next answer repeats a hit when the index grew between them;
  // two files of equal bytes are two hits
  const first = [pdf("p1", 3), file("f1", 2), file("f2", 2)];
  const next = [file("f1", 2), file("f1", 45), pdf("p1", 4), pdf("p1", 3)];
  assert.deepEqual(appendNew(first, next, docHitKey).map(docHitKey),
    ["pdf:p1:d1:3", "file:f1:t1:2", "file:f2:t1:2", "file:f1:t1:45", "pdf:p1:d1:4"]);
});
