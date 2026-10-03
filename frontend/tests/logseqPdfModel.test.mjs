// The pure block-tree operations the outliner's keys run on.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  EMPTY_VIEW, addChildBlock, addHighlightAsBlock, addSiblingBlock, blockPage, blockPosition, blocksToHighlights,
  closeEditing, extractBlock, findBlockContext, flattenBlocks, indentBlock, insertChild, insertSibling, isDescendant,
  isFolded, isFoldedAway, isHighlightBlock, makeBlockId,
  normalizeBlocks, outdentBlock, revealBlock, toggleFold, visibleBlocks, visibleNeighbor, withEditing,
} from "../src/shared/model/blockModel.js";

const N = (id, children = [], extra = {}) => ({ id, content: id, properties: {}, children, ...extra });
const ids = (list) => list.map((b) => b.id);
// a(a1), b, c(c1(c1x), c2)
const tree = () => [N("a", [N("a1")]), N("b"), N("c", [N("c1", [N("c1x")]), N("c2")])];

test("indent moves a block under its previous sibling; the first sibling stays", () => {
  const t = tree();
  assert.equal(indentBlock(t, "a"), t);
  const out = indentBlock(t, "b");
  assert.deepEqual(ids(out), ["a", "c"]);
  assert.deepEqual(ids(out[0].children), ["a1", "b"]);
  assert.deepEqual(ids(t), ["a", "b", "c"], "the input is not mutated");
});

test("outdent puts a block right after its parent, at the root or inside the grandparent", () => {
  assert.deepEqual(ids(outdentBlock(tree(), "a1")), ["a", "a1", "b", "c"]);
  const out = outdentBlock(tree(), "c1x");
  assert.deepEqual(ids(out[2].children), ["c1", "c1x", "c2"]);
  assert.deepEqual(out[2].children[0].children, []);
  assert.equal(outdentBlock(tree(), "b").length, 3, "a root block has nowhere to go");
});

test("insertSibling / insertChild place a block; an unknown anchor returns the same list", () => {
  const t = tree();
  assert.deepEqual(ids(insertSibling(t, "b", N("n"), false)), ["a", "n", "b", "c"]);
  assert.deepEqual(ids(insertSibling(t, "b", N("n"), true)), ["a", "b", "n", "c"]);
  assert.deepEqual(ids(insertSibling(t, "c2", N("n"), true)[2].children), ["c1", "c2", "n"]);
  assert.equal(insertSibling(t, "nope", N("n"), true), t);
  assert.deepEqual(ids(insertChild(t, "b", N("n"))[1].children), ["n"]);
  assert.deepEqual(ids(insertChild(t, "c", N("n"), true)[2].children), ["c1", "c2", "n"]);
  assert.deepEqual(ids(insertChild(t, "c", N("n"))[2].children), ["n", "c1", "c2"]);
});

test("extractBlock returns the subtree and the tree without it, untouched elsewhere", () => {
  const t = tree();
  const { extracted, remaining } = extractBlock(t, "c1");
  assert.equal(extracted.id, "c1");
  assert.deepEqual(ids(extracted.children), ["c1x"]);
  assert.deepEqual(ids(remaining[2].children), ["c2"]);
  assert.equal(remaining[0], t[0], "untouched branches keep their identity");
  assert.equal(extractBlock(t, "nope"), null);
});

test("isDescendant and findBlockContext", () => {
  const t = tree();
  assert.equal(isDescendant(t, "c", "c1x"), true);
  assert.equal(isDescendant(t, "c", "c"), true);
  assert.equal(isDescendant(t, "a", "c2"), false);
  assert.deepEqual(findBlockContext(t, "c1x"), { block: t[2].children[0].children[0], parentId: "c1", index: 0, depth: 2, ancestors: ["c", "c1"] });
  assert.deepEqual(findBlockContext(t, "b").parentId, null);
  assert.equal(findBlockContext(t, "nope"), null);
});

test("new blocks: sibling below or above, child, with fresh 12-char ids and nothing but the document's fields", () => {
  const t = tree();
  let { blocks, newId } = addSiblingBlock(t, "b");
  assert.deepEqual(ids(blocks), ["a", "b", newId, "c"]);
  assert.match(newId, /^[A-Za-z0-9_-]{12}$/);
  assert.deepEqual(blocks[2], { id: newId, parentId: null, children: [], content: "", properties: {} });
  ({ blocks, newId } = addSiblingBlock(t, "b", { above: true }));
  assert.deepEqual(ids(blocks), ["a", newId, "b", "c"]);
  ({ blocks, newId } = addChildBlock(t, "b"));
  assert.deepEqual(ids(blocks[1].children), [newId]);
  assert.notEqual(makeBlockId(), makeBlockId());
});

// The viewer's state beside the tree: {editingId, folds}. Folding a block
// nobody touched here follows its stored properties.collapsed.
const folded = (id) => ({ properties: { collapsed: true } });

test("flattenBlocks lists every block with depth and parent; visibleBlocks skips folded subtrees", () => {
  const t = tree();
  t[2] = { ...t[2], ...folded("c") };
  assert.deepEqual(flattenBlocks(t).map((b) => [b.id, b.depth, b.parentId]),
    [["a", 0, null], ["a1", 1, "a"], ["b", 0, null], ["c", 0, null], ["c1", 1, "c"], ["c1x", 2, "c1"], ["c2", 1, "c"]]);
  assert.deepEqual(visibleBlocks(t, EMPTY_VIEW).map((b) => b.id), ["a", "a1", "b", "c"]);
  // the viewer's own fold wins over the stored one, both ways
  assert.deepEqual(visibleBlocks(t, { ...EMPTY_VIEW, folds: { c: false } }).map((b) => b.id), ["a", "a1", "b", "c", "c1", "c1x", "c2"]);
  assert.deepEqual(visibleBlocks(t, { ...EMPTY_VIEW, folds: { a: true } }).map((b) => b.id), ["a", "b", "c"]);
  assert.equal(visibleNeighbor(t, "c", 1, EMPTY_VIEW), null, "c is folded: its children are not shown");
  assert.equal(visibleNeighbor(t, "c", 1, { ...EMPTY_VIEW, folds: { c: false } })?.id, "c1");
});

