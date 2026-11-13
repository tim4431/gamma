// node --test tests/treeDerived.test.mjs (from frontend/) — what App derives
// from the open page's tree on each change (app/treeDerived.js), and the
// JSON keep its memos hold the lists by (shared/lib/keepIfSame.js
// keepIfSameJson).
import assert from "node:assert/strict";
import { test } from "node:test";
import { blocksById, commentAnchors, pageHighlights, recordingList, sheetNumbersOf, stampedList } from "../src/app/treeDerived.js";
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

test("commentAnchors: the highlights and the ink groups on PDF pages, in document order, with their open threads", () => {
  const box = (page) => ({ pageNumber: page, width: 612, height: 792, boundingRect: { x1: 10, y1: 20, x2: 30, y2: 40 }, rects: [{ x1: 10, y1: 20, x2: 30, y2: 40 }] });
  const inked = [
    ...tree,
    { id: "g1", content: "", properties: { ink_url: "/api/uploads/a.ink", pdf_position: box(3) }, children: [
      { id: "g2", content: "", properties: { ink_url: "/api/uploads/b.ink", pdf_position: box(1) }, children: [] },
    ] },
    // a group on a sheet has no position; a highlight placed by its page alone has no box
    { id: "g3", content: "", properties: { ink_url: "/api/uploads/c.ink" }, children: [] },
    { id: "h2", content: "", properties: { pdf_position: { pageNumber: 2 } }, children: [] },
  ];
  const out = commentAnchors(inked, new Map([["h1", 2], ["g1", 1]]));
  assert.deepEqual(out.map((a) => [a.id, a.kind, a.threadCount]),
    [["h1", "highlight", 2], ["l1", "highlight", 0], ["g1", "ink", 1], ["g2", "ink", 0]]);
  assert.equal(out[2].position, inked[6].properties.pdf_position, "the block's own position");
  assert.deepEqual(commentAnchors([], new Map()), []);
});

test("pageHighlights and commentAnchors under the image key: a picture's regions, with their threads, on the shown version or not", () => {
  const region = (hash) => ({ width: 1200, height: 800, boundingRect: { x1: 10, y1: 20, x2: 300, y2: 200 },
    rects: [{ x1: 10, y1: 20, x2: 300, y2: 200 }], hash });
  const picture = [
    { id: "r1", content: "", properties: { image_position: region("i1"), color: "#ffe066aa" }, children: [
      { id: "n1", content: "a note", properties: {}, children: [] },
    ] },
    { id: "r0", content: "", properties: { image_position: region("i0") }, children: [] },
    { id: "h1", content: "", properties: { pdf_position: at(1), quote: "a quote" }, children: [] },
    { id: "g1", content: "", properties: { ink_url: "/api/uploads/a.ink", image_position: region("i1") }, children: [] },
  ];
  const flatPicture = flattenBlocks(picture);
  const out = pageHighlights(picture, flatPicture, new Map([["r1", 1]]), "image_position", "i1");
  assert.deepEqual(out.map((h) => [h.id, h.threadCount, h.hasNote, h.position.area, h.position.onVersion]),
    [["r1", 1, true, true, true], ["r0", undefined, false, true, false]]);
  assert.deepEqual(pageHighlights(picture, flatPicture, new Map()).map((h) => h.id), ["h1"], "the PDF key: the PDF's alone");
  const anchors = commentAnchors(picture, new Map([["r0", 2]]), "image_position");
  assert.deepEqual(anchors.map((a) => [a.id, a.kind, a.threadCount]), [["r1", "highlight", 0], ["r0", "highlight", 2]]);
  assert.equal(anchors[0].position, picture[0].properties.image_position, "the block's own position");
  assert.deepEqual(commentAnchors(picture, new Map()).map((a) => a.id), ["h1"], "the PDF key: the PDF's alone");
});

test("keepIfSameJson: the kept list while the next one reads the same, the next one once it differs", () => {
  const ref = { current: { json: "", value: [] } };
  const first = keepIfSameJson(ref, stampedList(tree));
  assert.equal(keepIfSameJson(ref, stampedList(tree)), first);
  const changed = stampedList([...tree, { id: "f3", content: "", properties: { t0: 30 }, children: [] }]);
  assert.equal(keepIfSameJson(ref, changed), changed);
  assert.deepEqual(ref.current, { json: JSON.stringify(changed), value: changed });
});
