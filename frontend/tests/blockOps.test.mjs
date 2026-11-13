// node --test tests/  (from frontend/) — the pure op module has no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { generateKeyBetween, generateNKeysBetween } from "fractional-indexing";
import { PLACED, applyOps, diffTrees, displacedRow, propsPatch, pushOp, seedPositions, withMerges } from "../src/shared/model/blockOps.js";
import { POSITION_KEY, TEXT_QUOTE_MAX, acceptedPosition, anchorOf, blockPage, blockPosition, blocksToHighlights, closestPlacement,
  freshCopy, isHighlightBlock, isPlaceBlock, isRegionBlock, isTextRangeBlock, linesLabel, pdfAnchorOf, placeKeyOf, positionKeyOf,
  rangePending, roughMatchPercent, textSpan } from "../src/shared/model/blockModel.js";
import { mergePicture } from "../src/markup/picture.js";
import { mergeTextBox } from "../src/markup/textBox.js";

const PAGE = "page1";
const N = (id, content = "", children = [], properties = {}, extra = {}) =>
  ({ id, content, properties, children, ...extra });

// A tree as the server sends it: every node carries a position.
function served(tree, pos = new Map()) {
  const keys = ["a0", "a1", "a2", "a3", "a4", "a5", "a6"];
  const walk = (list) => list.map((n, i) => ({ ...n, position: keys[i], children: walk(n.children || []) }));
  const out = walk(tree);
  seedPositions(out, pos);
  return { tree: out, pos };
}

// Apply what diffTrees says to the base and compare with the intended
// next tree (ids, order, content, props), ignoring positions/UI flags.
const shape = (list) => (list || []).map((n) => ({
  id: n.id, content: n.content || "", properties: n.properties || {}, children: shape(n.children),
}));
function roundTrip(baseTree, nextTree) {
  const { tree: base, pos } = served(baseTree);
  const ops = diffTrees(base, nextTree, PAGE, pos);
  const got = applyOps(base, ops, PAGE, new Map(pos));
  assert.deepEqual(shape(got), shape(nextTree));
  return ops;
}

test("no change → no ops", () => {
  const { tree, pos } = served([N("a", "x"), N("b", "y", [N("c", "z")])]);
  assert.deepEqual(diffTrees(tree, tree, PAGE, pos), []);
});

test("content and property patches", () => {
  const ops = roundTrip(
    [N("a", "x", [], { color: "red", quote: "q" })],
    [N("a", "x2", [], { color: "red", n: 1 })],
  );
  // `base`: the text the change was made from (the server's three-way merge).
  assert.deepEqual(ops, [{ op: "set", id: "a", content: "x2", base: "x", props: { n: 1, quote: null } }]);
  assert.deepEqual(propsPatch({ a: 1, b: { x: 1 } }, { a: 1, b: { x: 1 } }), {});
});

test("a value whose keys come in another order is no change: no set, nor its base_props", () => {
  const box = { x: 1, y: 2, w: 30 }, turned = { w: 30, y: 2, x: 1 };
  assert.deepEqual(propsPatch({ text_box: box, quote: "q" }, { quote: "q", text_box: turned }), {});
  const { tree, pos } = served([N("b", "", [], { text_box: box })]);
  assert.deepEqual(diffTrees(tree, [{ ...tree[0], properties: { text_box: turned } }], PAGE, pos), []);
  assert.deepEqual(propsPatch({ a: [1, 2] }, { a: [2, 1] }), { a: [2, 1] }, "an array's order is its value");
});

test("a node's extra fields (a flattening's depth and parent) never travel", () => {
  const { tree, pos } = served([N("a", "x")]);
  const next = [{ ...tree[0], depth: 3, parentId: null }];
  assert.deepEqual(diffTrees(tree, next, PAGE, pos), []);
});

test("insert at the end, in the middle, nested", () => {
  const ops = roundTrip(
    [N("a", "a"), N("c", "c")],
    [N("a", "a"), N("b", "b"), N("c", "c"), N("d", "d", [N("e", "e")])],
  );
  assert.deepEqual(ops.map((o) => [o.op, o.id, o.parent]),
    [["insert", "b", PAGE], ["insert", "d", PAGE], ["insert", "e", "d"]]);
  // b's key sorts between a and c
  assert.ok(ops[0].position > "a0" && ops[0].position < "a1", ops[0].position);
});

test("delete emits only the top-most removed subtrees", () => {
  const ops = roundTrip(
    [N("a", "a", [N("b", "b", [N("c", "c")])]), N("d", "d")],
    [N("d", "d")],
  );
  assert.deepEqual(ops, [{ op: "delete", id: "a" }]);
});

