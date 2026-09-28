// The undo stack's classifier: what a tree transition counts as. null —
// nothing undoable (a load, a fold's stored default); true — a structural
// or property edit; a block id — only that block's content changed (the
// candidate for merging with the previous keystroke).
//
// Then the collaborative part: another client's batches folded into the
// entries (rebaseHistory, text carried over by rebaseText) and an undo step
// (undoStep) that never takes back what someone else changed since.
import assert from "node:assert/strict";
import { test } from "node:test";
import { applyOps, seedPositions } from "../src/shared/model/blockOps.js";
import {
  classifyTransition, createHistory, describeTransition, observeTree, rebaseHistory, rebaseText, undoStep,
} from "../src/editor/blockHistory.js";

const N = (id, content = id, children = [], extra = {}) => ({ id, content, properties: {}, children, ...extra });
const tree = () => [N("a", "a", [N("a1")]), N("b")];

test("undo descriptions name creations, deletions, text, moves, and properties", () => {
  assert.equal(describeTransition([], [N("a", "New note")]), "note creation: “New note”");
  assert.equal(describeTransition([N("a", "Old note")], []), "note deletion: “Old note”");
  assert.equal(describeTransition([N("a", "before")], [N("a", "after")]), "note text edit: “after”");
  assert.equal(describeTransition(tree(), tree().reverse()), "note move");
  assert.equal(describeTransition([N("a")], [N("a", "a", [], { properties: { color: "red" } })]), "note properties change");
  const h = N("h", "quote", [], { properties: { highlight_id: "h", color: "red" } });
  assert.equal(describeTransition([h], [{ ...h, properties: { ...h.properties, color: "blue" } }]), "highlight color change");
  assert.equal(describeTransition([N("a"), N("b")], []), "deletion of 2 notes");
});

test("identity and a fold's stored default are not edits", () => {
  const t = tree();
  assert.equal(classifyTransition(t, t), null);
  assert.equal(classifyTransition(t, [{ ...t[0], properties: { collapsed: true } }, t[1]]), null);
});

test("one block's content names that block, nested or not", () => {
  const t = tree();
  assert.equal(classifyTransition(t, [t[0], { ...t[1], content: "b2" }]), "b");
  assert.equal(classifyTransition(t, [{ ...t[0], children: [{ ...t[0].children[0], content: "typed" }] }, t[1]]), "a1");
});

test("anything structural, a property, or two blocks' content is a plain edit", () => {
  const t = tree();
  assert.equal(classifyTransition(t, [t[0]]), true, "a block removed");
  assert.equal(classifyTransition(t, [t[1], t[0]]), true, "reordered");
  assert.equal(classifyTransition(t, [{ ...t[0], properties: { color: "red" } }, t[1]]), true, "a property");
  assert.equal(classifyTransition(t, [{ ...t[0], content: "x" }, { ...t[1], content: "y" }]), true, "two contents");
  assert.equal(classifyTransition(t, [{ ...t[0], content: "x", properties: { k: 1 } }, t[1]]), true, "content plus a property");
  assert.equal(classifyTransition(t, [{ ...t[0], children: [] }, t[1]]), true, "a child removed");
});

test("property comparison is by value and ignores the collapsed flag", () => {
  const t = [N("a", "a", [], { properties: { link: { url: "u" }, collapsed: false } })];
  assert.equal(classifyTransition(t, [{ ...t[0], properties: { link: { url: "u" }, collapsed: true } }]), null);
  assert.equal(classifyTransition(t, [{ ...t[0], properties: { link: { url: "v" } } }]), true);
});

// --- collaborative undo ------------------------------------------------------------

test("another client's text change is carried over onto an older text, unless the two overlap", () => {
  // from → to is theirs; the third argument is the snapshot's text (ours differs from `from`)
  assert.equal(rebaseText("block x alice1", "bob1 block x alice1", "block x"), "bob1 block x");
  assert.equal(rebaseText("alice made this", "alice made this and bob", ""), " and bob");
  assert.equal(rebaseText("one two", "one two three", "one"), "one three");
  assert.equal(rebaseText("same", "changed", "same"), "changed");
  assert.equal(rebaseText("The dog sat", "The hog sat", "The cat sat"), null, "both changed the same word");
});

