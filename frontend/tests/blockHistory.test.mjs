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
  boxChange, classifyTransition, createHistory, describeTransition, observeTree, rebaseHistory, rebaseText, undoStep,
} from "../src/editor/blockHistory.js";

const N = (id, content = id, children = [], extra = {}) => ({ id, content, properties: {}, children, ...extra });
const tree = () => [N("a", "a", [N("a1")]), N("b")];

test("undo descriptions name creations, deletions, text, moves, and properties", () => {
  assert.equal(describeTransition([], [N("a", "New note")]), "note creation: “New note”");
  assert.equal(describeTransition([N("a", "Old note")], []), "note deletion: “Old note”");
  assert.equal(describeTransition([N("a", "before")], [N("a", "after")]), "note text edit: “after”");
  assert.equal(describeTransition(tree(), tree().reverse()), "note move");
  assert.equal(describeTransition([N("a")], [N("a", "a", [], { properties: { color: "red" } })]), "note properties change");
  const h = N("h", "quote", [], { properties: { pdf_position: { pageNumber: 1 }, color: "red" } });
  assert.equal(describeTransition([h], [{ ...h, properties: { ...h.properties, color: "blue" } }]), "highlight color change");
  assert.equal(describeTransition([N("a"), N("b")], []), "deletion of 2 notes");
});