test("revealBlock unfolds every ancestor that hides the target, in the view only", () => {
  const t = tree();
  t[2] = { ...t[2], ...folded("c"), children: [{ ...t[2].children[0], ...folded("c1") }, t[2].children[1]] };
  t[0] = { ...t[0], ...folded("a") };
  assert.equal(isFoldedAway(t, "c1x", EMPTY_VIEW), true);
  const view = revealBlock(t, EMPTY_VIEW, "c1x");
  assert.deepEqual(view.folds, { c: false, c1: false });
  assert.equal(isFoldedAway(t, "c1x", view), false);
  assert.equal(isFolded(t[0], view), true, "an ancestor of something else stays folded");
  assert.deepEqual(t[2].properties, { collapsed: true }, "the document is untouched");
  assert.equal(revealBlock(t, view, "c1x"), view, "nothing to open: the same view");
  assert.equal(revealBlock(t, EMPTY_VIEW, "b"), EMPTY_VIEW, "a root block is never hidden");
  assert.equal(isFoldedAway(t, "nope", EMPTY_VIEW), false);
});

test("toggleFold flips the viewer's folding and the stored property together", () => {
  const { blocks, view } = toggleFold(tree(), EMPTY_VIEW, "c");
  assert.equal(blocks[2].properties.collapsed, true);
  assert.deepEqual(view.folds, { c: true });
  assert.equal(isFolded(blocks[2], view), true);
  const again = toggleFold(blocks, view, "c");
  assert.equal(again.blocks[2].properties.collapsed, false);
  assert.deepEqual(again.view.folds, { c: false });
  // a block revealed here (stored folded, shown open) folds again on the next toggle
  const t = tree();
  t[2] = { ...t[2], ...folded("c") };
  const shown = revealBlock(t, EMPTY_VIEW, "c1");
  assert.equal(toggleFold(t, shown, "c").view.folds.c, true);
  assert.deepEqual(toggleFold(t, EMPTY_VIEW, "nope"), { blocks: t, view: EMPTY_VIEW });
});

test("the open editor: one at a time, and a stale blur never closes the new one", () => {
  const v = withEditing(EMPTY_VIEW, "a");
  assert.equal(v.editingId, "a");
  assert.equal(withEditing(v, "a"), v, "the same view when nothing changes");
  assert.equal(withEditing(v, "b").editingId, "b");
  assert.equal(closeEditing(withEditing(v, "b"), "a").editingId, "b");
  assert.equal(closeEditing(v, "a").editingId, null);
});

test("normalizeBlocks gives every node a children array and nothing else", () => {
  const out = normalizeBlocks([{ id: "x", content: "", properties: { collapsed: true }, children: [{ id: "y", content: "" }] }]);
  assert.deepEqual(out, [{ id: "x", content: "", properties: { collapsed: true }, children: [{ id: "y", content: "", children: [] }] }]);
});

const POS = { pageNumber: 2, width: 600, height: 800, boundingRect: { x1: 1, y1: 2, x2: 3, y2: 4 }, rects: [{ x1: 1, y1: 2, x2: 3, y2: 4 }] };

test("blocksToHighlights lists positioned highlight blocks, by block id, and whether they carry a note", () => {
  const hl = (id, content, children = []) => N(id, children, { content, properties: { quote: "q", color: "red", pdf_position: POS } });
  const out = blocksToHighlights([N("plain"), hl("h1", ""), hl("h2", "a comment"), hl("h3", "", [N("k", [], { content: " " }), N("m", [], { content: "note" })]),
    N("pageonly", [], { properties: { quote: "q", pdf_position: { pageNumber: 4 } } }),
    N("ink", [], { properties: { ink_url: "/api/uploads/a.ink", pdf_position: POS } })]);
  assert.deepEqual(out.map((h) => [h.id, h.hasNote]), [["h1", false], ["h2", true], ["h3", true]]);
  assert.deepEqual(out[0], { id: "h1", content: { text: "q" }, comment: { text: "" }, hasNote: false, color: "red", position: POS });
});

test("a highlight is a block with a position that is no ink group; its page is the position's", () => {
  const [hl, pageOnly, link, ink, box, note] = [
    { pdf_position: POS }, { pdf_position: { pageNumber: 4 } }, { pdf_position: POS, link_url: "https://x.test" },
    { ink_url: "", pdf_position: POS }, { text_box: { x: 1 }, pdf_page: 5 }, {},
  ].map((properties, i) => N(`b${i}`, [], { properties }));
  assert.deepEqual([hl, pageOnly, link, ink, box, note].map(isHighlightBlock), [true, true, true, false, false, false]);
  assert.deepEqual([hl, pageOnly, link, ink, box, note].map(blockPage), [2, 4, 2, 2, 5, null]);
  // the place on the page: a position with its rectangles, not its page alone
  assert.deepEqual([hl, pageOnly].map(blockPosition), [POS, null]);
});

test("a highlight made in the viewer is a block whose id is the highlight's, its position as given", () => {
  const [block] = addHighlightAsBlock([], { id: "hx", position: POS, content: { text: "q" }, comment: { text: "c" }, color: "red" });
  assert.deepEqual(block, { id: "hx", parentId: null, children: [], content: "c",
    properties: { color: "red", quote: "q", pdf_position: POS } });
});
