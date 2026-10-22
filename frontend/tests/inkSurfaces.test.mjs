// node --test tests/inkSurfaces.test.mjs (from frontend/) — the page's ink
// by surface (ink/inkSurfaces.js, useInkSurfaces.js's pure parts): each
// surface's groups, what messages call a surface, and where a paste
// without a point goes.
import assert from "node:assert/strict";
import { test } from "node:test";
import { inkBySheet } from "../src/notebook/notebook.js";
import { groupsBySurface, pasteTargetOf, surfaceLabel } from "../src/ink/inkSurfaces.js";

const group = (id, pageNumber) => ({ id, properties: { ink_url: `/api/uploads/${id}.ink`, ...(pageNumber ? { pdf_position: { pageNumber } } : {}) } });

test("a PDF page's groups by their position's page, in order; a sheet's by its id (inkBySheet), taking its key over", () => {
  const blocks = [group("a", 1), group("b", 2), group("c", 1), group("s"), group("odd", "s1")];
  const tree = [{ id: "s1", properties: { sheet: {} }, children: [{ id: "s", properties: blocks[3].properties, children: [] }] },
    { id: "s2", properties: { sheet: {} }, children: [] }];
  const map = groupsBySurface(blocks, inkBySheet(tree));
  assert.deepEqual([...map.keys()], [1, 2, "s1", "s2"]);
  assert.deepEqual(map.get(1).map((b) => b.id), ["a", "c"]);
  assert.equal(map.get(1)[0], blocks[0], "the groups themselves");
  assert.deepEqual(map.get("s1").map((b) => b.id), ["s"], "the sheet's own list replaces a position that names its id");
  assert.deepEqual(map.get("s2"), [], "a sheet with no ink has its key");
  assert.equal(groupsBySurface([group("x")], new Map()).size, 0, "a group with no position is on no PDF page");
});

test("a surface's name in messages: a PDF page's number, a sheet's place among the sheets, ? for one not among them", () => {
  const sheets = [{ id: "s1" }, { id: "s2" }];
  assert.equal(surfaceLabel(3, sheets), 3);
  assert.equal(surfaceLabel("s2", sheets), 2);
  assert.equal(surfaceLabel("s9", sheets), "?");
});

test("a paste without a point goes on the notebook's current sheet, else its last; else on the PDF page in view while a PDF shows", () => {
  const sheets = [{ id: "s1", paper: { width: 595, height: 842 } }, { id: "s2", paper: { width: 842, height: 595, pattern: "grid" } }];
  const inView = { page: 4, size: { width: 612, height: 792 } };
  const target = (patch) => pasteTargetOf({ notebook: false, sheets, current: null, inView, pdfUrl: "/a.pdf", pdfHidden: false, ...patch });
  assert.deepEqual(target({ notebook: true, current: "s1" }), { page: "s1", size: { width: 595, height: 842 } });
  assert.deepEqual(target({ notebook: true, current: "gone" }), { page: "s2", size: { width: 842, height: 595 } }, "else the last sheet, its paper's size");
  assert.equal(target({ notebook: true, sheets: [] }), null);
  assert.deepEqual(target({}), { page: 4, size: { width: 612, height: 792 } });
  assert.equal(target({ pdfHidden: true }), null, "the PDF closed");
  assert.equal(target({ pdfUrl: null }), null, "no PDF");
  assert.equal(target({ inView: null }), null, "no page reported in view");
});
