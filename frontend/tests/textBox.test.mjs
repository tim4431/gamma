// node --test tests/textBox.test.mjs (from frontend/) — the text-box rules.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import {
  LINE, MIN_WIDTH, PAD, isTextBox, mergeTextBox, moveBox, normalizeTextBox, placeBox, refitBox, resizeBox, textBoxesBySurface,
  textStyle,
} from "../src/markup/textBox.js";

// tests/shared/textbox.json: the same cases gamma/text_box.py passes
// (backend/tests/test_shared_fixtures.py).
const TEXTBOX = JSON.parse(readFileSync(new URL("../../tests/shared/textbox.json", import.meta.url), "utf8"));
for (const c of TEXTBOX.normalize) {
  test(`text box: ${c.note}`, () => assert.deepEqual(normalizeTextBox(c.input), c.output));
}
// tests/shared/textboxmerge.json: two writers, one box (gamma/text_box.py merge_text_box).
const MERGE = JSON.parse(readFileSync(new URL("../../tests/shared/textboxmerge.json", import.meta.url), "utf8"));
for (const c of MERGE.merge) {
  test(`text box merge: ${c.note}`, () => assert.deepEqual(mergeTextBox(c.stored, c.mine, c.base), c.result));
}

test("a block is a text box when its text_box is an object", () => {
  assert.equal(isTextBox({ id: "a", properties: { text_box: {} } }), true);
  assert.equal(isTextBox({ id: "a", properties: { text_box: { x: "far" } } }), true);
  for (const text_box of [null, undefined, "box", 3, true, []]) {
    assert.equal(isTextBox({ id: "a", properties: { text_box } }), false, String(text_box));
  }
  assert.equal(isTextBox({ id: "a" }), false);
  assert.equal(isTextBox(null), false);
});

const box = (id, props = {}, children = []) => ({ id, content: id, properties: { text_box: { x: 10, y: 20 }, ...props }, children });
const note = (id, props = {}, children = []) => ({ id, content: id, properties: props, children });
const sheet = (id, children = []) => ({ id, content: "", properties: { sheet: {} }, children });

test("a PDF page's boxes key by pdf_page, in document order, wherever they sit in the notes", () => {
  const b1 = box("b1", { pdf_page: 2 }), b2 = box("b2", { pdf_page: 1 });
  const moved = box("moved", { pdf_page: 2 });
  const tree = [
    b1,
    note("hl", { pdf_position: { pageNumber: 2 } }, [moved]), // moved under a highlight: still on its page
    b2,
    note("ink", { pdf_position: { pageNumber: 1 }, ink_url: "/api/uploads/a.ink" }),
    box("loose"), // no page, no sheet: on no surface
    box("bad", { pdf_page: "3" }),
    box("zero", { pdf_page: 0 }),
  ];
  const map = textBoxesBySurface(tree);
  assert.deepEqual([...map.keys()].sort(), [1, 2]);
  assert.deepEqual(map.get(2).map((b) => b.id), ["b1", "moved"]);
  assert.deepEqual(map.get(1).map((b) => b.id), ["b2"]);
  // the tree's own objects, so an unchanged box keeps its identity
  assert.equal(map.get(2)[0], b1);
  assert.equal(map.get(2)[1], moved);
});

test("a sheet's boxes are those under it that no nearer sheet holds", () => {
  const tree = [
    note("intro"),
    sheet("s1", [
      box("t1"),
      note("why", {}, [box("t2")]),
      sheet("s2", [box("t3"), box("stale", { pdf_page: 4 })]), // the nearest sheet decides, not a leftover pdf_page
      box("t4"),
    ]),
    note("bullet", {}, [sheet("s3"), box("after")]), // after s3 in document order, but not under it
  ];
  const map = textBoxesBySurface(tree);
  assert.deepEqual([...map.keys()], ["s1", "s2"]);
  assert.deepEqual(map.get("s1").map((b) => b.id), ["t1", "t2", "t4"]);
  assert.deepEqual(map.get("s2").map((b) => b.id), ["t3", "stale"]);
  assert.equal(map.has("s3"), false); // a surface without boxes has no entry
  assert.equal(textBoxesBySurface([]).size, 0);
});

