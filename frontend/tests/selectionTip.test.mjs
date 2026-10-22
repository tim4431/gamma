// Where the PDF selection popup opens (src/pdf/selectionTip.js): at the
// mouse's release point when that is near the selection, else under it.
import test from "node:test";
import assert from "node:assert/strict";
import { POINT_GAP, POINT_LEFT, RECT_GAP, RELEASE_NEAR, nearRelease, tipAnchor } from "../src/pdf/selectionTip.js";
import { placeFloating } from "../src/shared/ui/placement.js";

const rect = { top: 100, left: 200, width: 300, bottom: 140 };

test("a release point inside or beside the selection is kept; one far off is not", () => {
  assert.deepEqual(nearRelease({ x: 480, y: 138 }, rect), { x: 480, y: 138 });
  assert.deepEqual(nearRelease({ x: 500 + RELEASE_NEAR, y: 140 + RELEASE_NEAR }, rect), { x: 560, y: 200 }, "at the limit");
  assert.equal(nearRelease({ x: 500 + RELEASE_NEAR + 1, y: 120 }, rect), null);
  assert.equal(nearRelease({ x: 0, y: 0 }, rect), null, "a mouseup with no position (a synthetic one)");
  assert.equal(nearRelease(null, rect), null, "a touch or keyboard selection");
  assert.equal(nearRelease({ x: NaN, y: 1 }, rect), null);
});

test("the anchor: an area at its tip, a mouse selection at its release point, any other under its rect", () => {
  assert.deepEqual(tipAnchor({ kind: "area", tip: { top: 50, left: 60 } }), { top: 50, bottom: 50, left: 60, gap: 0 });
  assert.deepEqual(tipAnchor({ rect, point: { x: 480, y: 138 } }),
    { top: 138, bottom: 138, left: 480 - POINT_LEFT, gap: POINT_GAP });
  assert.deepEqual(tipAnchor({ rect, point: null }), { top: 100, bottom: 140, left: 200, gap: RECT_GAP });
});

test("placed from the point: below it and a little left, above it at the window's bottom, inside the window", () => {
  const view = { width: 1000, height: 800 };
  const size = { width: 220, height: 44 };
  const at = (x, y) => {
    const a = tipAnchor({ rect, point: { x, y } });
    return placeFloating(size, { x: a.left, y: a.bottom + a.gap, flipY: a.top - a.gap }, view);
  };
  assert.deepEqual(at(480, 138), { left: 472, top: 150 });
  assert.deepEqual(at(480, 780), { left: 472, top: 780 - POINT_GAP - 44 }, "no room below: above the point");
  assert.deepEqual(at(990, 138), { left: 1000 - 8 - 220, top: 150 }, "kept inside the window's right edge");
});