test("a block escaping a deleted parent moves out before the delete", () => {
  const ops = roundTrip(
    [N("a", "a", [N("b", "b")])],
    [N("b", "b")],
  );
  assert.deepEqual(ops.map((o) => o.op), ["move", "delete"]);
  assert.equal(ops[0].id, "b");
});

test("reorder: one moved block, the rest keep their keys", () => {
  // [a b c d] → [d a b c]: only d is re-keyed
  const ops = roundTrip(
    [N("a"), N("b"), N("c"), N("d")],
    [N("d"), N("a"), N("b"), N("c")],
  );
  assert.deepEqual(ops.map((o) => [o.op, o.id]), [["move", "d"]]);
  assert.ok(ops[0].position < "a0");
  // [a b c d] → [b c d a]: only a moves
  const ops2 = roundTrip([N("a"), N("b"), N("c"), N("d")], [N("b"), N("c"), N("d"), N("a")]);
  assert.deepEqual(ops2.map((o) => o.id), ["a"]);
  assert.ok(ops2[0].position > "a3");
});

test("indent / outdent are moves", () => {
  const ops = roundTrip([N("a"), N("b")], [N("a", "", [N("b")])]);
  assert.deepEqual(ops, [{ op: "move", id: "b", parent: "a", position: ops[0].position }]);
  const ops2 = roundTrip([N("a", "", [N("b")])], [N("a"), N("b")]);
  assert.deepEqual(ops2.map((o) => [o.op, o.id, o.parent]), [["move", "b", PAGE]]);
});

test("swapping parent and child orders the moves top-down", () => {
  const ops = roundTrip([N("x", "", [N("y")])], [N("y", "", [N("x")])]);
  assert.deepEqual(ops.map((o) => [o.op, o.id, o.parent]), [["move", "y", PAGE], ["move", "x", "y"]]);
});

test("move plus edit in one transition", () => {
  const ops = roundTrip([N("a", "a"), N("b", "b")], [N("b", "b!"), N("a", "a")]);
  assert.deepEqual(ops.map((o) => [o.op, o.id]), [["move", "b"], ["set", "b"]]);
});

test("applyOps: remote insert lands sorted, unknown ids are no-ops, re-insert re-parents", () => {
  const { tree, pos } = served([N("a", "a"), N("c", "c")]);
  let t = applyOps(tree, [{ op: "insert", id: "b", parent: PAGE, position: "a0V", content: "b", props: { k: 1 } }], PAGE, pos);
  assert.deepEqual(t.map((n) => n.id), ["a", "b", "c"]);
  assert.deepEqual(t[1], { id: "b", content: "b", properties: { k: 1 }, position: "a0V", children: [] });
  t = applyOps(t, [{ op: "move", id: "zzz", parent: PAGE, position: "a9" }, { op: "delete", id: "nope" },
                   { op: "set", id: "ghost", content: "x" }], PAGE, pos);
  assert.deepEqual(t.map((n) => n.id), ["a", "b", "c"]);
  t = applyOps(t, [{ op: "insert", id: "b", parent: "a", position: "a0", content: "b2", props: {} }], PAGE, pos);
  assert.deepEqual(t.map((n) => n.id), ["a", "c"]);
  assert.equal(t[0].children[0].content, "b2");
  // a set on the page id is left to the caller
  assert.equal(applyOps(t, [{ op: "set", id: PAGE, content: "title" }], PAGE, pos), t);
});

test("applyOps: set patches properties (a stored fold included — the viewer's own folding is beside the tree)", () => {
  const { tree, pos } = served([N("a", "a", [N("b")], { collapsed: false })]);
  const t = applyOps(tree, [{ op: "set", id: "a", props: { collapsed: true, color: "c" } }], PAGE, pos);
  assert.deepEqual(t[0].properties, { collapsed: true, color: "c" });
});

test("applyOps: untouched subtrees keep their identity", () => {
  const { tree, pos } = served([N("a", "a", [N("b")]), N("c", "c")]);
  const t = applyOps(tree, [{ op: "set", id: "c", content: "c2" }], PAGE, pos);
  assert.equal(t[0], tree[0]);
  assert.notEqual(t[1], tree[1]);
});

test("pushOp coalesces consecutive sets of one block", () => {
  const q = [];
  pushOp(q, { op: "set", id: "a", props: { z: 0 } });
  pushOp(q, { op: "set", id: "a", content: "1", base: "" });
  pushOp(q, { op: "set", id: "a", content: "12", base: "1", props: { x: 1 } });
  pushOp(q, { op: "set", id: "b", content: "b", base: "" });
  pushOp(q, { op: "set", id: "a", props: { y: 2 } });
  // The run of keystrokes keeps the base it started from (a props-only op
  // adopts the first content's base), and the latest content.
  assert.deepEqual(q, [
    { op: "set", id: "a", content: "12", base: "", props: { z: 0, x: 1, y: 2 } },
    { op: "set", id: "b", content: "b", base: "" },
  ]);
  pushOp(q, { op: "move", id: "a", parent: PAGE, position: "a5" });
  pushOp(q, { op: "set", id: "a", content: "after move" });
  assert.equal(q.length, 4);
});