const PAGE = "pg";
// A page's history as App drives it: our edits observed, another client's
// batches applied to the tree and folded into the entries, undo steps.
function page(initial) {
  const origin = new WeakMap();
  const pos = seedPositions(initial, new Map());
  const p = { tree: initial, s: createHistory(initial) };
  p.o = { originOf: (tr) => origin.get(tr), setBlocks: (tr) => { p.tree = tr; observeTree(p.s, tr, p.o); } };
  p.edit = (next) => { p.tree = next; observeTree(p.s, next, p.o); };
  p.remote = (ops) => {
    const before = p.tree;
    const next = applyOps(before, ops, PAGE, pos);
    origin.set(next, "remote");
    rebaseHistory(p.s, ops, { pageId: PAGE, pos, before });
    p.tree = next;
    observeTree(p.s, next, p.o);
  };
  p.undo = (redo = false) => undoStep(p.s, p.o, redo);
  p.texts = () => p.tree.map((n) => n.content);
  return p;
}

test("undo takes out only our typing in a block someone else wrote in, and never deletes their text", () => {
  const p = page([N("e", "existing block")]);
  p.edit([...p.tree, N("x", "")]); // alice makes a block …
  p.edit([p.tree[0], { ...p.tree[1], content: "alice made this" }]); // … and types in it
  p.remote([{ op: "set", id: "x", content: "alice made this and bob wrote a paragraph here" }]);
  const first = p.undo();
  assert.equal(first.kept, false);
  assert.match(first.description, /^note text edit/);
  assert.deepEqual(p.texts(), ["existing block", " and bob wrote a paragraph here"]);
  assert.deepEqual(p.undo(), { blocked: true }, "the block holds bob's text: it is not deleted");
  assert.deepEqual(p.texts(), ["existing block", " and bob wrote a paragraph here"]);
  assert.equal(p.undo(), false);
  p.undo(true); // redo brings alice's text back, bob's still there
  assert.deepEqual(p.texts(), ["existing block", "alice made this and bob wrote a paragraph here"]);
});

test("undo of an edit at one end of a block keeps what someone typed at the other", () => {
  const p = page([N("x", "block x")]);
  p.edit([{ ...p.tree[0], content: "block x alice1" }]);
  p.remote([{ op: "set", id: "x", content: "bob1 block x alice1" }]);
  assert.match(p.undo().description, /^note text edit/);
  assert.deepEqual(p.texts(), ["bob1 block x"]);
});

test("text of theirs an entry can't be rebased around stays; the rest of the entry applies", () => {
  const p = page([N("x", "The cat sat"), N("y", "y")]);
  p.edit([{ ...p.tree[0], content: "The dog sat" }, { ...p.tree[1], content: "y edited" }]); // one step, two blocks
  p.remote([{ op: "set", id: "x", content: "The hog sat" }]);
  const step = p.undo();
  assert.equal(step.kept, true);
  assert.deepEqual(p.texts(), ["The hog sat", "y"]);
});

test("an entry someone else's change emptied is not reported as undone: the step says so", () => {
  const p = page([N("h", "quote", [], { properties: { color: "yellow" } })]);
  p.edit([{ ...p.tree[0], properties: { color: "red" } }]);
  p.remote([{ op: "set", id: "h", props: { color: "blue" } }]);
  assert.deepEqual(p.undo(), { blocked: true });
  assert.equal(p.tree[0].properties.color, "blue");
});

test("an entry that changes nothing any more is passed over, never reported as undone", () => {
  const p = page([N("a"), N("b")]);
  p.edit([{ ...p.tree[0], content: "a1" }, p.tree[1]]);
  p.edit([p.tree[0], { ...p.tree[1], content: "b1" }]);
  p.edit([p.tree[0], { ...p.tree[1], content: "b" }]); // typed and taken back within one chunk
  assert.deepEqual(p.undo(), { description: "note text edit: “a1”", kept: false });
  assert.deepEqual(p.texts(), ["a", "b"]);
});

test("undoing our new block keeps it when someone else wrote a note under it", () => {
  const p = page([N("a")]);
  p.edit([...p.tree, N("p", "mine")]);
  p.remote([{ op: "insert", id: "q", parent: "p", position: "a0", content: "theirs", props: {} }]);
  assert.deepEqual(p.undo(), { blocked: true });
  assert.deepEqual(p.tree.map((n) => [n.id, n.children.map((c) => c.content)]), [["a", []], ["p", ["theirs"]]]);
});
