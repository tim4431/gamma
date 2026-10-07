// refLabelsByBlock resolves every row's [[ref]] and Gamma-link labels once
// per tree change, and a row's entry keeps its identity while its labels
// read the same (what a memoized row's props compare).
import { test } from "node:test";
import assert from "node:assert/strict";
import { NO_LABELS, refLabelsByBlock } from "../src/editor/refLabels.js";
import { updateBlockTree } from "../src/shared/model/blockModel.js";

const block = (id, content, children = []) => ({ id, content, properties: {}, children });
const byIdOf = (tree) => {
  const map = new Map();
  const walk = (list) => { for (const b of list) { map.set(b.id, { ...b, page_title: "Page" }); walk(b.children); } };
  walk(tree);
  return map;
};

test("rows with refs get their labels; rows without get no entry", () => {
  const tree = [block("a", "see [[b]] and [[zz]]"), block("b", "plain"), block("c", "", [block("c1", "[[a]]")])];
  const labels = refLabelsByBlock(tree, byIdOf(tree), { zz: { trashed: { id: "p" } } }, null);
  assert.deepEqual([...labels.keys()], ["a", "c1"]);
  assert.deepEqual(labels.get("a"), { b: { content: "plain", page_title: "Page" }, zz: { trashed: { id: "p" } } });
  assert.deepEqual(labels.get("c1"), { a: { content: "see [[b]] and [[zz]]", page_title: "Page" } });
  assert.equal(labels.get("b"), undefined);
  assert.equal(NO_LABELS.x, undefined);
});

test("an entry keeps its identity across a keystroke elsewhere, and changes with its target", () => {
  const tree = [block("a", "see [[b]]"), block("b", "plain"), block("c", "typing")];
  const first = refLabelsByBlock(tree, byIdOf(tree), {}, null);
  const typed = updateBlockTree(tree, "c", (b) => ({ ...b, content: "typing more" }));
  const second = refLabelsByBlock(typed, byIdOf(typed), {}, first);
  assert.equal(second.get("a"), first.get("a"), "the same object: the row does not re-render");
  const renamed = updateBlockTree(typed, "b", (b) => ({ ...b, content: "renamed" }));
  const third = refLabelsByBlock(renamed, byIdOf(renamed), {}, second);
  assert.notEqual(third.get("a"), second.get("a"));
  assert.equal(third.get("a").b.content, "renamed");
});

test("a Gamma link that resolves gets a label, one whose block is missing does not", () => {
  const tree = [block("a", "https://x.test/p/page1#block-b and https://x.test/p/page2#block-gone")];
  const byId = new Map([["b", { id: "b", content: "target", page_title: "P" }]]);
  const labels = refLabelsByBlock(tree, byId, { gone: { missing: true } }, null);
  const got = labels.get("a") || {};
  assert.equal(got.gone, undefined, "a missing link target stays an ordinary URL");
  if (got.b) assert.equal(got.b.content, "target");
});
