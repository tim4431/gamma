// A region drawn on a picture (src/shared/ui/regionGeometry.js): the drag's
// rectangle in the box's px, the click threshold, the picture's frame, the
// stored image_position in its own pixels and the fractions it is drawn by.
import test from "node:test";
import assert from "node:assert/strict";
import { MIN_DRAG_PX, dragRect, imageFrame, isClick, regionFractions, toImagePosition } from "../src/shared/ui/regionGeometry.js";

const box = { left: 100, top: 50, width: 600, height: 400 };

test("dragRect orders the corners and clamps to the box", () => {
  assert.deepEqual(dragRect(150, 100, 400, 300, box), { x1: 50, y1: 50, x2: 300, y2: 250 });
  assert.deepEqual(dragRect(400, 300, 150, 100, box), { x1: 50, y1: 50, x2: 300, y2: 250 }, "dragged up and left");
  assert.deepEqual(dragRect(0, 0, 10000, 10000, box), { x1: 0, y1: 0, x2: 600, y2: 400 }, "past the box on every side");
});

test("a drag under 6 px either way is a click", () => {
  assert.equal(MIN_DRAG_PX, 6);
  assert.equal(isClick({ x1: 0, y1: 0, x2: 5, y2: 100 }), true, "too narrow");
  assert.equal(isClick({ x1: 0, y1: 0, x2: 100, y2: 5 }), true, "too low");
  assert.equal(isClick({ x1: 0, y1: 0, x2: 6, y2: 6 }), false, "just a region");
  assert.equal(isClick({ x1: 0, y1: 0, x2: 8, y2: 8 }, 10), true, "the caller's threshold");
});

test("imageFrame is the picture's own pixels, else the box at scale 1 for an SVG with no intrinsic size", () => {
  assert.deepEqual(imageFrame({ width: 2400, height: 1600 }, { width: 600, height: 400 }), { width: 2400, height: 1600 });
  assert.deepEqual(imageFrame({ width: 0, height: 0 }, { width: 640, height: 480 }), { width: 640, height: 480 });
  assert.deepEqual(imageFrame(null, { width: 640.4, height: 479.6 }), { width: 640, height: 480 }, "whole pixels");
  assert.equal(imageFrame({ width: 0, height: 0 }, { width: 0, height: 0 }), null, "nothing measured yet");
});

test("toImagePosition maps the box's px into the picture's pixels, whole and clamped, one rect equal to its box, with the file's hash", () => {
  const frame = { width: 2400, height: 1600 };
  const pos = toImagePosition({ x1: 60, y1: 40, x2: 300, y2: 200 }, { width: 600, height: 400 }, frame, "9f2c");
  assert.deepEqual(pos, {
    width: 2400, height: 1600, boundingRect: { x1: 240, y1: 160, x2: 1200, y2: 800 }, rects: [{ x1: 240, y1: 160, x2: 1200, y2: 800 }], hash: "9f2c",
  });
  assert.notEqual(pos.rects[0], pos.boundingRect, "the rect is its own object");
  assert.deepEqual(toImagePosition({ x1: 0.3, y1: 0.2, x2: 599.9, y2: 399.9 }, { width: 600, height: 400 }, frame, "h").boundingRect,
    { x1: 1, y1: 1, x2: 2400, y2: 1600 }, "rounded to whole pixels, within the frame");
  assert.deepEqual(toImagePosition({ x1: -5, y1: -5, x2: 700, y2: 500 }, { width: 600, height: 400 }, frame, "h").boundingRect,
    { x1: 0, y1: 0, x2: 2400, y2: 1600 }, "clamped");
  assert.equal(toImagePosition({ x1: 10, y1: 10, x2: 10.1, y2: 50 }, { width: 600, height: 400 }, frame, "h"), null, "nothing wide once rounded");
  assert.equal(toImagePosition({ x1: 0, y1: 0, x2: 10, y2: 10 }, { width: 0, height: 0 }, frame, "h"), null, "a box of no size");
  assert.equal(toImagePosition({ x1: 0, y1: 0, x2: 10, y2: 10 }, { width: 600, height: 400 }, null, "h"), null, "no frame yet");
  assert.equal(toImagePosition({ x1: 0, y1: 0, x2: 10, y2: 10 }, { width: 600, height: 400 }, frame, undefined).hash, "", "a hash is always a string");
  // the picture drawn at another size: the same fractions
  const zoomed = toImagePosition({ x1: 120, y1: 80, x2: 600, y2: 400 }, { width: 1200, height: 800 }, frame, "9f2c");
  assert.deepEqual(zoomed.boundingRect, pos.boundingRect);
});

test("regionFractions draws a region by fractions of its frame, whatever size the picture shows at", () => {
  const pos = { width: 2400, height: 1600, boundingRect: { x1: 240, y1: 160, x2: 1200, y2: 800 }, rects: [{ x1: 240, y1: 160, x2: 1200, y2: 800 }], hash: "h" };
  assert.deepEqual(regionFractions(pos), { left: 0.1, top: 0.1, width: 0.4, height: 0.4 });
  assert.deepEqual(regionFractions({ width: 100, height: 100, boundingRect: { x1: -10, y1: 25, x2: 150, y2: 75 } }), { left: 0, top: 0.25, width: 1, height: 0.5 }, "clamped");
  assert.equal(regionFractions({ width: 100, height: 100, boundingRect: { x1: 50, y1: 50, x2: 50, y2: 60 } }), null, "no width");
  assert.equal(regionFractions({ width: 0, height: 100, boundingRect: { x1: 0, y1: 0, x2: 10, y2: 10 } }), null, "no frame");
  assert.equal(regionFractions({ width: 100, height: 100 }), null);
  assert.equal(regionFractions(null), null);
});
