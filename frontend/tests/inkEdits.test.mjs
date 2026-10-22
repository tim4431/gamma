// node --test tests/inkEdits.test.mjs (from frontend/) — the arithmetic of
// a page's ink edits (ink/inkEdits.js; useInkDocument.js applyInk and the
// erasers, useInkStrokes.js): the byte budget, the group blocks an edit
// puts back, a fresh group's file, when strokes start a new group, a paste
// too big for any group, and the changes the erasers make through the
// eraser filter.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  GROUP_SPLIT_BYTES, MAX_BYTES, MAX_STROKES, encodeStroke, eraserKeep, inkBytes, newCanvasInk, newInk, pdfPositionOf, strokeBytes,
} from "../src/ink/ink.js";
import {
  eraseAlongChanges, eraseManyChanges, freshInk, missingGroups, overBudget, pasteTooBig, splitsGroup, withGroupBlocks,
} from "../src/ink/inkEdits.js";

const stroke = (id, x = 10, extra = {}) => encodeStroke({ id, ch: "xy", pen: false, size: 1, samples: [{ x, y: -40 }, { x, y: 40 }], ...extra });
const page = (...strokes) => ({ ...newInk(1, 612, 792), strokes });
// a stroke whose JSON is about `n` bytes long, and a group of `n` tiny strokes
const padded = (id, n) => ({ id, ch: "xy", pts: [0, 0], pad: "x".repeat(n) });
const many = (n) => page(...Array.from({ length: n }, (_, i) => ({ id: `s${i}`, ch: "xy", pts: [0, 0] })));
const ids = (ink) => ink.strokes.map((s) => s.id);

test("the byte budget refuses an edit that takes a group past MAX_BYTES, but lets one over it shrink", () => {
  const small = page(stroke("a")), huge = page(padded("big", MAX_BYTES)), huger = page(padded("big", MAX_BYTES + 100));
  assert.ok(inkBytes(huge) > MAX_BYTES);
  assert.equal(overBudget([{ before: small, after: page(stroke("a"), stroke("b", 20)) }]), false);
  assert.equal(overBudget([{ before: small, after: huge }]), true, "growth past the cap");
  assert.equal(overBudget([{ before: huger, after: huge }]), false, "still over, but smaller");
  assert.equal(overBudget([{ before: small, after: small }, { before: small, after: huge }]), true, "any change of the edit");
  // a group that ends exactly at the cap is the server's to take; one byte more is not
  const sized = (bytes) => { const base = strokeBytes(padded("p", 0)); return padded("p", bytes - base); };
  const exact = page(sized(MAX_BYTES - inkBytes(page()) + 1));
  assert.equal(inkBytes(exact), MAX_BYTES);
  assert.equal(overBudget([{ before: small, after: exact }]), false, "exactly MAX_BYTES");
  assert.equal(overBudget([{ before: small, after: page(sized(MAX_BYTES - inkBytes(page()) + 2)) }]), true, "one byte past it");
});

test("a change that leaves strokes in a group with no block in the tree puts the block back", () => {
  const present = new Set(["g1"]);
  const changes = [
    { id: "g1", page: 1, before: page(), after: page(stroke("a")) },
    { id: "g2", page: 1, before: page(stroke("b")), after: page(stroke("b"), stroke("c", 30)) },
    { id: "g3", page: 1, before: page(stroke("d")), after: page() },
  ];
  assert.deepEqual(missingGroups(changes, present).map((c) => c.id), ["g2"], "g1 is there; g3 is left empty, so it stays gone");
});

test("a block put back is fresh, with no file to base the save on: a sheet's under its sheet, a PDF page's at the top level with its place", () => {
  const sheet = { id: "s1", content: "", properties: { sheet: {} }, children: [{ id: "old", content: "", properties: { ink_url: "/u/o.ink" }, children: [] }] };
  const tree = [{ id: "intro", content: "about", properties: {}, children: [] }, sheet];
  const onSheet = { id: "gS", page: "s1", before: newCanvasInk(595, 842), after: { ...newCanvasInk(595, 842), strokes: [stroke("a")] } };
  const onPdf = { id: "gP", page: 3, before: newInk(3, 612, 792), after: { ...newInk(3, 612, 792), strokes: [stroke("b", 50)] } };
  const out = withGroupBlocks(tree, [onSheet, onPdf]);
  assert.equal(out[0], tree[0], "the rest of the tree stays as it was");
  assert.deepEqual(out[1].children.map((b) => b.id), ["old", "gS"], "the sheet's group goes last under its sheet");
  assert.deepEqual(out[1].children[1], { id: "gS", parentId: "s1", children: [], content: "", properties: { ink_url: "", ink_strokes: 0 } });
  assert.deepEqual(out[2], { id: "gP", parentId: null, children: [], content: "",
    properties: { ink_url: "", ink_strokes: 0, pdf_position: pdfPositionOf(onPdf.after) } });
  assert.ok(out[2].properties.pdf_position, "its place on the page");
  const gone = withGroupBlocks(tree, [{ ...onSheet, page: "s9" }]);
  assert.deepEqual(gone.at(-1), { id: "gS", parentId: "s9", children: [], content: "", properties: { ink_url: "", ink_strokes: 0 } },
    "a group whose sheet is gone goes at the top level");
  assert.equal(withGroupBlocks(tree, []), tree);
});

test("a fresh group's file is a sheet's canvas or a PDF page's", () => {
  assert.deepEqual(freshInk("s1", { width: 595, height: 842 }), newCanvasInk(595, 842));
  assert.deepEqual(freshInk(4, { width: 612, height: 792 }), newInk(4, 612, 792));
});

