// Where the PDF selection popup opens (src/pdf/selectionTip.js): at the
// mouse's release point when that is near the selection, else under it.
import test from "node:test";
import assert from "node:assert/strict";
import { MOVE_TO_SHOW, POINT_GAP, POINT_LEFT, RECT_GAP, RELEASE_NEAR, inTextLayer, nearRelease, tipAnchor, tipWaiter } from "../src/pdf/selectionTip.js";
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
  // A pen's or a finger's selection in a translation: under the reflowed text
  // selected (the second part of a paragraph a column break cut, in the right
  // column), not under the original lines it keeps, which start in the left.
  const shown = { top: 154, bottom: 166, left: 430, width: 120 };
  assert.deepEqual(tipAnchor({ rect, shown, point: null }), { top: 154, bottom: 166, left: 430, gap: RECT_GAP });
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

test("a mouse selection's popup opens once the hand moves on from the release, also when it moved before the release was read", () => {
  const opened = [];
  const tip = { point: { x: 100, y: 100 } };
  const waiter = tipWaiter((t) => opened.push(t));
  waiter.wait(tip);
  waiter.move(101, 101);
  assert.equal(opened.length, 0, "a move or two of a pixel keeps the text bare");
  waiter.move(100 + MOVE_TO_SHOW, 100);
  assert.deepEqual(opened, [tip]);
  waiter.move(200, 200);
  assert.equal(opened.length, 1, "opened once");

  // The release is read a timer after the mouseup: a hand that moved on
  // in between, and rests, still has the popup.
  waiter.press();
  waiter.move(106, 108);
  waiter.wait(tip);
  assert.equal(opened.length, 2);

  // A press drops a waiting tip, and where the mouse had moved before it.
  waiter.press();
  waiter.wait(tip);
  waiter.press();
  waiter.move(200, 200);
  assert.equal(opened.length, 2, "a press drops the waiting tip");
  waiter.press();
  waiter.wait(tip);
  assert.equal(opened.length, 2, "a move before the press is not counted");
  // A new reading of the selection drops the tip alone.
  waiter.drop();
  waiter.move(300, 300);
  assert.equal(opened.length, 2, "dropped");
});

test("the PDF's text selection is one that starts in a text layer, or has an end in a translation, and is not collapsed", () => {
  const layer = { closest: (s) => (s === ".textLayer" ? layer : null) };
  const note = { closest: () => null };
  const span = { nodeType: 1, closest: (s) => (s === ".textLayer" ? layer : null) };
  const words = { nodeType: 1, closest: (s) => (s === ".pdfTransText" ? words : null) };
  const text = (parent) => ({ nodeType: 3, parentElement: parent });
  const sel = (start, more = {}) => ({ isCollapsed: false, rangeCount: 1, getRangeAt: () => ({ startContainer: start }),
    anchorNode: start, focusNode: start, ...more });
  assert.equal(inTextLayer(sel(text(span))), true, "a text node in a page's span");
  assert.equal(inTextLayer(sel(span)), true, "an element of the layer");
  assert.equal(inTextLayer(sel(text(note))), false, "a note's text");
  assert.equal(inTextLayer(sel(text(words))), true, "a translation's text");
  assert.equal(inTextLayer(sel(text(words), { anchorNode: text(span) })), true, "a drag from the original into a translation");
  assert.equal(inTextLayer(sel(text(note), { focusNode: text(words) })), true, "an end in a translation");
  assert.equal(inTextLayer(sel(text(span), { isCollapsed: true })), false, "a caret");
  assert.equal(inTextLayer(sel(text(span), { rangeCount: 0 })), false, "no range");
  assert.equal(inTextLayer(null), false, "no selection");
});
