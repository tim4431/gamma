// node --test tests/replicaTextmerge.test.mjs (from frontend/) — the text
// merge's JavaScript twin against the Python reference's cases.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { contains, mapOffset, merge } from "../src/replica/textmerge.js";

const CASES = JSON.parse(readFileSync(new URL("../../tests/shared/textmerge.json", import.meta.url), "utf8"));

for (const c of CASES.merge) {
  test(`merge: ${c.note}`, () => assert.deepEqual(merge(c.base, c.ours, c.theirs), c.result));
}
for (const c of CASES.contains) {
  test(`contains: ${c.note}`, () => assert.equal(contains(c.base, c.ours, c.theirs), c.result));
}
for (const c of CASES.map_offset) {
  test(`map offset: ${c.note}`, () => assert.equal(mapOffset(c.src, c.dst, c.offset), c.result));
}
