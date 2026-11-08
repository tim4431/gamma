// Where a floating surface goes from its measured size (shared/ui/placement.js):
// its own side of the point, the other side of what it opens from when its own
// has no room, and always inside the window or the band it is given; and the
// point a button's menu opens from (menuAnchor).
import assert from "node:assert/strict";
import { test } from "node:test";
import { flyoutPlace, growFrom, menuAnchor, placeFloating, surfaceSize } from "../src/shared/ui/placement.js";

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

test("a menu from a button: 4 px under or over it, its left edge on the button's, the other side its flip", () => {
  const button = { left: 300, right: 332, top: 400, bottom: 432 };
  assert.deepEqual(menuAnchor(button), { x: 300, y: 436, anchorBottom: false, flipY: 396 });
  assert.deepEqual(menuAnchor(button, { side: "above" }), { x: 300, y: 396, anchorBottom: true, flipY: 436 });
  // placed: the player's menu stands on its button, the strip's hangs under it
  assert.deepEqual(placeFloating(menu, menuAnchor(button, { side: "above" }), view), { left: 300, top: 276 });
  assert.deepEqual(placeFloating(menu, menuAnchor(button), view), { left: 300, top: 436 });
});

test("a menu from a button with no room on its side: on the button's other side, never over it", () => {
  // 120 px of menu under a button whose bottom is 100 px from the window's:
  // it opens above, its bottom edge 4 px over the button
  const low = { left: 300, right: 332, top: 668, bottom: 700 };
  const placed = placeFloating(menu, menuAnchor(low), view);
  assert.deepEqual(placed, { left: 300, top: 544 });
  assert.equal(placed.top + menu.height, low.top - 4);
  // and one that opens upward from a button near the top goes under it
  const high = { left: 300, right: 332, top: 40, bottom: 72 };
  assert.deepEqual(placeFloating(menu, menuAnchor(high, { side: "above" }), view), { left: 300, top: 76 });
  // neither side has room: kept inside the window from its own side's position
  const tall = { width: 200, height: 500 };
  const middle = { left: 300, right: 332, top: 380, bottom: 412 };
  assert.deepEqual(placeFloating(tall, menuAnchor(middle), view), { left: 300, top: 292 });
});

// A surface grows in from var(--scale-in) (motion.css): it is placed by the
// size it lays out at, not by its smaller rect mid-arrival.
test("surfaceSize: the rect without the surface's own arriving scale", () => {
  const was = globalThis.getComputedStyle;
  try {
    const el = { getBoundingClientRect: () => ({ width: 194, height: 97 }) };
    globalThis.getComputedStyle = () => ({ scale: "0.97" });
    assert.deepEqual(surfaceSize(el), { width: 200, height: 100 });
    globalThis.getComputedStyle = () => ({ scale: "none" });
    assert.deepEqual(surfaceSize(el), { width: 194, height: 97 });
  } finally {
    globalThis.getComputedStyle = was;
  }
});

test("growFrom: the origin at the point a surface opens from", () => {
  assert.equal(growFrom(100, 100, { left: 100, top: 100 }), "0px 0px", "at its top-left corner");
  assert.equal(growFrom(500, 300, { left: 300, top: 300 }), "200px 0px", "by its right edge");
  assert.equal(growFrom(950, 750, { left: 792, top: 672 }), "158px 78px", "kept inside the window: still from the point");
});

test("a flyout opens beside its row, on the left with room there alone, and over its menu with room on neither side", () => {
  const flyout = { width: 190, height: 363 };
  const nudge = { nudge: -4 };
  const row = (left, top, width = 250) => ({ left, right: left + width, top, width, height: 30 });
  assert.deepEqual(flyoutPlace(row(100, 100), flyout, nudge, view), { left: 250, top: -4 }, "room on the right");
  assert.deepEqual(flyoutPlace(row(700, 100), flyout, nudge, view), { left: -190, top: -4 }, "room on the left alone");
  // A phone 390 wide: the translate menu's row at 58, room on neither side (the
  // flyout ran to x 493 and cut the languages' names). Over the menu, under its
  // row, inside the window.
  const phone = { width: 390, height: 844 };
  assert.deepEqual(flyoutPlace(row(58, 400, 242), flyout, nudge, phone), { left: 0, top: 30 });
  assert.deepEqual(flyoutPlace(row(195, 100, 180), flyout, nudge, phone), { left: 192 - 195, top: 30 }, "shifted in from the right edge");
  // The translate menu low on the phone, its row at 617: no room under it, so
  // over it, never on the row, where the tap that opened it would land.
  assert.deepEqual(flyoutPlace(row(58, 617, 242), flyout, nudge, phone), { left: 0, top: -363 });
  assert.deepEqual(flyoutPlace(row(58, 300, 242), { width: 190, height: 700 }, nudge, phone), { left: 0, top: 844 - 8 - 700 - 300 },
    "room on neither side of the row: kept inside the window");
  assert.deepEqual(flyoutPlace(row(100, 700), flyout, nudge, view), { left: 250, top: 800 - 8 - 363 - 700 }, "slid up from the bottom");
});
