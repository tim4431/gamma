// How a page's blocks sit in their columns (src/pdf/pageShape.js).
import test from "node:test";
import assert from "node:assert/strict";
import { footOf, isCentred, markShape, roomOf } from "../src/pdf/pageShape.js";
import { segmentPage } from "../src/pdf/pdfTranslate.js";

const COLUMN = { x1: 54, x2: 300 };
// A block of lines given as [x1, x2], 12 pt apart.
const block = (lines, over = {}) => ({
  size: 10, nLines: lines.length, text: "Words", translate: true,
  x1: Math.min(...lines.map((l) => l[0])), x2: Math.max(...lines.map((l) => l[1])), y1: 100, y2: 100 + lines.length * 12,
  lines: lines.map(([x1, x2], i) => ({ x1, x2, y1: 100 + i * 12, y2: 110 + i * 12 })), ...over,
});

test("a heading centred in its column is centred, a short left-aligned line is not", () => {
  assert.equal(isCentred(block([[130, 224]]), COLUMN), true, "the column's middle is 177");
  assert.equal(isCentred(block([[54, 120]]), COLUMN), false, "short, at the column's left edge");
  assert.equal(isCentred(block([[64, 120]]), COLUMN), false, "indented, and no nearer the middle");
  assert.equal(isCentred(block([[234, 300]]), COLUMN), false, "at the right edge");
});

test("a line that fills its column has nothing to centre, and one a little off still counts", () => {
  assert.equal(isCentred(block([[58, 296]]), COLUMN), false, "under an em clear of the edges");
  assert.equal(isCentred(block([[129, 227]]), COLUMN), true, "two points off, in 246");
  assert.equal(isCentred(block([[120, 228]]), COLUMN), false, "three points off");
});

test("lines centred one under another are; a justified block set in from both edges is not", () => {
  assert.equal(isCentred(block([[80, 274], [120, 234]]), COLUMN), true);
  assert.equal(isCentred(block([[80, 274], [80, 274], [80, 274]]), COLUMN), false, "an inset abstract");
  assert.equal(isCentred(block([[80, 274], [80, 180]]), COLUMN), false, "its last line ends short");
});

test("a list item's long last line, inset as far on both sides, goes on from somewhere", () => {
  const line = [[98, 256]];
  assert.equal(isCentred(block(line, { text: "where in the last step we used it" }), COLUMN), false);
  assert.equal(isCentred(block(line, { text: "Where in the last step we used it" }), COLUMN), true);
  assert.equal(isCentred(block([[150, 204]], { text: "by Jane Doe" }), COLUMN), true, "a short one is a byline");
});

test("a one-line block grows to its column's edge", () => {
  assert.deepEqual(roomOf(block([[54, 120]]), COLUMN, [], false), { x1: 54, x2: 300 });
  assert.equal(roomOf(block([[54, 300]]), COLUMN, [], false), null, "it reaches it already");
});

test("a centred one grows both ways alike, as far as the nearer side lets it", () => {
  assert.deepEqual(roomOf(block([[130, 224]]), COLUMN, [], true), { x1: 54, x2: 300 });
  const beside = block([[260, 290]]);
  assert.deepEqual(roomOf(block([[130, 224]]), COLUMN, [beside], true), { x1: 104, x2: 250 });
});

test("it stops short of what stands beside it, and not of what stands above or below", () => {
  const p = block([[54, 120]]);
  assert.deepEqual(roomOf(p, COLUMN, [block([[200, 260]])], false), { x1: 54, x2: 190 }, "a cell beside it");
  const figure = { x1: 180, x2: 400, y1: 40, y2: 200 };
  assert.deepEqual(roomOf(p, COLUMN, [figure], false), { x1: 54, x2: 170 }, "a figure the height of the column");
  const above = block([[200, 260]], { y1: 80, y2: 102, lines: [{ x1: 200, x2: 260, y1: 80, y2: 102 }] });
  assert.deepEqual(roomOf(p, COLUMN, [above], false), { x1: 54, x2: 300 }, "its glyph box touches, no more");
  assert.equal(roomOf(p, COLUMN, [block([[125, 180]])], false), null, "no room before the next");
});

test("what stands beside it are the others' lines, not their boxes", () => {
  const p = block([[54, 120]]);
  const paragraph = block([[54, 300], [54, 300], [54, 300]], { y1: 88, lines: [
    { x1: 54, x2: 300, y1: 88, y2: 98 }, { x1: 200, x2: 300, y1: 100, y2: 110 }, { x1: 54, x2: 300, y1: 112, y2: 122 },
  ] });
  assert.deepEqual(roomOf(p, COLUMN, [paragraph], false), { x1: 54, x2: 190 }, "a line of it starts to its right");
  const clear = block([[54, 300], [54, 300]], { y1: 76, lines: [{ x1: 54, x2: 300, y1: 76, y2: 86 }, { x1: 54, x2: 300, y1: 88, y2: 98 }] });
  assert.deepEqual(roomOf(p, COLUMN, [clear], false), { x1: 54, x2: 300 }, "its box ends above it");
});

test("a fragment inside another block's line has no room, either way", () => {
  const fragment = block([[150, 180]]);
  const line = block([[54, 300]]);
  assert.equal(roomOf(fragment, COLUMN, [line], false), null);
  assert.equal(roomOf(fragment, COLUMN, [line], true), null);
});

