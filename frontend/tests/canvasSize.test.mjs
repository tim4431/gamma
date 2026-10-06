import test from "node:test";
import assert from "node:assert/strict";
import { canvasRatio, canvasSize, CANVAS_MAX_PIXELS, CANVAS_MAX_EDGE } from "../src/shared/lib/canvasSize.js";

test("normal pages retain high-DPI resolution", () => {
  assert.deepEqual(canvasSize(612, 792, 2), { width: 1224, height: 1584 });
});
test("400% pages and oversized formats stay inside both canvas limits", () => {
  for (const [w, h] of [[2448, 3168], [10000, 14000], [400, 50000], [50000, 400]]) {
    for (const dpr of [1, 2, 3]) {
      const size = canvasSize(w, h, dpr);
      assert(size.width * size.height <= CANVAS_MAX_PIXELS);
      assert(size.width <= CANVAS_MAX_EDGE && size.height <= CANVAS_MAX_EDGE);
      assert(Math.abs(size.width / w - size.height / h) <= 1 / Math.min(w, h));
    }
  }
  assert(canvasSize(10000, 14000, 2).width < 10000, "can reduce backing below CSS resolution");
});
test("canvasRatio is the ratio asked for until a limit bites", () => {
  assert.equal(canvasRatio(612, 792, 2), 2);
  assert.equal(canvasRatio(612, 792), 1);
  assert.equal(canvasRatio(612, 792, 0), 1, "a missing ratio counts as 1");
  assert.equal(canvasRatio(2448, 3168, 2), Math.sqrt(CANVAS_MAX_PIXELS / 2448 / 3168), "the area limit");
  assert.equal(canvasRatio(400, 50000, 2), CANVAS_MAX_EDGE / 50000, "the edge limit");
  assert.equal(canvasRatio(50000, 400, 2), CANVAS_MAX_EDGE / 50000);
});
test("canvasRatio is 0 for what is not a box", () => {
  for (const [w, h] of [[0, 792], [612, 0], [-612, 792], [NaN, 792], [612, Infinity]]) assert.equal(canvasRatio(w, h, 2), 0);
  assert.deepEqual(canvasSize(0, 792, 2), { width: 1, height: 1 });
});
test("canvasSize is the box at canvasRatio, rounded down", () => {
  for (const [w, h] of [[612, 792], [595.28, 841.89], [2448, 3168], [400, 50000], [10000, 14000]]) {
    for (const ratio of [1, 1.5, 2, 3]) {
      const r = canvasRatio(w, h, ratio), size = canvasSize(w, h, ratio);
      assert.equal(size.width, Math.floor(w * r));
      assert.equal(size.height, Math.floor(h * r));
    }
  }
});
