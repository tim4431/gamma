// A selection of whole notes (editor/blockSelection.js) and the tree edits
// it runs (blockModel's removeBlocks, indentBlocks, outdentBlocks,
// moveSiblings, insertBlocks, freshCopy): ranges follow the rows shown,
// the actions work on the roots, and every edit keeps the blocks' order.
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  NO_SELECTION, pruneSelection, selectAll, selectOne, selectRange, selectionRoots, stepSelection, subtreeMarkdown, toggleSelected,
} from "../src/editor/blockSelection.js";
import {
  EMPTY_VIEW, freshCopy, indentBlocks, insertBlocks, moveSiblings, outdentBlocks, removeBlocks,
} from "../src/shared/model/blockModel.js";

const b = (id, children = [], properties = {}) => ({ id, content: id, properties, children });
function tree() {
  return [
    b("a", [b("a1"), b("a2", [b("a2x")])]),
    b("b"),
    b("c", [b("c1")]),
    b("d"),
  ];
}
const ids = (list) => list.map((x) => x.id);
const shape = (list) => list.map((x) => (x.children?.length ? { [x.id]: shape(x.children) } : x.id));
const folded = (id) => ({ ...EMPTY_VIEW, folds: { [id]: true } });

test("a range is the rows shown between its two ends, either way round", () => {
  assert.deepEqual([...selectRange(tree(), EMPTY_VIEW, "a2", "c").ids], ["a2", "a2x", "b", "c"]);
  const up = selectRange(tree(), EMPTY_VIEW, "c", "a2");
  assert.deepEqual([...up.ids], ["a2", "a2x", "b", "c"]);
  assert.equal(up.anchor, "c");
  assert.equal(up.head, "a2");
  assert.deepEqual([...selectRange(tree(), folded("a"), "a", "b").ids], ["a", "b"], "folded children are not shown, so not picked");
  assert.deepEqual([...selectRange(tree(), folded("a"), "a1", "b").ids], ["b"], "an end that is not shown: the other alone");
});

test("the actions work on the roots, in document order", () => {
  assert.deepEqual(selectionRoots(tree(), new Set(["c", "a2x", "a", "c1"])), ["a", "c"]);
  assert.deepEqual(selectionRoots(tree(), new Set(["d", "a2"])), ["a2", "d"]);
  assert.deepEqual(selectionRoots(tree(), new Set()), []);
});

test("toggling and the arrows", () => {
  const two = toggleSelected(selectOne("b"), "d");
  assert.deepEqual([...two.ids], ["b", "d"]);
  assert.equal(toggleSelected(selectOne("b"), "b"), NO_SELECTION);
  const range = selectRange(tree(), EMPTY_VIEW, "b", "c");
  assert.deepEqual([...stepSelection(tree(), EMPTY_VIEW, range, -1, false).ids], ["a2x"], "↑ from the top one");
  assert.deepEqual([...stepSelection(tree(), EMPTY_VIEW, range, 1, false).ids], ["c1"], "↓ from the bottom one");
  assert.deepEqual([...stepSelection(tree(), EMPTY_VIEW, range, 1, true).ids], ["b", "c", "c1"], "Shift+↓ grows from the head");
  assert.deepEqual([...stepSelection(tree(), EMPTY_VIEW, range, -1, true).ids], ["b"], "Shift+↑ shrinks back to the anchor");
  const top = selectOne("a");
  assert.equal(stepSelection(tree(), EMPTY_VIEW, top, -1, false), top, "at the top it stays");
  assert.deepEqual([...selectAll(tree(), folded("c")).ids], ["a", "a1", "a2", "a2x", "b", "c", "d"]);
});

test("a selection loses the blocks the tree no longer has", () => {
  const sel = selectRange(tree(), EMPTY_VIEW, "a2", "c");
  assert.equal(pruneSelection(tree(), sel), sel, "nothing gone: the same selection");
  const left = pruneSelection(removeBlocks(tree(), ["b"]), sel);
  assert.deepEqual([...left.ids], ["a2", "a2x", "c"]);
  assert.equal(pruneSelection([b("z")], sel), NO_SELECTION);
});

test("removing several blocks copies only the paths to them", () => {
  const before = tree();
  const after = removeBlocks(before, ["a2", "c"]);
  assert.deepEqual(shape(after), [{ a: ["a1"] }, "b", "d"]);
  assert.equal(after[1], before[1], "an untouched block keeps its identity");
  assert.equal(after[0].children[0], before[0].children[0]);
  assert.equal(removeBlocks(before, ["zz"]), before, "none there: the same tree");
});

test("indenting a run puts it under the sibling above, in order; a first sibling stops it", () => {
  assert.deepEqual(shape(indentBlocks(tree(), ["b", "c"])), [{ a: ["a1", { a2: ["a2x"] }, "b", { c: ["c1"] }] }, "d"]);
  const t = tree();
  assert.equal(indentBlocks(t, ["a", "b"]), t, "the first block has nothing to go under");
  assert.deepEqual(shape(indentBlocks(tree(), ["b", "d"])), [{ a: ["a1", { a2: ["a2x"] }, "b"] }, { c: ["c1", "d"] }]);
});

test("outdenting keeps the order; a block at the top stays", () => {
  assert.deepEqual(shape(outdentBlocks(tree(), ["a1", "a2"])), ["a", "a1", { a2: ["a2x"] }, "b", { c: ["c1"] }, "d"]);
  assert.deepEqual(shape(outdentBlocks(tree(), ["b", "c1"])), [{ a: ["a1", { a2: ["a2x"] }] }, "b", "c", "c1", "d"]);
});

test("a run of siblings moves up and down as one", () => {
  assert.deepEqual(ids(moveSiblings(tree(), ["b", "c"], -1)), ["b", "c", "a", "d"]);
  assert.deepEqual(ids(moveSiblings(tree(), ["b", "c"], 1)), ["a", "d", "b", "c"]);
  assert.deepEqual(shape(moveSiblings(tree(), ["a2"], -1))[0], { a: [{ a2: ["a2x"] }, "a1"] });
  const t = tree();
  assert.equal(moveSiblings(t, ["a", "b"], -1), t, "at the top: nowhere to go");
  assert.equal(moveSiblings(t, ["a", "c"], 1), t, "no one run");
});

test("blocks go in one after another where the drop says", () => {
  const rest = removeBlocks(tree(), ["b", "d"]);
  const moved = [b("b"), b("d")];
  assert.deepEqual(shape(insertBlocks(rest, { type: "sibling", id: "a", above: true }, moved)), ["b", "d", { a: ["a1", { a2: ["a2x"] }] }, { c: ["c1"] }]);
  assert.deepEqual(shape(insertBlocks(rest, { type: "child", id: "c" }, moved)), [{ a: ["a1", { a2: ["a2x"] }] }, { c: ["b", "d", "c1"] }]);
});

test("a fresh copy has new ids all the way down and leaves the PDF anchoring behind", () => {
  const src = b("h", [b("h1")], { pdf_position: { page: 1 }, quote: "q", color: "yellow" });
  const copy = freshCopy(src);
  assert.notEqual(copy.id, "h");
  assert.notEqual(copy.children[0].id, "h1");
  assert.deepEqual(copy.properties, { quote: "q", color: "yellow" });
});

test("the selection copies as an outline that pastes back as blocks", () => {
  const [a] = tree();
  assert.equal(subtreeMarkdown(a, 0), "- a\n  - a1\n  - a2\n    - a2x");
});
