// node --test tests/  (from frontend/) — the zoom window's rules
// (ink/zoomWindow.js): where it opens, its size from the panel, the
// auto-advance along a line and onto the next, the line keys, a move by
// hand, the magnification and the line height, every answer on its page.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  ADVANCE_AT, ADVANCE_BY, ZOOM_LINE, ZOOM_MAG, ZOOM_MAGS, advance, clampWindow, magForWidth, moveTo, moveWindow, newLine,
  nextLine, openWindow, prevLine, revealDelta, setLine, setMag, sized,
} from "../src/ink/zoomWindow.js";

const PAGE = { width: 612, height: 792 };
const PANEL = { width: 600, height: 150 };   // the view: 200 × 50 pt at 3×
const win = (over = {}) => ({ page: 1, x: 30, y: 100, w: 200, h: 50, mag: 3, line: 24, x0: 30, ...over });

test("a window opens at 3× on the part of the page in view, a twentieth of the page in, its middle at the view's", () => {
  assert.deepEqual(ZOOM_MAGS, [2, 3, 4]);
  assert.equal(ZOOM_MAG, 3);
  const w = openWindow(2, PAGE, { x: 0, y: 300, width: 612, height: 400 }, PANEL);
  assert.deepEqual(w, { page: 2, x: 30.6, y: 475, w: 200, h: 50, mag: 3, line: ZOOM_LINE, x0: 30.6 });
  // a page scrolled sideways: from the left of what shows
  assert.equal(openWindow(1, PAGE, { x: 100, y: 0, width: 300, height: 200 }, PANEL).x, 130.6);
  // no view known: the whole page; a window low on the page stays on it
  assert.equal(openWindow(1, PAGE, null, PANEL).y, 371);
  assert.equal(openWindow(1, PAGE, { x: 0, y: 780, width: 612, height: 12 }, PANEL).y, 742);
});

test("clamping keeps the window and its line start on the page, and gives back the same window when nothing changes", () => {
  const ok = win();
  assert.equal(clampWindow(ok, PAGE), ok);
  assert.deepEqual(clampWindow(win({ x: 500, y: -5, x0: 600 }), PAGE), win({ x: 412, y: 0, x0: 412 }));
  // wider than its page: its top-left corner stays on it
  assert.deepEqual(clampWindow(win({ w: 700, x: 50, x0: 50 }), PAGE), win({ w: 700, x: 0, x0: 0 }));
});

test("the panel's size sets w and h at the window's magnification, the top-left kept", () => {
  const w = win();
  assert.equal(sized(w, PANEL, PAGE), w, "the same view: the same window");
  assert.deepEqual(sized(w, { width: 900, height: 240 }, PAGE), win({ w: 300, h: 80 }));
  assert.equal(sized(w, { width: 0, height: 10 }, PAGE), w, "a view not laid out yet changes nothing");
  assert.deepEqual(sized(win({ x: 400, x0: 400 }), { width: 900, height: 150 }, PAGE), win({ x: 312, x0: 312, w: 300 }));
});

test("a stroke reaching into the window's right fifth moves it on by three fifths of its width", () => {
  const w = win();
  assert.equal(ADVANCE_AT, 0.8);
  assert.equal(ADVANCE_BY, 0.6);
  assert.equal(advance(w, [40, 110, 30 + 160, 130], PAGE), w, "up to 80 % of the width: no move");
  assert.deepEqual(advance(w, [40, 110, 30 + 161, 130], PAGE), win({ x: 150 }), "past it: x + 0.6 w, the line start kept");
  assert.equal(advance(w, null, PAGE), w);
  // the next three steps along the line
  let at = w;
  for (const x of [150, 270, 390]) { at = advance(at, [0, 0, at.x + at.w, 0], PAGE); assert.equal(at.x, x); }
  assert.equal(at.y, 100, "along the line, y stays");
});

test("past the page's right edge the window goes back to where the line began, one line down", () => {
  const w = win({ x: 390 });   // 390 + 120 + 200 > 612
  assert.deepEqual(advance(w, [500, 110, 580, 130], PAGE), win({ x: 30, y: 124 }));
  assert.deepEqual(advance(win({ x: 412 }), [600, 0, 611, 1], PAGE), win({ x: 30, y: 124 }));
});

