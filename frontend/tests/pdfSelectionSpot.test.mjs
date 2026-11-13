import test from "node:test";
import assert from "node:assert/strict";
import { highlightSpot } from "../src/pdf/pdfSelectionSpot.js";

test("a highlight's spot is its page and its box as page fractions", () => {
  const position = { pageNumber: 3, width: 600, height: 800, boundingRect: { x1: 60, y1: 200, x2: 540, y2: 260 } };
  assert.deepEqual(highlightSpot(position), { page: 3, box: [0.1, 0.25, 0.9, 0.325] });
});

test("a highlight without a usable box keeps its page", () => {
  assert.deepEqual(highlightSpot({ pageNumber: 2 }), { page: 2, box: null });
  assert.deepEqual(highlightSpot({ pageNumber: 4, width: 10, height: 10, boundingRect: { x1: 5, y1: 5, x2: 5, y2: 9 } }),
    { page: 4, box: null });
  assert.deepEqual(highlightSpot(null), { page: 0, box: null });
});

test("a range of a text file's spot is its lines, with no page and no box", () => {
  const position = { line: 118, col: 4, end: 120, end_col: 37, prefix: "", suffix: "", hash: "h1" };
  assert.deepEqual(highlightSpot(position), { page: 0, box: null, line: 118, end: 120 });
  assert.deepEqual(highlightSpot({ ...position, end: 118, end_col: 9 }), { page: 0, box: null, line: 118, end: 118 });
  assert.deepEqual(highlightSpot({ ...position, line: 0 }), { page: 0, box: null }, "a malformed one has no lines");
});