test("a tall block's line beside a shorter one stands beside it, by the shorter one's height", () => {
  const tall = block([[54, 120]], { y2: 151, lines: [{ x1: 54, x2: 120, y1: 100, y2: 151 }] });
  const short = block([[200, 260]], { y1: 138, y2: 147, lines: [{ x1: 200, x2: 260, y1: 138, y2: 147 }] });
  assert.deepEqual(roomOf(tall, COLUMN, [short], false), { x1: 54, x2: 190 });
});

test("a block's text stops where the next one's starts inside its box, with a little of the empty line box", () => {
  const p = block([[54, 300], [54, 300]]); // last line 112 to 122, box's foot 123.5
  const seam = 0.15 * p.size;
  const next = (y) => block([[54, 300]], { y1: y, y2: y + 10, lines: [{ x1: 54, x2: 300, y1: y, y2: y + 10 }] });
  assert.equal(footOf(p, [next(120)]), 120 + seam);
  assert.equal(footOf(p, [next(121), next(120)]), 120 + seam, "the nearest");
  assert.equal(footOf(p, [next(124)]), null, "a gap: the box's foot stands");
  assert.equal(footOf(p, [next(116)]), null, "at the height of its last line it is beside it");
  assert.equal(footOf(p, [block([[310, 400]], { y1: 120, y2: 130, lines: [{ x1: 310, x2: 400, y1: 120, y2: 130 }] })]), null, "another column");
  assert.equal(footOf({ ...p, room: { x1: 54, x2: 420 } }, [block([[310, 400]], { y1: 120, y2: 130, lines: [{ x1: 310, x2: 400, y1: 120, y2: 130 }] })]), 120 + seam, "across the room its text has");
  assert.equal(footOf(p, [{ x1: 54, x2: 300, y1: 120, y2: 130 }]), null, "a figure has no first line");
  assert.equal(footOf({ ...p, lines: [] }, [next(120)]), null);
});

test("markShape writes the foot on a translated block, whatever stands under it", () => {
  const p = block([[54, 300], [54, 300]], { src: "Words" });
  const under = block([[54, 300]], { src: "Words", translate: false, y1: 120, y2: 130, lines: [{ x1: 54, x2: 300, y1: 120, y2: 130 }] });
  markShape([p, under]);
  assert.ok(Math.abs(p.foot - 121.5) < 1e-9, `${p.foot}`);
  assert.equal(under.foot, undefined, "it has no text of its own to stop");
});

// Two columns at 10 pt, baselines 12 pt apart, `heading` over the left one.
const run = (str, x, y, w = str.length * 5) => ({ str, x, y, w, h: 10, font: "f1", asc: 0.7, desc: -0.2 });
const paragraph = (x, y, lines) => Array.from({ length: lines }, (_, i) => run("Words of a running paragraph that fill its whole measure.", x, y + i * 12, 240));
const twoColumns = (heading) => [...heading, ...paragraph(54, 150, 12), ...paragraph(317, 100, 16)];

test("on a two-column page a heading is centred in its column, and grows to that column's edges", () => {
  const paras = segmentPage(twoColumns([run("II. MODEL", 149, 120, 50)]));
  markShape(paras);
  const heading = paras.find((p) => p.text === "II. MODEL");
  assert.equal(heading.centred, true);
  assert.equal(heading.room.x1, 54);
  assert.equal(heading.room.x2, 294, "the left column's right edge");
  assert.equal(paras.filter((p) => p.centred).length, 1, "the paragraphs are not");
});

test("on a two-column page a left-aligned heading grows to the right, up to its gutter", () => {
  const paras = segmentPage(twoColumns([run("A. Model", 54, 120, 50)]));
  markShape(paras);
  const heading = paras.find((p) => p.text === "A. Model");
  assert.equal(heading.centred, undefined);
  assert.equal(heading.room.x1, 54);
  assert.ok(heading.room.x2 < 300, `the column's edge, not the other column: ${heading.room.x2}`);
});

test("a block over a figure stays in its box, and the figures hold the others back", () => {
  const paras = segmentPage(twoColumns([run("A. Model", 54, 120, 50)]));
  const heading = paras.find((p) => p.text === "A. Model");
  heading.overFigure = true;
  markShape(paras);
  assert.equal(heading.room, undefined);
  delete heading.overFigure;
  markShape(paras, [{ x1: 200, x2: 500, y1: 100, y2: 140 }]);
  assert.ok(Math.abs(heading.room.x2 - 190) < 1, `short of the figure: ${heading.room.x2}`);
});

test("a block that is no text, and a paragraph of lines, take no room", () => {
  const paras = segmentPage(twoColumns([run("A. Model", 54, 120, 50)]));
  const body = paras.find((p) => p.nLines > 3);
  const stray = paras.find((p) => p.text === "A. Model");
  stray.translate = false;
  markShape(paras);
  assert.equal(body.room, undefined);
  assert.equal(stray.room, undefined);
});

test("a one-line caption, which is out of the flow, takes the column it stands in", () => {
  const caption = run("FIG. 1. Short.", 54, 330, 70);
  const paras = segmentPage([...paragraph(54, 100, 12), ...paragraph(317, 100, 12), caption]);
  markShape(paras);
  const box = paras.find((p) => p.text.startsWith("FIG. 1"));
  assert.ok(box.room && box.room.x2 < 300 && box.room.x2 > 280, JSON.stringify(box.room));
});
