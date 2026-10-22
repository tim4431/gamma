import test from "node:test";
import assert from "node:assert/strict";
import {
  canvasLimits, canvasRatio, canvasSize, canvasWindow, windowHolds, windowMatrix, windowUnion, CANVAS_MAX_PIXELS, CANVAS_MAX_EDGE,
} from "../src/shared/lib/canvasSize.js";

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
test("the limits stay iPad-sized on WebKit and touch-first devices, and grow on a desktop", () => {
  const desktop = { matchMedia: () => ({ matches: false }) };
  const small = { pixels: 8 * 1024 * 1024, edge: 4096 };
  assert.deepEqual(canvasLimits(desktop), { pixels: 16 * 1024 * 1024, edge: 8192 });
  assert.deepEqual(canvasLimits({ ...desktop, GestureEvent: class {} }), small, "Safari, and every iPad browser");
  assert.deepEqual(canvasLimits({ matchMedia: (q) => ({ matches: q === "(pointer: coarse)" }) }), small, "a phone or tablet");
  assert.deepEqual(canvasLimits(null), small, "no window");
  assert.deepEqual([CANVAS_MAX_PIXELS, CANVAS_MAX_EDGE], [small.pixels, small.edge], "node has no window");
});
test("a canvas window is where the box meets its view, out to whole pixels; null when none of it shows", () => {
  const view = { left: 0, top: 600, right: 1000, bottom: 755 };
  // the zoom window's page at 4× (a 612 × 792 pt page, 2448 × 3168 css px), moved so its window shows
  const page = { left: -800.4, top: -200.25, right: -800.4 + 2448, bottom: -200.25 + 3168 };
  assert.deepEqual(canvasWindow(page, view), { x: 800, y: 800, width: 1001, height: 156 });
  // a box wholly in view is its whole self
  const small = { left: 10.5, top: 620, right: 210.5, bottom: 700 };
  assert.deepEqual(canvasWindow(small, view), { x: 0, y: 0, width: 200, height: 80 });
  // above, beside or touching the view: nothing
  assert.equal(canvasWindow({ left: 0, top: 0, right: 500, bottom: 600 }, view), null);
  assert.equal(canvasWindow({ left: 1000, top: 600, right: 1200, bottom: 700 }, view), null);
  // what it buys: the window at the device's ratio, where the whole page is held to about 1 by the caps
  const win = canvasWindow(page, view), size = canvasSize(win.width, win.height, 2);
  assert.deepEqual(size, { width: 2002, height: 312 });
  assert(canvasRatio(2448, 3168, 2) < 1.5, "the whole magnified page under the caps");
});
test("windows hold, join and map page points to their canvas's pixels", () => {
  const a = { x: 10, y: 20, width: 100, height: 50 };
  assert(windowHolds(a, a));
  assert(windowHolds(a, { x: 20, y: 30, width: 10, height: 10 }));
  assert(!windowHolds(a, { x: 5, y: 30, width: 10, height: 10 }));
  assert(!windowHolds(a, { x: 100, y: 30, width: 20, height: 10 }));
  assert.deepEqual(windowUnion(a, { x: 0, y: 40, width: 50, height: 100 }), { x: 0, y: 20, width: 110, height: 120 });
  // a canvas of 200 × 100 pixels on that window, 3 css px a point: a point maps to (k·p − win) × ratio
  const [sx, b, c, sy, e, f] = windowMatrix({ width: 200, height: 100 }, a, 3);
  assert.deepEqual([b, c], [0, 0]);
  const px = (x, y) => [sx * x + e, sy * y + f];
  assert.deepEqual(px(10 / 3, 20 / 3).map((v) => Math.round(v * 1e9) / 1e9), [0, 0], "the window's corner is the canvas's");
  assert.deepEqual(px(110 / 3, 70 / 3).map((v) => Math.round(v * 1e9) / 1e9), [200, 100], "and its far corner the canvas's");
});
