// updateBlockTree copies only the path to the edited block: every other
// subtree keeps its identity (a memoized row on it does not re-render on a
// keystroke elsewhere), and a miss hands the same array back.
import { test } from "node:test";
import assert from "node:assert/strict";
import { setBlockText, updateBlockTree } from "../src/shared/model/blockModel.js";

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
