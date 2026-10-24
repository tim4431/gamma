// updateBlockTree copies only the path to the edited block: every other
// subtree keeps its identity (a memoized row on it does not re-render on a
// keystroke elsewhere), and a miss hands the same array back.
import { test } from "node:test";
import assert from "node:assert/strict";
import { countBlocks, sameShape, setBlockText, updateBlockTree } from "../src/shared/model/blockModel.js";

function tree() {
  return [
    { id: "a", content: "a", properties: {}, children: [
      { id: "a1", content: "a1", properties: {}, children: [] },
      { id: "a2", content: "a2", properties: {}, children: [
        { id: "a2x", content: "deep", properties: {}, children: [] },
      ] },
    ] },
    { id: "b", content: "b", properties: {}, children: [
      { id: "b1", content: "b1", properties: {}, children: [] },
    ] },
  ];
}

test("only the path to the edited block is copied", () => {
  const before = tree();
  const after = updateBlockTree(before, "a2x", (b) => ({ ...b, content: "changed" }));
  assert.notEqual(after, before);
  assert.equal(after[1], before[1], "the other top-level subtree keeps its identity");
  assert.notEqual(after[0], before[0]);
  assert.equal(after[0].children[0], before[0].children[0], "an untouched sibling keeps its identity");
  assert.notEqual(after[0].children[1], before[0].children[1]);
  assert.equal(after[0].children[1].children[0].content, "changed");
  assert.equal(before[0].children[1].children[0].content, "deep", "the old tree is untouched");
});

test("a block not in the tree hands the same array back", () => {
  const before = tree();
  assert.equal(updateBlockTree(before, "nope", (b) => ({ ...b, content: "x" })), before);
  const empty = [];
  assert.equal(updateBlockTree(empty, "a", (b) => b), empty);
});

test("the updater gets the stored block, with its children as they are", () => {
  const before = tree();
  let seen;
  const after = updateBlockTree(before, "a", (b) => { seen = b; return { ...b, children: [...b.children, { id: "a3", content: "", properties: {}, children: [] }] }; });
  assert.equal(seen, before[0]);
  assert.equal(after[0].children.length, 3);
  assert.equal(after[0].children[0], before[0].children[0]);
  assert.equal(before[0].children.length, 2);
});

test("setBlockText goes through the same path copy", () => {
  const before = tree();
  const after = setBlockText(before, "b1", "typed");
  assert.equal(after[0], before[0]);
  assert.equal(after[1].children[0].content, "typed");
});

test("sameShape: the same ids in the same places, whatever their text", () => {
  const tree = [{ id: "a", content: "A", children: [{ id: "a1", content: "", children: [] }] }, { id: "b", content: "B", children: [] }];
  assert.equal(sameShape(tree, setBlockText(tree, "a1", "typed")), true, "a keystroke");
  assert.equal(sameShape(tree, updateBlockTree(tree, "b", (b) => ({ ...b, properties: { collapsed: true } }))), true, "a fold");
  assert.equal(sameShape(tree, [tree[1], tree[0]]), false, "a move");
  assert.equal(sameShape(tree, [...tree, { id: "c", children: [] }]), false, "an insert");
  assert.equal(sameShape(tree, updateBlockTree(tree, "a", (a) => ({ ...a, children: [] }))), false, "a child removed");
  assert.equal(sameShape(tree, [{ ...tree[0], children: [{ ...tree[0].children[0], children: [{ id: "x" }] }] }, tree[1]]), false, "a grandchild added");
  assert.equal(sameShape([{ id: "a" }], [{ id: "a", children: [] }]), true, "no children and an empty list are the same");
});

test("countBlocks: every block under a list, or past a cap one more than it", () => {
  const tree = [{ id: "a", children: [{ id: "a1" }, { id: "a2", children: [{ id: "x" }] }] }, { id: "b" }];
  assert.equal(countBlocks(tree), 5);
  assert.equal(countBlocks(tree, 2), 3);
  assert.equal(countBlocks([]), 0);
  assert.equal(countBlocks(undefined), 0);
});