test("New line goes back to the line start one line down; Next and Previous keep x; at the page's bottom New line stops", () => {
  const w = win({ x: 270 });
  assert.deepEqual(newLine(w, PAGE), win({ x: 30, y: 124 }));
  assert.deepEqual(nextLine(w, PAGE), win({ x: 270, y: 124 }));
  assert.deepEqual(prevLine(w, PAGE), win({ x: 270, y: 76 }));
  assert.deepEqual(prevLine(win({ y: 10 }), PAGE), win({ y: 0 }), "up to the page's top");
  const low = win({ x: 270, y: 730 });
  assert.deepEqual(newLine(low, PAGE), win({ x: 30, y: 742 }), "less than a line left: down to the bottom");
  const bottom = win({ x: 270, y: 742 });
  assert.equal(newLine(bottom, PAGE), bottom, "at the bottom: nothing moves");
  assert.equal(advance(win({ x: 412, y: 742 }), [600, 742, 611, 790], PAGE).y, 742);
  assert.equal(nextLine(bottom, PAGE), bottom);
});

test("a move by hand sets where the line begins; a tap centres the window, on another page too", () => {
  assert.deepEqual(moveWindow(win(), { x: 80, y: 300 }, PAGE), win({ x: 80, y: 300, x0: 80 }));
  assert.deepEqual(moveWindow(win(), { x: -20, y: 900 }, PAGE), win({ x: 0, y: 742, x0: 0 }));
  assert.deepEqual(moveTo(win(), 1, { x: 306, y: 400 }, PAGE), win({ x: 206, y: 375, x0: 206 }));
  const small = { width: 300, height: 300 };
  assert.deepEqual(moveTo(win(), 4, { x: 290, y: 5 }, small), win({ page: 4, x: 100, y: 0, x0: 100 }));
});

test("the magnification is held to 2…4, sizes the window from the panel and keeps the left edge and the line's middle", () => {
  const w = win();
  assert.equal(setMag(w, 3, PANEL, PAGE), w);
  assert.deepEqual(setMag(w, 2, PANEL, PAGE), win({ mag: 2, w: 300, h: 75, y: 87.5 }));
  assert.deepEqual(setMag(w, 4, PANEL, PAGE), win({ mag: 4, w: 150, h: 37.5, y: 106.25 }));
  assert.equal(setMag(w, 9, PANEL, PAGE).mag, 4);
  assert.equal(setMag(w, 0.5, PANEL, PAGE).mag, 2);
  // without a panel, the window's own w × mag is the view
  assert.deepEqual(setMag(w, 2, null, PAGE), setMag(w, 2, PANEL, PAGE));
  // the page's resize corner: the width the window is dragged to
  assert.equal(magForWidth(w, 300), 2);
  assert.equal(magForWidth(w, 150), 4);
});

test("the line height steps by 2 pt between 8 and 96", () => {
  const w = win();
  assert.equal(setLine(w, 24), w);
  assert.equal(setLine(w, 26).line, 26);
  assert.equal(setLine(w, 25).line, 26);
  assert.equal(setLine(w, 2).line, 8);
  assert.equal(setLine(w, 200).line, 96);
});

test("revealing the window scrolls only as far as it must", () => {
  const view = { left: 0, top: 0, right: 800, bottom: 600 };
  assert.deepEqual(revealDelta({ left: 100, top: 100, right: 300, bottom: 200 }, view), { dx: 0, dy: 0 });
  assert.deepEqual(revealDelta({ left: 100, top: 650, right: 300, bottom: 700 }, view), { dx: 0, dy: 116 });
  assert.deepEqual(revealDelta({ left: 100, top: -80, right: 300, bottom: -30 }, view), { dx: 0, dy: -96 });
  // taller than the view: its top first
  assert.deepEqual(revealDelta({ left: 100, top: 700, right: 300, bottom: 1500 }, view), { dx: 0, dy: 684 });
});
