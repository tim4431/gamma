// The pure source-range algebra behind moving a rendered object (an image,
// a table, a diagram) around the notes: mdObjects.js. Objects are given as
// {from, to} ranges — what MdObject.jsx's scanners produce.
import test from "node:test";
import assert from "node:assert/strict";
import { cutObject, insertObject, moveObject, moveObjectInTree } from "../src/editor/mdObjects.js";

const TABLE = "| a | b |\n|---|---|\n| 1 | 2 |";
const IMG = "![|20](/api/uploads/x.png)";
const rangeOf = (content, needle) => ({ from: content.indexOf(needle), to: content.indexOf(needle) + needle.length });

test("cutObject: a whole-line object takes its lines, the gap around it closes to the wider side", () => {
  const c = `intro\n\n${TABLE}\n\nafter`;
  const cut = cutObject(c, rangeOf(c, TABLE));
  assert.equal(cut.md, TABLE);
  assert.equal(cut.content, "intro\n\nafter");
  // Single line breaks (one paragraph with breaks) stay single.
  const d = `a\n${IMG}\nb`;
  assert.equal(cutObject(d, rangeOf(d, IMG)).content, "a\nb");
  // First / last: nothing left dangling.
  const e = `${TABLE}\n\nafter`;
  assert.equal(cutObject(e, rangeOf(e, TABLE)).content, "after");
  const f = `intro\n\n${TABLE}`;
  assert.equal(cutObject(f, rangeOf(f, TABLE)).content, "intro");
  assert.equal(cutObject(TABLE, rangeOf(TABLE, TABLE)).content, "");
});

test("cutObject: an image inside a text line leaves the text; a list item that was only the picture goes", () => {
  const c = `see ${IMG} here\n\nmore`;
  const cut = cutObject(c, rangeOf(c, IMG));
  assert.equal(cut.md, IMG);
  assert.equal(cut.content, "see  here\n\nmore");
  const l = `- one\n- ${IMG}\n- three`;
  assert.equal(cutObject(l, rangeOf(l, IMG)).content, "- one\n- three");
});

test("cutObject: mapOffset follows the text after the cut and refuses the removed zone", () => {
  const c = `a\n\n${TABLE}\n\nb\n\nc`;
  const cut = cutObject(c, rangeOf(c, TABLE));
  const cStart = c.indexOf("c");
  assert.equal(cut.content.slice(cut.mapOffset(cStart)), "c");
  assert.equal(cut.mapOffset(0), 0);
  assert.equal(cut.mapOffset(c.indexOf(TABLE)), null);
});

test("insertObject: a paragraph of its own at a line start or at the end, indentation kept", () => {
  assert.equal(insertObject("a\n\nb", 3, TABLE), `a\n\n${TABLE}\n\nb`);
  assert.equal(insertObject("a\nb", 2, TABLE), `a\n\n${TABLE}\n\nb`);
  assert.equal(insertObject("a\n\nb", 0, IMG), `${IMG}\n\na\n\nb`);
  assert.equal(insertObject("a\n\nb", null, IMG), `a\n\nb\n\n${IMG}`);
  assert.equal(insertObject("a\n\nb\n\n", null, IMG), `a\n\nb\n\n${IMG}`);
  assert.equal(insertObject("", null, IMG), IMG);
  assert.equal(insertObject("- one\n  - two", 6, IMG), `- one\n\n${IMG}\n\n  - two`);
});

test("moveObject: within one block; onto itself is a no-op", () => {
  const c = `a\n\n${TABLE}\n\nb\n\nc`;
  const obj = rangeOf(c, TABLE);
  assert.equal(moveObject(c, obj, c.indexOf("c")), `a\n\nb\n\n${TABLE}\n\nc`);
  assert.equal(moveObject(c, obj, 0), `${TABLE}\n\na\n\nb\n\nc`);
  assert.equal(moveObject(c, obj, null), `a\n\nb\n\nc\n\n${TABLE}`);
  assert.equal(moveObject(c, obj, c.indexOf(TABLE)), null);
  // The blank line just above it counts as itself too.
  assert.equal(moveObject(c, obj, c.indexOf(TABLE) - 1), null);
});

const block = (id, content, children = []) => ({ id, content, properties: {}, collapsed: false, editMode: false, children });

test("moveObjectInTree: one tree edit for a cross-block move, a new block for a sibling drop", () => {
  const src = `intro\n\n${TABLE}\n\nafter`;
  const tree = [block("s", src), block("d", "para one\n\npara two", [block("k", "kid")])];
  const obj = rangeOf(src, TABLE);
  const inside = moveObjectInTree(tree, { sourceId: "s", obj, target: { type: "inside", id: "d", offset: "para one\n\n".length } });
  assert.equal(inside[0].content, "intro\n\nafter");
  assert.equal(inside[1].content, `para one\n\n${TABLE}\n\npara two`);
  assert.equal(tree[0].content, src, "the input tree is untouched");

  const sib = moveObjectInTree(tree, { sourceId: "s", obj, target: { type: "sibling", id: "d", above: true } });
  assert.deepEqual(sib.map((b) => b.content), ["intro\n\nafter", TABLE, "para one\n\npara two"]);
  assert.ok(sib[1].id && sib[1].id !== "s" && sib[1].id !== "d");

  const child = moveObjectInTree(tree, { sourceId: "s", obj, target: { type: "child", id: "d" } });
  assert.deepEqual(child[1].children.map((b) => b.content), [TABLE, "kid"], "a child drop lands first, like a block dropped under a row");

  const self = moveObjectInTree(tree, { sourceId: "s", obj, target: { type: "inside", id: "s", offset: src.indexOf(TABLE) } });
  assert.equal(self, null);
  assert.equal(moveObjectInTree(tree, { sourceId: "zz", obj, target: { type: "child", id: "d" } }), null);
});
