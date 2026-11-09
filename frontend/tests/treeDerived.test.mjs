// node --test tests/treeDerived.test.mjs (from frontend/) — what App derives
// from the open page's tree on each change (app/treeDerived.js), and the
// JSON keep its memos hold the lists by (shared/lib/keepIfSame.js
// keepIfSameJson).
import assert from "node:assert/strict";
import { test } from "node:test";
import { blocksById, pageHighlights, recordingList, sheetNumbersOf, stampedList } from "../src/app/treeDerived.js";
import { sheetsOf } from "../src/notebook/notebook.js";
import { keepIfSameJson } from "../src/shared/lib/keepIfSame.js";
import { flattenBlocks } from "../src/shared/model/blockModel.js";

const at = (page) => ({ pageNumber: page, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4, width: 612, height: 792, pageNumber: page }, rects: [] });
const rec = { started: 1000, state: "done", segments: [{ url: "/api/uploads/r.webm", started: 1000, dur: 5000 }] };
const tree = [
  { id: "h1", content: "", properties: { pdf_position: at(1), quote: "a quote", color: "#ffe066aa" }, children: [
    { id: "n1", content: "a note", properties: { t0: 20 }, children: [] },
  ] },
  { id: "s1", content: "", properties: { sheet: { width: 612, height: 792 } }, children: [
    { id: "r1", content: "Lecture", properties: { recording: rec }, children: [] },
  ] },
  { id: "l1", content: "", properties: { pdf_position: at(2), link_url: "https://example.org", link_block_id: "b9" }, children: [] },
  { id: "s2", content: "", properties: { sheet: {} }, children: [] },
  { id: "f1", content: "stamped", properties: { t0: 10 }, children: [] },
  { id: "f2", content: "not stamped", properties: { t0: "soon" }, children: [] },
];
const flat = flattenBlocks(tree);

test("blocksById and sheetNumbersOf: the lookups App's rows read", () => {
  const byId = blocksById(flat);
  assert.equal(byId.size, 8);
  assert.equal(byId.get("n1").parentId, "h1");
  assert.deepEqual([...sheetNumbersOf(sheetsOf(tree))], [["s1", 1], ["s2", 2]]);
});

test("recordingList and stampedList: the recordings, and the blocks stamped with a finite t0, in tree order", () => {
  assert.deepEqual(recordingList(tree), [{ id: "r1", properties: { recording: rec } }]);
  assert.deepEqual(stampedList(tree), [{ id: "n1", t0: 20 }, { id: "f1", t0: 10 }]);
});

test("pageHighlights: the viewer's highlights with their open threads counted and a link block's target", () => {
  const out = pageHighlights(tree, flat, new Map([["h1", 2]]));
  assert.deepEqual(out.map((h) => h.id), ["h1", "l1"]);
  assert.equal(out[0].threadCount, 2);
  assert.equal(out[0].hasNote, true);
  assert.equal(out[0].linkTarget, undefined);
  assert.equal("threadCount" in out[1], false);
  assert.deepEqual(out[1].linkTarget, { url: "https://example.org", pageId: "", blockId: "b9" });
});

test("keepIfSameJson: the kept list while the next one reads the same, the next one once it differs", () => {
  const ref = { current: { json: "", value: [] } };
  const first = keepIfSameJson(ref, stampedList(tree));
  assert.equal(keepIfSameJson(ref, stampedList(tree)), first);
  const changed = stampedList([...tree, { id: "f3", content: "", properties: { t0: 30 }, children: [] }]);
  assert.equal(keepIfSameJson(ref, changed), changed);
  assert.deepEqual(ref.current, { json: JSON.stringify(changed), value: changed });
});
