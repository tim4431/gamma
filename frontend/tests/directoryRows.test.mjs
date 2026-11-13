// A work folder's directory reads (explorer/directoryRows.js): an answer
// merged into what a directory holds, a read again of every row held in
// requests of MAX_ROWS, which open directories to read now, and how far the
// reveal pages.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_ROWS, NONE, PAGE_ROWS, changedSince, findStep, joined, merged, readLimit, readRows, wantedReads,
} from "../src/explorer/directoryRows.js";

const rows = (from, n) => Array.from({ length: n }, (_, i) => ({ id: `p${from + i}`, content: `p${from + i}`, properties: {} }));
const entry = (n, more = {}) => ({ ...NONE, rows: rows(0, n), total: n, loaded: true, count: n, seq: 10, ...more });
const CHANGES = { seq: 10, since: 0, dirs: {} };

test("an answer replaces the rows from the top or follows them, a row held already kept once", () => {
  const first = merged(undefined, { pages: rows(0, 2), total: 3, next: 2, count: 3, seq: 7 }, true);
  assert.deepEqual(first.rows.map((r) => r.id), ["p0", "p1"]);
  assert.deepEqual([first.total, first.next, first.loaded, first.loading, first.failed, first.count, first.seq],
    [3, 2, true, false, false, 3, 7]);
  assert.equal(first.rows[0]._parentPage, "", "a row as the listing's: its parent");
  const next = merged({ ...first, loading: true }, { pages: [...rows(1, 1), ...rows(2, 1)], total: 3, next: null, seq: 9 }, false);
  assert.deepEqual(next.rows.map((r) => r.id), ["p0", "p1", "p2"]);
  assert.equal(next.next, null);
  assert.equal(next.count, 3, "a later page keeps the count the rows were read at");
  assert.equal(next.seq, 7, "and the seq the first rows were read at");
  const again = merged(next, { pages: rows(5, 1), total: 1, next: null, count: 1, seq: 12 }, true);
  assert.deepEqual(again.rows.map((r) => r.id), ["p5"]);
  assert.deepEqual([again.count, again.seq], [1, 12]);
  assert.deepEqual(joined(rows(0, 2), rows(1, 2)).map((r) => r.id), ["p0", "p1", "p2"]);
});

test("a read from the top asks for as many rows as are held, a page at least", () => {
  assert.equal(readLimit(undefined), PAGE_ROWS);
  assert.equal(readLimit(entry(3)), PAGE_ROWS);
  assert.equal(readLimit(entry(400)), 400);
  assert.equal(readLimit(entry(1000)), 1000, "past MAX_ROWS: every row held, read in more requests");
});

// A directory of `total` files, as GET /api/folders/{id}/pages answers.
const server = (total, seq = 3) => {
  const asked = [];
  const page = async (offset, limit) => {
    asked.push([offset, limit]);
    const got = rows(offset, Math.max(0, Math.min(limit, total - offset)));
    return { pages: got, total, next: offset + got.length < total ? offset + got.length : null, seq: seq + asked.length - 1 };
  };
  return { page, asked };
};

test("a read again of 1,000 rows asks MAX_ROWS at a time and keeps them all", async () => {
  const s = server(10000);
  const d = await readRows(s.page, 0, 1000);
  assert.deepEqual(s.asked, [[0, MAX_ROWS], [MAX_ROWS, MAX_ROWS]]);
  assert.equal(d.pages.length, 1000);
  assert.ok(d.pages.some((r) => r.id === "p700"), "the file past row 500 stays");
  assert.deepEqual([d.next, d.total, d.seq], [1000, 10000, 3], "the last answer's next, the first's seq");
  const held = merged(entry(1000), { ...d, count: 10000 }, true);
  assert.equal(held.rows.length, 1000);
});

test("a read stops at the rows' end, and one no longer wanted answers nothing", async () => {
  const s = server(650);
  const d = await readRows(s.page, 0, 1000);
  assert.deepEqual(s.asked, [[0, MAX_ROWS], [MAX_ROWS, MAX_ROWS]]);
  assert.deepEqual([d.pages.length, d.next], [650, null]);
  const one = server(10000);
  assert.equal((await readRows(one.page, 200, PAGE_ROWS)).pages.length, PAGE_ROWS);
  assert.deepEqual(one.asked, [[200, PAGE_ROWS]]);
  assert.equal(await readRows(async () => null, 0, 1000), null);
});

