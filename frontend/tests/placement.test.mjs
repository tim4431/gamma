// Where a floating surface goes from its measured size (shared/ui/placement.js):
// its own side of the point, the other side of what it opens from when its own
// has no room, and always inside the window or the band it is given; and the
// point a button's menu opens from (menuAnchor).
import assert from "node:assert/strict";
import { test } from "node:test";
import { menuAnchor, placeFloating } from "../src/shared/ui/placement.js";

const view = { width: 1000, height: 800 };
const menu = { width: 200, height: 120 };

test("a menu sits at its point, by its right or bottom edge when asked", () => {
  assert.deepEqual(placeFloating(menu, { x: 100, y: 100 }, view), { left: 100, top: 100 });
  assert.deepEqual(placeFloating(menu, { x: 500, y: 300, anchorRight: true }, view), { left: 300, top: 300 });
  assert.deepEqual(placeFloating(menu, { x: 100, y: 600, anchorBottom: true }, view), { left: 100, top: 480 });
});

test("a menu with no room is shifted inside the window", () => {
  assert.deepEqual(placeFloating(menu, { x: 950, y: 750 }, view), { left: 792, top: 672 });
  assert.deepEqual(placeFloating(menu, { x: -40, y: 50, anchorBottom: true }, view), { left: 8, top: 8 });
});

test("flipY: the other side when its own has no room and that one has", () => {
  // Opening downward from y = 740, under a selection whose top is at 700:
  // no room below, so it goes above, its bottom edge at 700.
  assert.deepEqual(placeFloating(menu, { x: 100, y: 740, flipY: 700 }, view), { left: 100, top: 580 });
  // Opening upward from y = 60 (above a selection whose bottom is at 100):
  // no room above, so it goes below, its top edge at 100.
  assert.deepEqual(placeFloating(menu, { x: 100, y: 60, anchorBottom: true, flipY: 100 }, view), { left: 100, top: 100 });
  // Room on its own side: the flip is never taken.
  assert.deepEqual(placeFloating(menu, { x: 100, y: 300, anchorBottom: true, flipY: 340 }, view), { left: 100, top: 180 });
});

test("no room on either side: kept inside on its own side's terms", () => {
  const tall = { width: 200, height: 500 };
  // Above would start at 400 - 40 - 500 < 8, below at 440 + 500 > 792:
  // clamped from the upward position into the window.
  assert.deepEqual(placeFloating(tall, { x: 100, y: 360, anchorBottom: true, flipY: 440 }, view), { left: 100, top: 8 });
});

test("bounds: the band a menu stays in, such as a scroller under a tool strip", () => {
  const bounds = { top: 150, bottom: 700 };
  // Above the selection would reach into the strip (top 150 + pad), so it flips below.
  assert.deepEqual(placeFloating(menu, { x: 100, y: 260, anchorBottom: true, flipY: 320, bounds }, view), { left: 100, top: 320 });
  // Room above inside the band: it stays above.
  assert.deepEqual(placeFloating(menu, { x: 100, y: 300, anchorBottom: true, flipY: 360, bounds }, view), { left: 100, top: 180 });
  // Neither fits: clamped into the band, its top under the strip.
  assert.deepEqual(placeFloating({ width: 200, height: 400 }, { x: 100, y: 300, anchorBottom: true, flipY: 400, bounds }, view), { left: 100, top: 158 });
});

test("a menu from a button: 4 px under or over it, by the button's left or right edge", () => {
  const button = { left: 300, right: 332, top: 400, bottom: 432 };
  assert.deepEqual(menuAnchor(button), { x: 300, y: 436, anchorRight: false, anchorBottom: false });
  assert.deepEqual(menuAnchor(button, { side: "above" }), { x: 300, y: 396, anchorRight: false, anchorBottom: true });
  assert.deepEqual(menuAnchor(button, { align: "right" }), { x: 332, y: 436, anchorRight: true, anchorBottom: false });
  // placed: the player's menu stands on its button, the strip's hangs under
  // it, and a right-aligned one ends at the button's right edge
  assert.deepEqual(placeFloating(menu, menuAnchor(button, { side: "above" }), view), { left: 300, top: 276 });
  assert.deepEqual(placeFloating(menu, menuAnchor(button), view), { left: 300, top: 436 });
  assert.deepEqual(placeFloating(menu, menuAnchor(button, { align: "right" }), view), { left: 132, top: 436 });
});