test("a text box's changes are named for what the user did to it", () => {
  const B = (box, content = "Some text", pdf_page = 1) => N("t", content, [], { properties: { text_box: box, pdf_page } });
  const box = { x: 10, y: 20, w: 80, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  assert.equal(describeTransition([], [B(box, "")]), "text box creation");
  assert.equal(describeTransition([N("a")], [N("a"), B(box)]), "text box creation: “Some text”", "a duplicate");
  assert.equal(describeTransition([B(box)], []), "text box deletion: “Some text”");
  // typing stores the size the box then measured at: still a text edit
  assert.equal(describeTransition([B(box)], [B({ ...box, w: 95.3, h: 38 }, "Other text")]), "text box text edit: “Other text”");
  assert.equal(describeTransition([B(box)], [{ ...B(box, "Other text"), properties: { text_box: box, pdf_page: 2 } }]),
    "note text and properties edit", "but not with another property of it");
  assert.equal(describeTransition([B(box), N("n")], [B({ ...box, w: 95.3 }, "Other text"), N("n", "n", [], { properties: { color: "red" } })]),
    "note text and properties edit", "nor with another block's");
  // a move, with the size it measured at after it
  assert.equal(describeTransition([B(box)], [B({ ...box, x: 40, w: 60, h: 40 })]), "text box move");
  assert.equal(describeTransition([B(box)], [B({ ...box, w: 150, auto: false, h: 12 })]), "text box resize");
  assert.equal(describeTransition([B(box)], [B({ ...box, size: 24, w: 160, h: 38 })]), "text box style change");
  assert.equal(describeTransition([B(box)], [B({ ...box, color: "#dc2626" })]), "text box style change");
  assert.equal(describeTransition([B(box)], [B({ ...box, bg: "#fff4b8" })]), "text box style change");
  // stored values are read as normalized: a default spelled out is no change of style
  assert.equal(describeTransition([B({ ...box, bg: undefined })], [B({ ...box, bg: null, w: 90 })]), "text box resize");
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
  // an edit that belongs to the one before it (App's foldBlocks); the mark
  // lasts one transition, as App's autosave drops it (a redo back to the
  // same tree is an edit)
  p.fold = (next) => { origin.set(next, "fold"); p.edit(next); origin.delete(next); };
  p.remote = (ops) => {
    const before = p.tree;
    const next = applyOps(before, ops, PAGE, pos);
    origin.set(next, "remote");
    rebaseHistory(p.s, ops, { pageId: PAGE, pos, before });
    p.tree = next;
    observeTree(p.s, next, p.o);
    origin.delete(next); // one transition's worth, as App's autosave drops it
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

test("a folded write is part of the edit before it: one undo takes both back, one redo brings both", () => {
  // a text box made, then its measured size stored (useTextBoxes)
  const p = page([N("a")]);
  p.edit([...p.tree, N("b", "", [], { properties: { text_box: { w: 24 } } })]);
  p.fold([p.tree[0], { ...p.tree[1], properties: { text_box: { w: 60 } } }]);
  assert.equal(p.undo().description, "text box creation");
  assert.deepEqual(p.tree.map((n) => n.id), ["a"]);
  p.undo(true);
  assert.deepEqual(p.tree[1].properties.text_box, { w: 60 });
  assert.equal(p.undo(false).description, "text box creation", "still one step");
});

test("a block made and folded away again leaves nothing to undo, and the redo steps as they were", () => {
  // a text box made, restyled before any typing (useTextBoxes folds that
  // into its making), and left empty
  const p = page([N("a")]);
  p.edit([{ ...p.tree[0], content: "ab" }]);
  p.undo(); // a redo step
  p.edit([...p.tree, N("b", "", [], { properties: { text_box: { size: 12 } } })]);
  p.fold([p.tree[0], { ...p.tree[1], properties: { text_box: { size: 24 } } }]);
  p.fold([p.tree[0]]);
  assert.equal(p.s.undo.length, 0, "no entry left");
  assert.equal(p.undo(true).description, "note text edit: “ab”", "the redo step is still there");
  assert.deepEqual(p.texts(), ["ab"]);
  assert.equal(p.undo().description, "note text edit: “ab”");
  assert.equal(p.undo(), false);
});

test("a run of changes to one text box undoes as one step, as a run of typing does", () => {
  const box = { x: 10, y: 10, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  const B = (id, b) => N(id, id, [], { properties: { text_box: b, pdf_page: 1 } });
  const realNow = Date.now;
  let now = 1_000_000;
  Date.now = () => now;
  try {
    const p = page([N("a", "a"), B("t", box), B("u", box)]);
    p.edit([{ ...p.tree[0], content: "a typed earlier" }, p.tree[1], p.tree[2]]);
    const nudge = (id, dx) => {
      now += 30; // a held arrow key's repeat, a colour dragged in the picker
      p.edit(p.tree.map((n) => (n.id === id ? { ...n, properties: { ...n.properties,
        text_box: { ...n.properties.text_box, x: n.properties.text_box.x + dx } } } : n)));
    };
    for (let i = 0; i < 250; i++) nudge("t", 1);
    assert.equal(p.s.undo.length, 2, "the note's edit and one run of nudges");
    nudge("u", 1); // another box: a step of its own
    now += 1500;
    nudge("u", 1); // after a pause: another
    assert.equal(p.s.undo.length, 4);
    assert.equal(p.undo().description, "text box move");
    assert.equal(p.undo().description, "text box move");
    assert.equal(p.tree[2].properties.text_box.x, 10);
    assert.equal(p.undo().description, "text box move");
    assert.equal(p.tree[1].properties.text_box.x, 10, "the whole run undone at once");
    assert.equal(p.tree[0].content, "a typed earlier", "and nothing before it");
  } finally {
    Date.now = realNow;
  }
  const t0 = [N("t", "x", [], { properties: { text_box: box, color: "red" } })];
  assert.equal(boxChange(t0, [{ ...t0[0], properties: { text_box: { ...box, x: 1 }, color: "red" } }]), "t");
  assert.equal(boxChange(t0, [{ ...t0[0], properties: { text_box: box, color: "blue" } }]), null, "another property");
  assert.equal(boxChange(t0, [{ ...t0[0], content: "y", properties: { text_box: { ...box, x: 1 }, color: "red" } }]), null, "its text");
});

test("a text box is rebased key by key: their measured width never blocks undoing our move, nor comes undone with it", () => {
  const box = { x: 10, y: 10, w: 60, h: 23, auto: true, size: 12, color: "#1f1f1f", bg: null };
  const B = (b, content = "Hello") => N("t", content, [], { properties: { text_box: b, pdf_page: 1 } });
  const p = page([B(box)]);
  p.edit([B({ ...box, x: 200 })]); // we move the box
  // they type in it, from the box as it stands now (the server merged their keystroke's size into our move)
  p.remote([{ op: "set", id: "t", content: "Hello world", base: "Hello", props: { text_box: { ...box, x: 200, w: 101.5 } } }]);
  const undone = p.undo();
  assert.equal(undone.description, "text box move");
  assert.equal(undone.kept, false);
  assert.deepEqual(p.tree[0].properties.text_box, { ...box, w: 101.5 }, "our move undone, their width kept");
  assert.equal(p.tree[0].content, "Hello world");
  p.undo(true);
  assert.deepEqual(p.tree[0].properties.text_box, { ...box, x: 200, w: 101.5 });
  // their restyle the same
  p.remote([{ op: "set", id: "t", props: { text_box: { ...box, x: 200, w: 101.5, size: 24, h: 38 } } }]);
  p.undo();
  assert.deepEqual(p.tree[0].properties.text_box, { ...box, w: 101.5, size: 24, h: 38 });
});

test("undoing our new block keeps it when someone else wrote a note under it", () => {
  const p = page([N("a")]);
  p.edit([...p.tree, N("p", "mine")]);
  p.remote([{ op: "insert", id: "q", parent: "p", position: "a0", content: "theirs", props: {} }]);
  assert.deepEqual(p.undo(), { blocked: true });
  assert.deepEqual(p.tree.map((n) => [n.id, n.children.map((c) => c.content)]), [["a", []], ["p", ["theirs"]]]);
});
