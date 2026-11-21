// refLabelsByBlock resolves every row's [[ref]] and Gamma-link labels once
// per tree change, and a row's entry keeps its identity while its labels
// read the same (what a memoized row's props compare).
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CHIP_RE, NO_LABELS, REF_RE, refLabelsByBlock, refTargetTitle, refWords, unlinkedText,
} from "../src/editor/refLabels.js";
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

test("[[id|words]] is a ref with its words, \\| a table's pipe; an embed's words are none", () => {
  const refs = (text) => [...text.matchAll(CHIP_RE)].map((m) => [m[0][0] === "!", m[1], refWords(m[2])]);
  assert.deepEqual(refs("see [[b|the plan]], [[b\\|cell]], [[b| ]] and ![[b|x]]"),
    [[false, "b", "the plan"], [false, "b", "cell"], [false, "b", ""], [true, "b", "x"]]);
  assert.deepEqual([..."[[a|b]c]] [[a|b\nc]] [[Some title|x]]".matchAll(REF_RE)], [], "no bracket or line break in the words, an id first");
  const tree = [block("a", "see [[b|the plan]]"), block("b", "plain")];
  assert.deepEqual(refLabelsByBlock(tree, byIdOf(tree), {}, null).get("a"), { b: { content: "plain", page_title: "Page" } },
    "its target's label loads all the same (the chip's title)");
});

test("a chip with words is titled by what it names; an unlinked [[title|words]] reads as its words", () => {
  assert.equal(refTargetTitle({ content: "Plan\nmore", page_title: "Notes" }), "Plan · From: Notes");
  assert.equal(refTargetTitle({ content: "Notes", page_title: "Notes" }), "Notes", "a page names itself once");
  assert.equal(refTargetTitle({ content: "" }), undefined);
  assert.equal(unlinkedText("Some title|my words"), "my words");
  assert.equal(unlinkedText("Some title\\|cell"), "cell");
  assert.equal(unlinkedText("Some title| "), "Some title");
  assert.equal(unlinkedText("Some title"), "Some title");
});

test("a Gamma link that resolves gets a label, one whose block is missing does not", () => {
  const tree = [block("a", "https://x.test/?block=b and https://x.test/?block=gone")];
  const byId = new Map([["b", { id: "b", content: "target", page_title: "P" }]]);
  const labels = refLabelsByBlock(tree, byId, { gone: { missing: true } }, null);
  const got = labels.get("a");
  assert.deepEqual(got, { b: { content: "target", page_title: "P" } }, "a missing link target stays an ordinary URL");
});