test("a directory is read when first wanted, and nothing while the window is not shown", () => {
  const held = new Map();
  assert.deepEqual(wantedReads(["W", "d"], held, { d: 2 }, CHANGES, true), ["W", "d"]);
  assert.deepEqual(wantedReads(["W", "d"], held, { d: 2 }, CHANGES, false), [], "not shown: nothing");
  assert.deepEqual(wantedReads(["W", "d"], held, { d: 2 }, CHANGES, true), ["W", "d"], "shown again: the reads it held back");
});

test("a listing read again with nothing changed reads nothing; a changed count reads that directory again", () => {
  const held = new Map([["W", entry(1)], ["d", entry(2)]]);
  assert.deepEqual(wantedReads(["W", "d"], held, { W: 1, d: 2 }, CHANGES, true), [], "a refresh with the same counts");
  assert.deepEqual(wantedReads(["W", "d"], held, { W: 1, d: 3 }, CHANGES, true), ["d"]);
  assert.deepEqual(wantedReads(["W", "d"], held, { d: 2 }, CHANGES, true), ["W"], "a count gone is a count of 0");
});

test("a directory written since its read is read again, its count the same: a file renamed on disk", () => {
  const held = new Map([["d", entry(2)], ["e", entry(3)]]);
  const counts = { d: 2, e: 3 };
  assert.deepEqual(wantedReads(["d", "e"], held, counts, { seq: 14, since: 0, dirs: { d: 14 } }, true), ["d"]);
  assert.deepEqual(wantedReads(["d", "e"], held, counts, { seq: 14, since: 0, dirs: { d: 10, e: 4 } }, true), [],
    "writes the rows were read after");
  assert.deepEqual(wantedReads(["d", "e"], held, counts, { seq: 600, since: 100, dirs: {} }, true), ["d", "e"],
    "a read older than the log answers for");
  assert.deepEqual(wantedReads(["d"], held, counts, null, true), [], "no changes from the listing: the counts alone");
  assert.equal(changedSince(entry(2), "d", { seq: 11, since: 0, dirs: { d: 11 } }), true);
  assert.equal(changedSince(merged(entry(2), { pages: rows(0, 2), total: 2, next: null, seq: 11 }, true), "d",
    { seq: 11, since: 0, dirs: { d: 11 } }), false, "read again: as the log stands");
});

test("a closed directory is not read, and one in flight is not asked again", () => {
  const held = new Map([["d", entry(2)], ["e", { ...NONE, loading: true, count: 5 }]]);
  assert.deepEqual(wantedReads([], held, { d: 9 }, CHANGES, true), [], "closed: not read, whatever changed");
  assert.deepEqual(wantedReads(["d"], held, { d: 9 }, CHANGES, true), ["d"], "opened again after a change: read");
  assert.deepEqual(wantedReads(["d"], held, { d: 2 }, { seq: 20, since: 0, dirs: { d: 20 } }, true), ["d"],
    "opened again after a write: read");
  assert.deepEqual(wantedReads(["e"], held, { e: 6 }, CHANGES, true), [], "in flight");
});

test("the reveal pages on until the row is held, the rows end, or MAX_ROWS are held", () => {
  assert.equal(findStep(entry(200, { next: 200, total: 1000 }), "p999"), 200);
  assert.equal(findStep(entry(200, { next: 200, total: 1000 }), "p5"), null, "held");
  assert.equal(findStep(entry(200, { next: null }), "p999"), null, "the rows end");
  assert.equal(findStep(entry(MAX_ROWS, { next: MAX_ROWS, total: 1000 }), "p999"), null, "MAX_ROWS held");
  assert.equal(findStep(entry(200, { next: 200, loading: true }), "p999"), null, "a read in flight");
  assert.equal(findStep(entry(200, { next: 200, failed: true }), "p999"), null, "a failed read");
  assert.equal(findStep(NONE, "p1"), null, "never read: the first read is the open directory's");
});