test("strokes start the next group past MAX_STROKES, a stroke's one and a paste's all", () => {
  assert.equal(splitsGroup(many(MAX_STROKES - 1), 10, 1), false, "the last stroke a group holds");
  assert.equal(splitsGroup(many(MAX_STROKES), 10, 1), true);
  assert.equal(splitsGroup(many(MAX_STROKES - 3), 30, 3), false, "a paste that just fits");
  assert.equal(splitsGroup(many(MAX_STROKES - 3), 40, 4), true, "one stroke more");
});

test("strokes start the next group past GROUP_SPLIT_BYTES; an empty group takes anything", () => {
  const before = page(stroke("a")), room = GROUP_SPLIT_BYTES - inkBytes(before);
  assert.equal(splitsGroup(before, room, 1), false, "up to the split exactly");
  assert.equal(splitsGroup(before, room + 1, 1), true);
  assert.equal(splitsGroup(before, room + 1, 5), true, "a paste the same");
  assert.equal(splitsGroup(page(), GROUP_SPLIT_BYTES * 2, MAX_STROKES + 1), false, "a group with no strokes is never left behind");
});

test("Too much handwriting to paste: more strokes than a group holds, or more bytes than a fresh group on the surface takes", () => {
  const size = { width: 612, height: 792 };
  assert.equal(pasteTooBig(1, Array.from({ length: MAX_STROKES + 1 }, (_, i) => ({ id: `p${i}`, ch: "xy", pts: [0, 0] })), size), true);
  assert.equal(pasteTooBig(1, Array.from({ length: MAX_STROKES }, (_, i) => ({ id: `p${i}`, ch: "xy", pts: [0, 0] })), size), false);
  // one stroke of exactly the room a fresh group has, on a sheet and on a PDF page
  const sized = (bytes) => { const base = strokeBytes(padded("p", 0)); return padded("p", bytes - base); };
  const sheetRoom = MAX_BYTES - inkBytes(newCanvasInk(size.width, size.height));
  const pdfRoom = MAX_BYTES - inkBytes(newInk(1, size.width, size.height));
  assert.ok(pdfRoom < sheetRoom, "a PDF page's file names its page: its empty file is longer");
  assert.equal(strokeBytes(sized(sheetRoom)), sheetRoom);
  assert.equal(pasteTooBig("s1", [sized(sheetRoom)], size), false);
  assert.equal(pasteTooBig("s1", [sized(sheetRoom + 1)], size), true);
  assert.equal(pasteTooBig(1, [sized(pdfRoom)], size), false);
  assert.equal(pasteTooBig(1, [sized(sheetRoom)], size), true, "what fits a sheet is too big for a PDF page");
});

test("a scribble's targets go through the eraser filter: the changes across their groups and the strokes counted", () => {
  const groups = {
    g1: page(stroke("a"), stroke("h", 30, { tool: "highlighter" }), stroke("k", 60)),
    g2: page(stroke("x", 90)),
    g3: page(stroke("hh", 120, { tool: "highlighter" })),
  };
  const inkOf = (id) => groups[id] || null;
  const targets = [{ gid: "g1", ids: ["a", "h"] }, { gid: "g2", ids: ["x"] }, { gid: "g3", ids: ["hh"] }, { gid: "gone", ids: ["y"] }];
  const { changes, n } = eraseManyChanges(2, targets, inkOf, eraserKeep("pen"));
  assert.equal(n, 2, "a and x: the highlighter strokes are kept by the filter");
  assert.deepEqual(changes.map((c) => [c.id, c.page, ids(c.before), ids(c.after)]),
    [["g1", 2, ["a", "h", "k"], ["h", "k"]], ["g2", 2, ["x"], []]], "g3 has nothing the filter lets go; a group gone is skipped");
  assert.equal(changes[0].before, groups.g1);
  assert.equal(eraseManyChanges(2, null, inkOf, eraserKeep("all")).n, 0);
  assert.equal(eraseManyChanges(2, targets, inkOf, eraserKeep("all")).n, 4);
});

test("the eraser's sweep: each group of the surface cut along the path or losing the strokes it touched, through the filter", () => {
  const groups = {
    g1: page(stroke("a", 50), stroke("h", 100, { tool: "highlighter" })),
    g2: page(stroke("far", 400)),
    g3: page(),
  };
  const inkOf = (id) => groups[id] || null;
  const blocks = [{ id: "g1" }, { id: "g2" }, { id: "g3" }, { id: "gone" }];
  const path = [{ x: 0, y: 0 }, { x: 150, y: 0 }];
  const whole = eraseAlongChanges(1, blocks, path, 3, false, inkOf, eraserKeep("pen"));
  assert.deepEqual(whole.map((c) => [c.id, c.page, ids(c.after)]), [["g1", 1, ["h"]]], "the pen stroke goes whole; the highlighter stays");
  assert.equal(whole[0].before, groups.g1);
  const partial = eraseAlongChanges(1, blocks, path, 3, true, inkOf, eraserKeep("all"));
  assert.deepEqual(partial.map((c) => c.id), ["g1"]);
  assert.equal(partial[0].after.strokes.length, 4, "each stroke cut in two");
  assert.ok(partial[0].after.strokes.every((s) => s.from === "a" || s.from === "h"));
  const pens = eraseAlongChanges(1, blocks, path, 3, true, inkOf, eraserKeep("pen"));
  assert.deepEqual(pens[0].after.strokes.map((s) => s.from || s.id), ["a", "a", "h"], "the partial eraser cuts the pen stroke; the filter keeps the highlighter whole");
  assert.equal(pens[0].after.strokes[2], groups.g1.strokes[1]);
  assert.deepEqual(eraseAlongChanges(1, blocks, [{ x: 0, y: 300 }, { x: 600, y: 300 }], 3, false, inkOf, eraserKeep("all")), [],
    "a sweep that touches nothing changes nothing");
});