test("the style of new boxes reads like a box's, the rest dropped", () => {
  assert.deepEqual(textStyle({}), { size: 12, color: "#1f1f1f", bg: null });
  assert.deepEqual(textStyle({ size: 24, color: "#DC2626", bg: "#fff4b8", x: 5 }), { size: 24, color: "#dc2626", bg: "#fff4b8" });
  for (const raw of [null, "big", [], 7]) assert.deepEqual(textStyle(raw), textStyle({}), String(raw));
});

const page = { width: 612, height: 792 };
const style = { size: 16, color: "#1d4ed8", bg: null };
const line = 16 * LINE + 2 * PAD; // one line of 16 pt text, padding included

test("a tap puts an auto-width box's first line under the point", () => {
  const box = placeBox(page, { x: 100, y: 200 }, null, style);
  assert.deepEqual(box, { x: 100 - PAD, y: 200 - line / 2, w: MIN_WIDTH, h: line, auto: true, size: 16, color: "#1d4ed8", bg: null });
});

test("a drag makes a box as wide as the drag, either way, with its first line at the start", () => {
  const right = placeBox(page, { x: 100, y: 200 }, { x: 260, y: 230 }, style);
  assert.deepEqual([right.x, right.y, right.w, right.auto], [100, 200 - line / 2, 160, false]);
  const left = placeBox(page, { x: 260, y: 200 }, { x: 100, y: 180 }, style);
  assert.deepEqual([left.x, left.w], [100, 160]);
  assert.equal(placeBox(page, { x: 100, y: 200 }, { x: 108, y: 200 }, style).w, MIN_WIDTH, "at least the least width");
});

test("a new box stays inside its surface", () => {
  const corner = placeBox(page, { x: 1, y: 1 }, null, style);
  assert.deepEqual([corner.x, corner.y], [0, 0]);
  const far = placeBox(page, { x: 611, y: 791 }, null, style);
  assert.deepEqual([far.x, far.y], [612 - MIN_WIDTH, Math.round((792 - line) * 100) / 100]);
  const wide = placeBox({ width: 300, height: 400 }, { x: 250, y: 50 }, { x: 900, y: 50 }, style);
  assert.deepEqual([wide.x, wide.w], [0, 300], "a drag past the edge: the surface's width, from its left");
});

test("refitting stores the measured height, and the width only of an auto box, up to the surface's edge", () => {
  const auto = normalizeTextBox({ x: 500, y: 10, w: 40, h: 23, auto: true });
  assert.deepEqual(refitBox(auto, { w: 80.123, h: 38.5 }, 612), { ...auto, w: 80.12, h: 38.5 });
  assert.equal(refitBox(auto, { w: 400, h: 23 }, 612).w, 112, "no further than the right edge");
  const fixed = { ...auto, auto: false };
  assert.deepEqual(refitBox(fixed, { w: 80, h: 60 }, 612), { ...fixed, h: 60 });
});

test("refitting what a box stores, to within half a point, changes nothing", () => {
  const box = normalizeTextBox({ x: 10, y: 10, w: 80, h: 23, auto: true });
  assert.equal(refitBox(box, { w: 80.4, h: 22.6 }, 612), box);
  assert.notEqual(refitBox(box, { w: 80.5, h: 23 }, 612), box);
});

test("a move keeps the box inside its surface, by the size it is drawn at", () => {
  const box = normalizeTextBox({ x: 100, y: 100, w: 80, h: 23, auto: true });
  assert.deepEqual([moveBox(box, 12.345, -40, page).x, moveBox(box, 12.345, -40, page).y], [112.35, 60]);
  const far = moveBox(box, 1000, 1000, page);
  assert.deepEqual([far.x, far.y], [612 - 80, 792 - 23]);
  const tall = moveBox(box, 0, 1000, page, { w: 80, h: 200 }); // its stored height is stale: the text is taller
  assert.equal(tall.y, 792 - 200);
  assert.deepEqual([moveBox(box, -500, -500, page).x, moveBox(box, -500, -500, page).y], [0, 0]);
  assert.equal(moveBox(box, 0, 0, page).auto, true, "a move keeps the rest");
});

test("the width handle fixes the width, from the least width to the surface's edge", () => {
  const box = normalizeTextBox({ x: 500, y: 10, w: 40, h: 23, auto: true });
  assert.deepEqual(resizeBox(box, 90.456, page), { ...box, w: 90.46, auto: false });
  assert.equal(resizeBox(box, 5, page).w, MIN_WIDTH);
  assert.equal(resizeBox(box, 400, page).w, 112, "no further than the right edge");
});