test("pushOp folds nothing into the first `floor` ops (a batch sent under its name)", () => {
  const q = [{ op: "set", id: "a", content: "1", base: "" }];
  assert.equal(pushOp(q, { op: "set", id: "a", content: "12", base: "1" }, 1), null);
  assert.ok(pushOp(q, { op: "set", id: "a", content: "123", base: "1" }, 1), "a set after them folds as before");
  assert.deepEqual(q, [{ op: "set", id: "a", content: "1", base: "" }, { op: "set", id: "a", content: "123", base: "1" }]);
});

test("a changed text box names the box it was changed from, which a run of changes keeps", () => {
  const box = { x: 10, y: 10, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  const ops = roundTrip(
    [N("t", "Hello", [], { text_box: box, pdf_page: 1 }), N("n", "note")],
    [N("t", "Hello world", [], { text_box: { ...box, w: 101.5 }, pdf_page: 1 }), N("n", "note", [], { text_box: box })],
  );
  // (gamma/text_box.py merge_text_box: the server takes only the keys changed from it)
  assert.deepEqual(ops, [
    { op: "set", id: "t", content: "Hello world", base: "Hello", props: { text_box: { ...box, w: 101.5 } },
      base_props: { text_box: box } },
    { op: "set", id: "n", props: { text_box: box } }, // a new box: nothing to merge into
  ]);
  const q = [];
  pushOp(q, { op: "set", id: "t", content: "a", base: "", props: { text_box: { ...box, w: 30 } }, base_props: { text_box: box } });
  pushOp(q, { op: "set", id: "t", props: { text_box: { ...box, w: 30, x: 50 } }, base_props: { text_box: { ...box, w: 30 } } });
  pushOp(q, { op: "set", id: "t", props: { pdf_page: 2 } });
  assert.deepEqual(q, [{ op: "set", id: "t", content: "a", base: "",
    props: { text_box: { ...box, w: 30, x: 50 }, pdf_page: 2 }, base_props: { text_box: box } }], "the first base stays");
  const r = [];
  pushOp(r, { op: "set", id: "t", props: { pdf_page: 2 } });
  pushOp(r, { op: "set", id: "t", props: { text_box: { ...box, x: 50 } }, base_props: { text_box: box } });
  assert.deepEqual(r[0].base_props, { text_box: box }, "the box's first change brings its base");
  const w = [];
  pushOp(w, { op: "set", id: "t", props: { text_box: box } });
  pushOp(w, { op: "set", id: "t", props: { text_box: { ...box, x: 50 } }, base_props: { text_box: box } });
  assert.equal(w[0].base_props, undefined, "a run that began with a whole box stays one");
});

const PIC = { url: "/api/uploads/3f2a9c.png", x: 10, y: 10, w: 100, h: 80, angle: 0,
  crop: { x: 0, y: 0, w: 1, h: 1 }, flip_h: false, flip_v: false };

test("a changed picture names the picture it was changed from, as a text box does", () => {
  const box = { x: 10, y: 10, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  const crop = { ...PIC, x: 30, y: 25, w: 60, h: 40, crop: { x: 0.2, y: 0.25, w: 0.6, h: 0.5 } };
  const ops = roundTrip(
    [N("i", "A figure", [], { picture: PIC, pdf_page: 1 }), N("t", "Hi", [], { text_box: box, pdf_page: 1 }), N("n", "note")],
    [N("i", "A figure", [], { picture: crop, pdf_page: 1 }), N("t", "Hi", [], { text_box: { ...box, x: 40 }, pdf_page: 1 }),
      N("n", "note", [], { picture: PIC })],
  );
  // (gamma/ops.py PLACED_KEYS: the server takes only the keys changed from it)
  assert.deepEqual(ops, [
    { op: "set", id: "i", props: { picture: crop }, base_props: { picture: PIC } },
    { op: "set", id: "t", props: { text_box: { ...box, x: 40 } }, base_props: { text_box: box } },
    { op: "set", id: "n", props: { picture: PIC } }, // a new picture: nothing to merge into
  ]);
  // one block changing both names both
  const both = roundTrip([N("x", "", [], { picture: PIC, text_box: box })],
    [N("x", "", [], { picture: { ...PIC, angle: 90 }, text_box: { ...box, x: 1 } })]);
  assert.deepEqual(both[0].base_props, { text_box: box, picture: PIC });
  // a removed picture names none: the server takes the removal as sent
  assert.deepEqual(roundTrip([N("i", "", [], { picture: PIC })], [N("i", "", [], {})]),
    [{ op: "set", id: "i", props: { picture: null } }]);
  const q = [];
  pushOp(q, { op: "set", id: "i", props: { picture: { ...PIC, x: 50 } }, base_props: { picture: PIC } });
  pushOp(q, { op: "set", id: "i", props: { picture: { ...PIC, x: 50, angle: 15 } }, base_props: { picture: { ...PIC, x: 50 } } });
  assert.deepEqual(q, [{ op: "set", id: "i", props: { picture: { ...PIC, x: 50, angle: 15 } }, base_props: { picture: PIC } }],
    "a run keeps the picture it began from");
  assert.deepEqual(Object.keys(PLACED), ["text_box", "picture"]);
  assert.equal(PLACED.text_box.merge, mergeTextBox);
  assert.equal(PLACED.picture.merge, mergePicture);
});

test("a picture is placed by its pdf_page and is no highlight, whatever position it carries", () => {
  const pos = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  const pic = N("i", "", [], { picture: PIC, pdf_page: 5, pdf_position: pos });
  assert.equal(isHighlightBlock(pic), false);
  assert.equal(blockPage(pic), 5);
  assert.equal(blockPage(N("i", "", [], { picture: { url: "/api/uploads/gone.bmp" }, pdf_page: 3 })), 3, "a missing file still places it");
  assert.equal(blockPage(N("i", "", [], { picture: PIC })), null, "on no PDF page");
  // the precedence: ink, then a text box, then a picture
  assert.equal(blockPage(N("g", "", [], { ink_url: "/api/uploads/a.ink", picture: PIC, pdf_page: 5, pdf_position: pos })), 2);
  // a value that is no object is no picture
  assert.equal(isHighlightBlock(N("h", "", [], { picture: [], pdf_position: pos })), true);
  assert.equal(blockPage(N("h", "", [], { picture: "x", pdf_page: 5, pdf_position: pos })), 2);
});

test("a position, a text box and a sheet are objects, never arrays, as the server's kind rule has them", () => {
  const pos = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  assert.equal(isHighlightBlock(N("h", "", [], { pdf_position: [] })), false, "an array is no position");
  assert.equal(isHighlightBlock(N("h", "", [], { text_box: [], pdf_position: pos })), true, "nor a text box");
  assert.equal(isHighlightBlock(N("h", "", [], { sheet: [], pdf_position: pos })), true, "nor a sheet");
  assert.equal(isHighlightBlock(N("h", "", [], { text_box: { x: 1 }, pdf_position: pos })), false);
  assert.equal(isHighlightBlock(N("h", "", [], { sheet: {}, pdf_position: pos })), false);
  assert.equal(blockPage(N("h", "", [], { text_box: [], pdf_page: 5, pdf_position: pos })), 2, "placed by its position");
  assert.equal(blockPage(N("h", "", [], { text_box: { x: 1 }, pdf_page: 5, pdf_position: pos })), 5);
});

test("a region of a picture: a block placed by image_position that no other kind claims, listed under the image key", () => {
  const region = { width: 1200, height: 800, boundingRect: { x1: 10, y1: 20, x2: 300, y2: 200 }, rects: [{ x1: 10, y1: 20, x2: 300, y2: 200 }], hash: "i1" };
  const pos = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  const r = N("r", "a thought", [], { image_position: region, color: "#ffe066aa" });
  assert.equal(isRegionBlock(r), true);
  assert.equal(isHighlightBlock(r), false, "no place on a PDF page");
  assert.equal(isPlaceBlock(r), true);
  assert.equal(isPlaceBlock(N("h", "", [], { pdf_position: pos })), true);
  assert.equal(isPlaceBlock(N("n", "", [], {})), false);
  for (const other of [{ ink_url: "" }, { text_box: { x: 1 } }, { picture: { url: "/api/uploads/p.png" } }, { sheet: {} }]) {
    assert.equal(isRegionBlock(N("x", "", [], { ...other, image_position: region })), false, JSON.stringify(other));
  }
  assert.equal(isRegionBlock(N("x", "", [], { image_position: [1] })), false, "an array is no region");
  assert.equal(isRegionBlock(N("x", "", [], { image_position: "r" })), false);
  // the position by the page's document's key
  assert.deepEqual(POSITION_KEY, { pdf: "pdf_position", image: "image_position", text: "text_position" });
  assert.equal(positionKeyOf({ format: "image" }), "image_position");
  assert.equal(positionKeyOf({ format: "pdf" }), "pdf_position");
  assert.equal(positionKeyOf({ format: "text" }), "text_position");
  assert.equal(positionKeyOf({ format: "audio" }), "pdf_position", "the PDF's for any other document");
  assert.equal(positionKeyOf(null), "pdf_position");
  assert.equal(blockPosition(r), null, "no PDF place");
  assert.equal(blockPosition(r, "image_position"), region);
  // the block's own key: a region's image_position, any other block's pdf_position
  assert.equal(placeKeyOf(r), "image_position");
  assert.equal(blockPosition(r, placeKeyOf(r)), region);
  assert.equal(placeKeyOf(N("h", "", [], { pdf_position: pos })), "pdf_position");
  assert.equal(placeKeyOf(N("n", "", [], {})), "pdf_position");
  assert.equal(blockPosition(N("x", "", [], { image_position: { width: 1, height: 1, hash: "i1" } }), "image_position"), null, "no rectangles");
  // the viewer's list under the image key: regions alone, as areas, with whether they are on the shown version
  const tree = [r, N("h", "", [], { pdf_position: pos, quote: "q" }), N("old", "", [], { image_position: { ...region, hash: "i0" } })];
  const regions = blocksToHighlights(tree, "image_position", "i1");
  assert.deepEqual(regions.map((h) => [h.id, h.position.area, h.position.onVersion, h.style]), [["r", true, true, "highlight"], ["old", true, false, "highlight"]]);
  assert.equal(regions[0].note, "a thought");
  assert.equal(regions[0].position.hash, "i1");
  assert.equal("area" in region, false, "the stored shape carries no area");
  assert.equal(blocksToHighlights(tree, "image_position")[0].position.onVersion, false, "no file to compare with");
  assert.deepEqual(blocksToHighlights(tree).map((h) => h.id), ["h"], "the PDF key leaves the regions out");
  // the anchor a comment card stands beside
  assert.deepEqual(anchorOf(r, "image_position"), { page: null, position: region });
  assert.equal(anchorOf(r), null);
  assert.equal(anchorOf(N("h", "", [], { pdf_position: pos }), "image_position"), null);
  assert.deepEqual(anchorOf(N("h", "", [], { pdf_position: pos })), pdfAnchorOf(N("h", "", [], { pdf_position: pos })));
  // Duplicate leaves the region with the original, as it does the PDF place
  const copy = freshCopy({ ...r, children: [] });
  assert.deepEqual(copy.properties, { color: "#ffe066aa" });
  assert.notEqual(copy.id, "r");
});

test("a range of a text file: a block placed by text_position that no other kind claims, listed under the text key", () => {
  const range = { line: 16, col: 0, end: 16, end_col: 33, prefix: "\\section{Intro}\n", suffix: ", and", hash: "f1" };
  const pos = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  const t = N("t", "", [], { text_position: range, quote: "Weight decay is the oldest remedy", color: "#ffe066aa", style: "underline" });
  assert.equal(isTextRangeBlock(t), true);
  assert.equal(isHighlightBlock(t), false, "no place on a PDF page");
  assert.equal(isRegionBlock(t), false);
  assert.equal(isPlaceBlock(t), true);
  for (const other of [{ ink_url: "" }, { text_box: { x: 1 } }, { picture: { url: "/api/uploads/p.png" } }, { sheet: {} }]) {
    assert.equal(isTextRangeBlock(N("x", "", [], { ...other, text_position: range })), false, JSON.stringify(other));
  }
  assert.equal(isTextRangeBlock(N("x", "", [], { text_position: [16] })), false, "an array is no range");
  assert.equal(isTextRangeBlock(N("x", "", [], { text_position: "1-3" })), false);
  // the span, by the server's text_span rule
  assert.deepEqual(textSpan(range), { line: 16, col: 0, end: 16, end_col: 33 });
  assert.deepEqual(textSpan({ line: 3, col: 9, end: 5, end_col: 0 }), { line: 3, col: 9, end: 5, end_col: 0 }, "a later line's column 0");
  for (const bad of [{ line: 0, col: 0, end: 1, end_col: 2 }, { line: 2, col: 0, end: 1, end_col: 2 }, { line: 1, col: 4, end: 1, end_col: 4 },
    { line: 1, col: -1, end: 1, end_col: 2 }, { line: 1.5, col: 0, end: 2, end_col: 2 }, { line: "1", col: 0, end: 1, end_col: 2 }, { line: 1 }, null, []]) {
    assert.equal(textSpan(bad), null, JSON.stringify(bad));
  }
  // the position by the key, the block's own key
  assert.equal(blockPosition(t), null, "no PDF place");
  assert.equal(blockPosition(t, "text_position"), range);
  assert.equal(blockPosition(N("x", "", [], { text_position: { ...range, end_col: 0 } }), "text_position"), null, "no span");
  assert.equal(placeKeyOf(t), "text_position");
  assert.equal(placeKeyOf(N("h", "", [], { pdf_position: pos, text_position: range })), "pdf_position", "a place on the PDF first");
  assert.equal(blockPage(t), null);
  // the viewer's list under the text key: ranges alone, each with its own position
  const tree = [t, N("h", "", [], { pdf_position: pos, quote: "q" }), N("old", "", [], { text_position: { ...range, hash: "f0" }, quote: "x" }),
    N("bad", "", [], { text_position: { line: 0 }, quote: "y" })];
  const ranges = blocksToHighlights(tree, "text_position", "f1");
  assert.deepEqual(ranges.map((h) => [h.id, h.style]), [["t", "underline"], ["old", "highlight"]], "a malformed one is drawn nowhere");
  assert.equal(ranges[0].position, range, "the stored position, nothing added");
  assert.equal(ranges[0].quote, "Weight decay is the oldest remedy");
  // pending: measured on another version than the file shown, not lost; an empty hash reads as the file's own (the server's rule)
  assert.deepEqual(ranges.map((h) => rangePending(h.position, "f1")), [false, true]);
  assert.equal(rangePending({ ...range, hash: "f0", approx: true, match: 0.9 }, "f1"), true, "placed to check on an older version: still pending");
  assert.equal(rangePending({ ...range, hash: "f0", lost: true }, "f1"), false, "lost is lost");
  assert.equal(rangePending({ ...range, hash: "" }, "f1"), false);
  assert.equal(rangePending({ ...range, hash: "f0" }, ""), false, "no file to compare with");
  assert.deepEqual([linesLabel(3, 5), linesLabel(3, 3), linesLabel(3)], ["Lines 3–5", "Line 3", "Line 3"]);
  assert.deepEqual(blocksToHighlights(tree).map((h) => h.id), ["h"], "the PDF key leaves the ranges out");
  assert.deepEqual(blocksToHighlights(tree, "image_position"), []);
  // the anchor a comment card stands beside
  assert.deepEqual(anchorOf(t, "text_position"), { page: null, position: range });
  assert.equal(anchorOf(t), null);
  assert.equal(anchorOf(t, "image_position"), null);
  assert.equal(anchorOf(N("h", "", [], { pdf_position: pos }), "text_position"), null);
  // Duplicate leaves the range with the original
  const copy = freshCopy({ ...t, children: [] });
  assert.deepEqual(copy.properties, { quote: "Weight decay is the oldest remedy", color: "#ffe066aa", style: "underline" });
});

test("a lost range placed at its closest match is dashed there, to check, with where it was", () => {
  const closest = { line: 40, col: 2, end: 41, end_col: 7, match: 0.62, quote: "is to repeat\nthe study", hash: "f2" };
  const lost = { line: 55, col: 0, end: 55, end_col: 30, prefix: "p", suffix: "s", hash: "f1", lost: true, closest };
  assert.deepEqual(closestPlacement(lost, "is to see whether the same holds"), {
    position: { line: 40, col: 2, end: 41, end_col: 7, hash: "f2", prefix: "p", suffix: "s", approx: true, match: 0.62,
      prev: { hash: "f1", line: 55, col: 0, end: 55, end_col: 30, quote: "is to see whether the same holds" } },
    quote: "is to repeat\nthe study",
  });
  const placed = closestPlacement(lost, "x".repeat(1500));
  assert.equal(placed.position.prev.quote.length, 1500, "a range's prev keeps its whole quote, which the next run matches it by");
  assert.equal(closestPlacement(lost, "x".repeat(TEXT_QUOTE_MAX + 5)).position.prev.quote.length, TEXT_QUOTE_MAX,
    "clipped as the server's");
  assert.equal(closestPlacement({ ...lost, closest: { ...closest, quote: "y".repeat(3000) } }).quote.length, 3000,
    "the closest's quote whole: the range's quote from then on");
  assert.equal(roughMatchPercent(placed.position), 62, "the menu's percentage");
  assert.equal(acceptedPosition(placed.position).approx, undefined, "Accept draws it solid");
  assert.deepEqual(Object.keys(acceptedPosition(placed.position)).sort(), ["col", "end", "end_col", "hash", "line", "prefix", "suffix"]);
  assert.equal(closestPlacement({ ...lost, closest: undefined }), null, "nothing to apply without a closest match");
  assert.equal(closestPlacement({ ...lost, closest: { ...closest, hash: undefined } }), null, "nor one naming no version");
  assert.equal(closestPlacement({ ...lost, closest: { ...closest, match: 2 } }), null);
  assert.equal(closestPlacement({ ...lost, closest: { ...closest, end_col: 0, end: 40, col: 0 } }), null, "nor one with no span");
});

test("a comment thread stands on the PDF on a highlight, a link region or an ink group with a position", () => {
  const pos = { pageNumber: 2, width: 612, height: 792, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [] };
  assert.deepEqual(pdfAnchorOf(N("h", "", [], { pdf_position: pos, quote: "q" })), { page: 2, position: pos });
  assert.deepEqual(pdfAnchorOf(N("l", "", [], { pdf_position: pos, link_url: "https://example.org" })), { page: 2, position: pos });
  assert.deepEqual(pdfAnchorOf(N("g", "", [], { ink_url: "/api/uploads/a.ink", pdf_position: pos })), { page: 2, position: pos });
  assert.equal(pdfAnchorOf(N("g", "", [], { ink_url: "/api/uploads/a.ink" })), null, "a group on a sheet");
  assert.equal(pdfAnchorOf(N("b", "", [], { text_box: { x: 1 }, pdf_page: 2, pdf_position: pos })), null, "a text box");
  assert.equal(pdfAnchorOf(N("i", "", [], { picture: PIC, pdf_page: 5, pdf_position: pos })), null, "a picture");
  assert.equal(pdfAnchorOf(N("n", "a note")), null);
  assert.equal(pdfAnchorOf(N("h", "", [], { pdf_position: [] })), null, "an array is no position");
  assert.deepEqual(pdfAnchorOf(N("h", "", [], { pdf_position: { boundingRect: pos.boundingRect } })).page, null, "no page");
});

test("a diff of a large flat reorder stays minimal", () => {
  const ids = Array.from({ length: 40 }, (_, i) => `n${i}`);
  const base = ids.map((id) => N(id));
  const pos = new Map();
  const keys = generateNKeysBetween(null, null, ids.length);
  ids.forEach((id, i) => pos.set(id, keys[i]));
  const rotated = [...base.slice(3), ...base.slice(0, 3)];
  const ops = diffTrees(base, rotated, PAGE, pos);
  assert.equal(ops.length, 3);
  assert.deepEqual(ops.map((o) => o.id), ["n0", "n1", "n2"]);
  const got = applyOps(base, ops, PAGE, new Map(pos));
  assert.deepEqual(got.map((n) => n.id), rotated.map((n) => n.id));
});

test("withMerges folds a block joined to another into one op, marker and children with it", () => {
  const merged = (before, after) => {
    const { tree: base, pos } = served(before);
    return withMerges(diffTrees(base, after, PAGE, pos), base, PAGE);
  };
  assert.deepEqual(merged([N("a", "one"), N("b", "two")], [N("a", "onetwo")]),
    [{ op: "merge", id: "b", into: "a", base: "one" }]);
  assert.deepEqual(merged([N("a", "one"), N("b", "- two\n- more")], [N("a", "onetwo\n- more")]),
    [{ op: "merge", id: "b", into: "a", base: "one", marker: "- " }], "only the first line loses its marker");
  assert.deepEqual(merged([N("a", "one"), N("b", "two", [N("k", "kid")]), N("z", "z")], [N("a", "onetwo", [N("k", "kid")]), N("z", "z")]),
    [{ op: "merge", id: "b", into: "a", base: "one" }], "the children go with it");
  assert.deepEqual(merged([N("p", "one", [N("b", "two", [N("k", "kid")])])], [N("p", "onetwo", [N("k", "kid")])]),
    [{ op: "merge", id: "b", into: "p", base: "one" }], "into its parent");

  const props = merged([N("a", "one"), N("b", "two")], [N("a", "onetwo", [], { color: "red" })]);
  assert.deepEqual(props, [{ op: "set", id: "a", props: { color: "red" } }, { op: "merge", id: "b", into: "a", base: "one" }], "a property change stays a set");
});

test("withMerges leaves a delete and a set that are no join as they are", () => {
  const kinds = (before, after) => {
    const { tree: base, pos } = served(before);
    return withMerges(diffTrees(base, after, PAGE, pos), base, PAGE).map((op) => op.op);
  };
  assert.deepEqual(kinds([N("a", "one"), N("b", "two")], [N("a", "one!")]), ["set", "delete"], "other text");
  assert.deepEqual(kinds([N("a", "one"), N("b", "two")], [N("a", "twoone")]), ["set", "delete"], "at the start");
  assert.deepEqual(kinds([N("a", "one"), N("b", "")], [N("a", "one")]), ["delete"], "an empty block");
  assert.deepEqual(kinds([N("a", "one"), N("b", "two", [N("k", "kid")])], [N("a", "onetwo")]), ["set", "delete"], "its subtree went with it");
  // two blocks removed, one join: the second finds the set taken
  assert.deepEqual(kinds([N("a", "one"), N("b", "two"), N("c", "two")], [N("a", "onetwo")]), ["merge", "delete"]);
  const { tree: base, pos } = served([N("a", "one")]);
  const ops = diffTrees(base, [N("a", "two")], PAGE, pos);
  assert.equal(withMerges(ops, base, PAGE), ops, "nothing to fold");
});

test("applyOps lays a merge over a tree as the server applies it, and passes one it would refuse", () => {
  const { tree, pos } = served([N("a", "one", [N("low", "low")]), N("b", "- two", [N("k1", "kid1"), N("k2", "kid2")]), N("z", "z")]);
  const out = applyOps(tree, [{ op: "merge", id: "b", into: "a", marker: "- " }], PAGE, pos);
  assert.deepEqual(shape(out).map((n) => n.id), ["a", "z"]);
  assert.equal(out[0].content, "onetwo");
  assert.deepEqual(out[0].children.map((n) => n.id), ["low", "k1", "k2"]);

  const nested = served([N("p", "one", [N("x", "x"), N("b", "two", [N("k", "kid")]), N("y", "y")])]);
  const spliced = applyOps(nested.tree, [{ op: "merge", id: "b", into: "p" }], PAGE, nested.pos);
  assert.equal(spliced[0].content, "onetwo");
  assert.deepEqual(spliced[0].children.map((n) => n.id), ["x", "k", "y"]);

  assert.equal(applyOps(tree, [{ op: "merge", id: "gone", into: "a" }], PAGE, pos), tree);
  assert.equal(applyOps(tree, [{ op: "merge", id: "b", into: "gone" }], PAGE, pos), tree);
  assert.equal(applyOps(tree, [{ op: "merge", id: "a", into: "low" }], PAGE, pos), tree, "into itself");
});

test("pushOp keeps a set behind a queued merge into its block", () => {
  const q = [{ op: "set", id: "a", content: "one!", base: "one" }, { op: "merge", id: "b", into: "a" }];
  pushOp(q, { op: "set", id: "a", content: "one!two?", base: "one!two" });
  assert.equal(q.length, 3, "folded forward, the set would drop what the merge joins to it");
  pushOp(q, { op: "set", id: "a", content: "one!two?!", base: "one!two?" });
  assert.equal(q.length, 3);
  assert.equal(q[2].base, "one!two");
});

test("a remote move displaces the open editor's row when it moves it, an ancestor, or reorders their siblings", () => {
  // top · parent[ child (editing), sibling ] · other
  const tree = [N("top"), N("parent", "", [N("child"), N("sibling")]), N("other")];
  const moved = (op) => displacedRow(tree, [op], "child", PAGE);
  assert.equal(moved({ op: "move", id: "parent", parent: "top", position: "a0" }), true, "an ancestor indented");
  assert.equal(moved({ op: "move", id: "child", parent: PAGE, position: "a9" }), true, "the block outdented");
  assert.equal(moved({ op: "move", id: "sibling", parent: "parent", position: "Zz" }), true, "a sibling reordered");
  assert.equal(moved({ op: "move", id: "other", parent: PAGE, position: "Zz" }), true, "the ancestor's siblings reordered");
  assert.equal(moved({ op: "move", id: "other", parent: "top", position: "a0" }), false, "a block moved elsewhere");
  assert.equal(moved({ op: "insert", id: "new", parent: "parent", position: "a0", content: "", props: {} }), false, "a new block");
  assert.equal(displacedRow(tree, [{ op: "set", id: "child", content: "x" }], "child", PAGE), false);
  assert.equal(displacedRow(tree, [{ op: "move", id: "parent", parent: "top" }], null, PAGE), false, "no editor open");
});

// The positions the ops carry are minted here and checked by the server
// (gamma/ops.py order_key_error) with another library, fractional_indexing
// on PyPI: tests/shared/fractionalkeys.json holds the two to the same keys.
const KEYS = JSON.parse(readFileSync(new URL("../../tests/shared/fractionalkeys.json", import.meta.url), "utf8"));

test("sibling keys are the server's library's, and the keys it refuses are refused here (the shared cases)", () => {
  for (const c of KEYS.between) {
    assert.deepEqual(generateNKeysBetween(c.a, c.b, c.n), c.keys, c.note);
    if (c.n === 1) assert.equal(generateKeyBetween(c.a, c.b), c.keys[0], c.note);
  }
  for (const c of KEYS.invalid) assert.throws(() => generateKeyBetween(c.key, null), undefined, c.note);
  // The library takes a key outside the base-62 alphabet, which the server
  // refuses: it must never mint one.
  const base62 = /^[0-9A-Za-z]+$/;
  for (const c of KEYS.between) for (const k of c.keys) assert.match(k, base62, c.note);
  for (const c of KEYS.foreign) assert.doesNotMatch(c.key, base62, c.note);
});
